import hashlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth.models import Group, User
from django.core.management import call_command, CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Curriculum, Department, Discipline, LoadItem, PositionRule, Specialty, Teacher,
    TelegramLink, TelegramLinkToken, TelegramOutbox,
)
from core.tgbot.auth import _failed_attempts, create_link_code, link_account, unlink_telegram, user_roles
from core.tgbot.handlers.common import consent_yes
from core.tgbot.handlers.dean import _dashboard_text, _department_choice_rows
from core.tgbot.handlers.head import _assigned_items, _distribution_filter_choices, _undistributed, teachers_menu
from core.tgbot.keyboards import ActionCallback, ChoiceCallback, ReportCallback, main_menu
from core.tgbot.states import LinkForm
from core.tgbot.texts import button_text, display_hours, display_name, escape, privacy_safe_excel_table, safe_topic
from core.tgbot.urls import site_link


class TelegramAccountTests(TestCase):
    def setUp(self):
        _failed_attempts.clear()
        self.user = User.objects.create_user(username='teacher_tg', password='pass')
        self.department = Department.objects.create(name='Кафедра теста')
        self.position = PositionRule.objects.create(position='Тестовая должность')
        self.teacher = Teacher.objects.create(
            user=self.user, full_name='Иванов Иван Иванович',
            department=self.department, position=self.position,
        )
        self.user.groups.add(Group.objects.create(name='teacher'))

    def tearDown(self):
        _failed_attempts.clear()

    def test_link_code_is_hashed_short_lived_and_previous_unused_codes_are_invalidated(self):
        first = create_link_code(self.user)
        second = create_link_code(self.user)
        self.assertEqual(len(second), 9)
        self.assertTrue(set(second) <= set('ABCDEFGHJKLMNPQRSTUVWXYZ23456789'))
        self.assertNotEqual(first, second)
        tokens = list(TelegramLinkToken.objects.order_by('created_at'))
        self.assertEqual(tokens[0].token_hash, hashlib.sha256(first.encode('ascii')).hexdigest())
        self.assertIsNotNone(tokens[0].used_at)
        self.assertIsNone(tokens[1].used_at)
        self.assertLessEqual(tokens[1].expires_at - timezone.now(), timedelta(minutes=10))

    def test_consent_callback_links_the_clicking_telegram_user_and_private_chat(self):
        code = create_link_code(self.user)
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={'link_code': code}),
            get_state=AsyncMock(return_value=LinkForm.consent.state),
            clear=AsyncMock(),
        )
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=101, username='teacher_tg'),
            message=SimpleNamespace(
                from_user=SimpleNamespace(id=999, username='bot'),
                chat=SimpleNamespace(id=202),
                answer=AsyncMock(),
            ),
            answer=AsyncMock(),
        )

        async_to_sync(consent_yes)(callback, ChoiceCallback(action='consent_yes'), state)

        link = TelegramLink.objects.get(user=self.user)
        self.assertEqual((link.telegram_user_id, link.chat_id), (101, 202))
        self.assertNotEqual(link.telegram_user_id, callback.message.from_user.id)
        callback.message.answer.assert_awaited_once()
        state.clear.assert_awaited_once()

    @override_settings(TELEGRAM_BOT_TOKEN='123:token', TELEGRAM_BOT_USERNAME='tutorsys_test', USE_HTTPS=False)
    def test_non_teacher_account_can_create_telegram_link_code(self):
        user = User.objects.create_user(username='dean_tg', password='pass')
        user.groups.add(Group.objects.create(name='deputy_dean'))
        client = Client()
        client.force_login(user)

        response = client.post(reverse('core:telegram_account'), {'action': 'create_code'})

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context['generated_code'])
        self.assertTrue(TelegramLinkToken.objects.filter(user=user).exists())
        self.assertContains(response, 'Telegram')

    def test_stale_head_menu_is_refreshed_from_current_linked_account_roles(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=160), answer=AsyncMock())
        with patch(
            'core.tgbot.handlers.head.authorized_user',
            new=AsyncMock(return_value=(None, self.user, {'teacher'})),
        ):
            async_to_sync(teachers_menu)(message)

        message.answer.assert_awaited_once()
        self.assertIn('командой /whoami', message.answer.await_args.args[0])
        keyboard = message.answer.await_args.kwargs['reply_markup']
        labels = {button.text for row in keyboard.keyboard for button in row}
        self.assertIn('📖 Моя нагрузка', labels)
        self.assertNotIn('👥 Преподаватели', labels)

    def test_link_success_and_single_use(self):
        code = create_link_code(self.user)
        success, user_id = link_account(code, 101, 202, 'teacher_name', timezone.now())
        self.assertTrue(success)
        self.assertEqual(user_id, self.user.pk)
        link = TelegramLink.objects.get(user=self.user)
        self.assertEqual((link.telegram_user_id, link.chat_id, link.username), (101, 202, 'teacher_name'))
        self.assertIsNotNone(link.consent_at)
        self.assertEqual(link_account(code, 101, 202)[1], 'invalid')

    def test_expired_code_and_inactive_user_are_rejected(self):
        expired = create_link_code(self.user)
        TelegramLinkToken.objects.filter(user=self.user).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(link_account(expired, 103, 203)[1], 'invalid')
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        inactive_code = create_link_code(self.user)
        self.assertEqual(link_account(inactive_code, 104, 204)[1], 'invalid')
        self.assertIsNone(TelegramLink.objects.filter(telegram_user_id=103).first())

    def test_another_telegram_account_cannot_claim_user_already_linked(self):
        TelegramLink.objects.create(user=self.user, telegram_user_id=111, chat_id=211)
        code = create_link_code(self.user)
        self.assertEqual(link_account(code, 112, 212)[1], 'user_has_link')

    def test_telegram_account_cannot_be_linked_to_second_user(self):
        other = User.objects.create_user(username='other')
        code = create_link_code(other)
        TelegramLink.objects.create(user=self.user, telegram_user_id=120, chat_id=220)
        self.assertEqual(link_account(code, 120, 221)[1], 'already_linked')

    def test_failed_attempt_limit_is_five_per_telegram_account(self):
        for _ in range(5):
            self.assertEqual(link_account('wrong-code', 130, 230)[1], 'invalid')
        self.assertEqual(link_account('wrong-code', 130, 230)[1], 'limit')
        self.assertEqual(link_account('wrong-code', 131, 231)[1], 'invalid')

    def test_unlink_deletes_outbox_and_roles_are_read_from_database(self):
        link = TelegramLink.objects.create(user=self.user, telegram_user_id=140, chat_id=240)
        TelegramOutbox.objects.create(link=link, kind='test', text='test')
        self.assertEqual(user_roles(self.user), {'teacher'})
        self.assertTrue(unlink_telegram(140))
        self.assertFalse(TelegramLink.objects.filter(pk=link.pk).exists())
        self.assertFalse(TelegramOutbox.objects.exists())

    @override_settings(TELEGRAM_BOT_TOKEN='123:token', TELEGRAM_BOT_USERNAME='tutorsys_test', USE_HTTPS=False)
    def test_account_page_shows_generated_code_once_and_stores_only_hash(self):
        client = Client()
        client.force_login(self.user)
        response = client.post(reverse('core:telegram_account'), {'action': 'create_code'})
        self.assertEqual(response.status_code, 200)
        code = response.context['generated_code']
        self.assertIn(f'start=link_{code}', response.context['bot_url'])
        self.assertTrue(TelegramLinkToken.objects.filter(
            user=self.user, token_hash=hashlib.sha256(code.encode('ascii')).hexdigest()
        ).exists())
        self.assertNotIn(code, TelegramLinkToken.objects.get(user=self.user).token_hash)

    @override_settings(TELEGRAM_BOT_TOKEN='123:token', TELEGRAM_BOT_USERNAME='tutorsys_test', USE_HTTPS=True)
    def test_account_page_refuses_to_show_code_over_http_when_https_required(self):
        client = Client()
        client.force_login(self.user)
        response = client.post(reverse('core:telegram_account'), {'action': 'create_code'})
        self.assertIsNone(response.context['generated_code'])
        self.assertFalse(TelegramLinkToken.objects.filter(user=self.user).exists())

    @override_settings(TELEGRAM_BOT_TOKEN='')
    def test_bot_command_reports_missing_token_without_starting_telegram(self):
        with self.assertRaises(CommandError):
            call_command('run_telegram_bot')

    def test_privacy_helpers_escape_and_mask_data(self):
        with override_settings(TELEGRAM_PRIVACY_MODE='masked'):
            self.assertEqual(display_name(self.teacher, self.user), 'Иванов И.И.')
            self.assertEqual(safe_topic('Важная тема'), '')
            self.assertEqual(escape('<Дисциплина & тест>'), '&lt;Дисциплина &amp; тест&gt;')
            self.assertEqual(button_text(escape('<Дисциплина & тест>')), '<Дисциплина & тест>')
            self.assertEqual(len(button_text('x' * 100)), 64)
        with override_settings(TELEGRAM_PRIVACY_MODE='minimal'):
            self.assertEqual(display_name(self.teacher, self.user), 'Преподаватель')
            self.assertEqual(display_hours(12, self.user, self.teacher), 'скрыто')

    def test_callback_payloads_fit_telegram_limit_and_site_link_is_optional(self):
        payloads = [
            ActionCallback(action='distribution_item', item_id=123456, page=12).pack(),
            ChoiceCallback(action='dean_recalc_confirm', object_id=123456).pack(),
            ReportCallback(action='excel', teacher_id=123456, year=2025, page=12).pack(),
        ]
        self.assertTrue(all(len(payload.encode('utf-8')) <= 64 for payload in payloads))
        with override_settings(SITE_URL=''):
            self.assertIsNone(site_link('/report/'))
        with override_settings(SITE_URL='https://example.test/'):
            self.assertEqual(site_link('/report/'), 'https://example.test/report/')

    def test_privacy_safe_excel_redacts_names_topics_and_adjustment_reasons(self):
        table = (
            'report.xlsx', 'Факт', ['Месяц'], [['Январь']],
            [('Корректировки', ['Месяц', 'Преподаватель', 'Дисциплина', 'Вид', 'Часы', 'Тип', 'Причина'],
              [['Январь', 'Иванов Иван Иванович', 'Тема', 'Лекция', 1, 'Плановая', 'Секретная причина']])],
        )
        with override_settings(TELEGRAM_PRIVACY_MODE='masked'):
            masked = privacy_safe_excel_table(table)
        self.assertEqual(masked[4][0][2][0], ['Январь', 'Иванов И.И.', 'Тема', 'Лекция', 1, 'Плановая'])
        self.assertNotIn('Причина', masked[4][0][1])
        with override_settings(TELEGRAM_PRIVACY_MODE='minimal'):
            self.assertIsNone(privacy_safe_excel_table(table))

    def test_outbox_coalesces_recent_assignments_after_sixty_seconds_and_bounds_retries(self):
        from core.tgbot.outbox import _mark_error, _next_batch

        link = TelegramLink.objects.create(user=self.user, telegram_user_id=150, chat_id=250)
        first = TelegramOutbox.objects.create(
            link=link, kind='assignment', text='🧩 Назначена\nДисциплина А', dedupe_key='load-assignment:1'
        )
        second = TelegramOutbox.objects.create(
            link=link, kind='assignment', text='🧩 Назначена\nДисциплина Б', dedupe_key='load-assignment:1'
        )
        TelegramOutbox.objects.filter(pk__in=[first.pk, second.pk]).update(
            created_at=timezone.now() - timedelta(seconds=61)
        )
        batch = _next_batch()
        self.assertEqual(len(batch), 1)
        self.assertEqual(set(batch[0][0]), {first.pk, second.pk})
        self.assertIn('Обновлено назначений нагрузки (2)', batch[0][5])
        _mark_error(batch[0][0], 'temporary')
        self.assertEqual(TelegramOutbox.objects.get(pk=first.pk).attempts, 1)
        _mark_error(batch[0][0], 'final', terminal=True)
        self.assertEqual(TelegramOutbox.objects.get(pk=second.pk).attempts, 3)

    def test_coalesced_assignment_notification_fits_telegram_message_limit(self):
        link = TelegramLink.objects.create(user=self.user, telegram_user_id=151, chat_id=251)
        records = [
            TelegramOutbox.objects.create(
                link=link, kind='assignment', text=f'🧩 Нагрузка назначена.\nНазначение {index} ' + 'x' * 3000,
                dedupe_key='load-assignment:long',
            )
            for index in range(5)
        ]
        TelegramOutbox.objects.filter(pk__in=[record.pk for record in records]).update(
            created_at=timezone.now() - timedelta(seconds=61)
        )

        from core.tgbot.outbox import _next_batch
        batch = _next_batch()

        self.assertEqual(len(batch), 1)
        self.assertLessEqual(len(batch[0][5]), 4096)
        self.assertIn('Назначение 0', batch[0][5])
        self.assertIn('и ещё 4', batch[0][5])

    def test_department_distribution_filters_and_separates_assigned_items(self):
        specialty = Specialty.objects.create(code='09.03.03', name='Информатика', abbr='ПИ')
        curriculum = Curriculum.objects.create(specialty=specialty, admission_year=2025)
        discipline = Discipline.objects.create(
            curriculum=curriculum, department=self.department, name='Алгоритмы', semester=1,
        )
        unassigned = LoadItem.objects.create(discipline=discipline, kind='lec', hours=12)
        assigned = LoadItem.objects.create(
            discipline=discipline, kind='lab', hours=8, teacher=self.teacher,
        )

        filters = {'specialty_id': specialty.pk, 'semester': 1, 'kind': 'lec'}
        unassigned_items, unassigned_count = _undistributed(self.department, filters=filters)
        assigned_items, assigned_count = _assigned_items(self.department, filters=filters)

        self.assertEqual((unassigned_count, [item.pk for item in unassigned_items]), (1, [unassigned.pk]))
        self.assertEqual((assigned_count, assigned_items), (0, []))
        self.assertEqual([item.pk for item in _assigned_items(self.department)[0]], [assigned.pk])
        self.assertEqual([item.pk for item in _undistributed(self.department)[0]], [unassigned.pk])
        self.assertEqual(_distribution_filter_choices(self.department, 2), [1])
        self.assertEqual(_distribution_filter_choices(self.department, 3), [('lec', 'Лекция'), ('lab', 'Лабораторная')])

    def test_deputy_dean_lists_are_paginated(self):
        departments = [SimpleNamespace(pk=index, name=f'Кафедра {index}') for index in range(10)]
        data = {
            'specialties': 2, 'departments': 10, 'groups': 12, 'curricula': 4, 'disciplines': 20,
            'rows': [(department, 10, 2, 1) for department in departments],
        }

        text, keyboard = _dashboard_text(data, 0)
        rows = _department_choice_rows(departments, 0, 'import_department', 'import_department_page')

        self.assertIn('страница 1/2', text)
        self.assertIn('Кафедра 7', text)
        self.assertNotIn('Кафедра 8:', text)
        self.assertEqual(len(rows), 9)
        self.assertIsNotNone(keyboard)
        self.assertTrue(all(len(label) <= 64 for row in rows for label, _ in row))

    def test_menu_contains_combined_roles(self):
        menu = main_menu({'teacher', 'head'})
        labels = {button.text for row in menu.keyboard for button in row}
        self.assertIn('📖 Моя нагрузка', labels)
        self.assertIn('🧩 Распределение', labels)
