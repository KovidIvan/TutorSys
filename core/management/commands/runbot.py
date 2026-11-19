from django.core.management.base import BaseCommand
from django.contrib.auth import authenticate
from django.conf import settings
from django.db.models import Sum
from datetime import date
from decimal import Decimal
import time

from core.models import (
    TelegramProfile, Teacher, Department, Specialty, Group as AcademicGroup,
    Discipline, LoadItem, JournalEntry
)
from core.calculations import teacher_total, teacher_workload, report_matrix, check_teacher, MONTH_NAMES
from core.telegram_utils import telegram_api_call


class Command(BaseCommand):
    help = 'Запуск Telegram-бота и кнопки Telegram Mini App (WebApp) для платформы S.C.A.L.E'

    def add_arguments(self, parser):
        parser.add_argument(
            '--webapp-url',
            type=str,
            default='',
            help='Публичный HTTPS URL приложения для кнопки Telegram Mini App (например, https://scale.example.com)',
        )
        parser.add_argument(
            '--once',
            action='store_true',
            help='Обработать одну пачку обновлений и завершиться (для тестирования)',
        )

    def handle(self, *args, **options):
        token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '').strip()
        if not token:
            self.stderr.write(self.style.ERROR('TELEGRAM_BOT_TOKEN не задан в .env'))
            return

        webapp_base = (options.get('webapp_url') or getattr(settings, 'TELEGRAM_WEBAPP_URL', '')).strip().rstrip('/')
        webapp_url = f'{webapp_base}/telegram/webapp/' if webapp_base.startswith('https://') else ''

        me = telegram_api_call('getMe', bot_token=token)
        bot_info = me.get('result', {})
        bot_username = bot_info.get('username', 'scale_bot')
        self.stdout.write(self.style.SUCCESS(
            f'Telegram-бот S.C.A.L.E запущен: @{bot_username} (ID: {bot_info.get("id")})'
        ))

        self._setup_bot_commands_and_menu(token, webapp_url)

        offset = 0
        while True:
            try:
                resp = telegram_api_call(
                    'getUpdates',
                    payload={'offset': offset, 'timeout': 25},
                    bot_token=token,
                    timeout=35,
                )
                for upd in resp.get('result', []):
                    offset = upd['update_id'] + 1
                    self._process_update(upd, token, webapp_url)
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING('Остановка Telegram-бота...'))
                break
            except Exception as exc:
                self.stderr.write(f'Ошибка опроса Telegram API: {exc}')
                time.sleep(3)

            if options.get('once'):
                break

    def _setup_bot_commands_and_menu(self, token: str, webapp_url: str):
        commands = [
            {'command': 'start', 'description': 'Главное меню и запуск S.C.A.L.E Mini App'},
            {'command': 'status', 'description': 'Мой профиль, роль и сводка'},
            {'command': 'workload', 'description': 'Моя учебная нагрузка по семестрам'},
            {'command': 'report', 'description': 'Сводка План / Факт за текущий месяц и год'},
            {'command': 'link', 'description': 'Привязать аккаунт: /link логин пароль'},
            {'command': 'unlink', 'description': 'Отвязать Telegram от аккаунта'},
            {'command': 'help', 'description': 'Список команд и справка'},
        ]
        try:
            telegram_api_call('setMyCommands', {'commands': commands}, bot_token=token)
            if webapp_url:
                telegram_api_call(
                    'setChatMenuButton',
                    {
                        'menu_button': {
                            'type': 'web_app',
                            'text': 'S.C.A.L.E',
                            'web_app': {'url': webapp_url},
                        }
                    },
                    bot_token=token,
                )
                self.stdout.write(self.style.SUCCESS(f'Кнопка Telegram Mini App настроена на: {webapp_url}'))
        except Exception as exc:
            self.stderr.write(f'Предупреждение при настройке меню бота: {exc}')

    def _build_keyboard(self, webapp_url: str, is_linked: bool):
        inline_rows = []
        if webapp_url:
            inline_rows.append([
                {'text': '🚀 Открыть приложение S.C.A.L.E в чате', 'web_app': {'url': webapp_url}}
            ])
        if is_linked:
            inline_rows.append([
                {'text': '📊 Моя нагрузка', 'callback_data': 'cmd_workload'},
                {'text': '📈 План / Факт', 'callback_data': 'cmd_report'},
            ])
            inline_rows.append([
                {'text': '👤 Мой профиль', 'callback_data': 'cmd_status'},
            ])
        return {'inline_keyboard': inline_rows} if inline_rows else None

    def _process_update(self, upd: dict, token: str, webapp_url: str):
        if 'callback_query' in upd:
            cb = upd['callback_query']
            chat_id = cb['message']['chat']['id']
            tg_user = cb['from']
            data = cb.get('data', '')
            try:
                telegram_api_call('answerCallbackQuery', {'callback_query_id': cb['id']}, bot_token=token)
            except Exception:
                pass
            if data == 'cmd_workload':
                self._handle_workload(chat_id, tg_user, token, webapp_url)
            elif data == 'cmd_report':
                self._handle_report(chat_id, tg_user, token, webapp_url)
            elif data == 'cmd_status':
                self._handle_status(chat_id, tg_user, token, webapp_url)
            return

        msg = upd.get('message')
        if not msg or 'text' not in msg:
            return

        chat_id = msg['chat']['id']
        tg_user = msg.get('from', {})
        text = msg['text'].strip()

        if text.startswith('/link'):
            # Удаляем сообщение с паролем из чата для безопасности
            try:
                telegram_api_call('deleteMessage', {'chat_id': chat_id, 'message_id': msg['message_id']}, bot_token=token)
            except Exception:
                pass
            self._handle_link(chat_id, tg_user, text, token, webapp_url)
        elif text.startswith('/unlink'):
            self._handle_unlink(chat_id, tg_user, token, webapp_url)
        elif text.startswith('/workload'):
            self._handle_workload(chat_id, tg_user, token, webapp_url)
        elif text.startswith('/report'):
            self._handle_report(chat_id, tg_user, token, webapp_url)
        elif text.startswith('/status'):
            self._handle_status(chat_id, tg_user, token, webapp_url)
        elif text.startswith('/help'):
            self._handle_start(chat_id, tg_user, token, webapp_url)
        else:
            self._handle_start(chat_id, tg_user, token, webapp_url)

    def _send(self, chat_id: int, text: str, token: str, reply_markup=None):
        payload = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML',
        }
        if reply_markup:
            payload['reply_markup'] = reply_markup
        telegram_api_call('sendMessage', payload, bot_token=token)

    def _get_profile(self, tg_user: dict):
        tg_id = tg_user.get('id')
        if not tg_id:
            return None
        return TelegramProfile.objects.filter(telegram_id=tg_id).select_related('user').first()

    def _handle_start(self, chat_id: int, tg_user: dict, token: str, webapp_url: str):
        profile = self._get_profile(tg_user)
        first_name = tg_user.get('first_name', 'пользователь')

        if profile:
            user = profile.user
            role_label = self._role_title(user)
            text = (
                f'🏛 <b>S.C.A.L.E — Staff Curriculum &amp; Allocation Ledger Engine</b>\n\n'
                f'Здравствуйте, <b>{user.first_name or user.username}</b>!\n'
                f'Ваш аккаунт привязан: <code>{user.username}</code> ({role_label}).\n\n'
                f'• Нажмите кнопку ниже, чтобы открыть веб-приложение прямо в чате или посмотреть сводку нагрузки и выполнения плана.\n'
                f'• Команды: /status, /workload, /report, /unlink'
            )
        else:
            text = (
                f'🏛 <b>S.C.A.L.E — Staff Curriculum &amp; Allocation Ledger Engine</b>\n\n'
                f'Здравствуйте, <b>{first_name}</b>!\n'
                f'Чтобы пользоваться ботом и автоматически входить в Telegram Mini App, привяжите свою учётную запись командой:\n\n'
                f'<code>/link ваш_логин ваш_пароль</code>\n\n'
                f'<i>Например (демо-аккаунты):</i>\n'
                f'• <code>/link deputy_dean pass123</code> (Зам. декана)\n'
                f'• <code>/link head pass123</code> (Зав. кафедрой)\n'
                f'• <code>/link teacher pass123</code> (Преподаватель)'
            )

        self._send(chat_id, text, token, self._build_keyboard(webapp_url, bool(profile)))

    def _handle_link(self, chat_id: int, tg_user: dict, text: str, token: str, webapp_url: str):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            self._send(
                chat_id,
                '⚠️ Укажите логин и пароль через пробел:\n<code>/link логин пароль</code>',
                token,
            )
            return

        _, username, password = parts
        user = authenticate(username=username.strip(), password=password)
        if user is None:
            self._send(chat_id, '❌ Неверный логин или пароль учётной записи S.C.A.L.E.', token)
            return

        tg_id = tg_user.get('id')
        TelegramProfile.objects.filter(telegram_id=tg_id).exclude(user=user).delete()
        TelegramProfile.objects.update_or_create(
            user=user,
            defaults={
                'telegram_id': tg_id,
                'telegram_username': tg_user.get('username', '') or '',
                'first_name': tg_user.get('first_name', '') or '',
            },
        )
        role_label = self._role_title(user)
        self._send(
            chat_id,
            f'✅ Аккаунт <b>{user.first_name or user.username}</b> (<code>{user.username}</code> · {role_label}) успешно привязан к вашему Telegram!\n\n'
            f'Сообщение с паролем автоматически удалено из чата.',
            token,
            self._build_keyboard(webapp_url, True),
        )

    def _handle_unlink(self, chat_id: int, tg_user: dict, token: str, webapp_url: str):
        profile = self._get_profile(tg_user)
        if not profile:
            self._send(chat_id, 'ℹ️ Ваш Telegram сейчас не привязан ни к одной учётной записи.', token)
            return
        uname = profile.user.username
        profile.delete()
        self._send(
            chat_id,
            f'🔓 Привязка к учётной записи <code>{uname}</code> удалена.',
            token,
            self._build_keyboard(webapp_url, False),
        )

    def _role_title(self, user) -> str:
        roles = set(user.groups.values_list('name', flat=True))
        if 'deputy_dean' in roles:
            return 'Заместитель декана'
        if 'head' in roles:
            return 'Заведующий кафедрой'
        if 'teacher' in roles:
            return 'Преподаватель'
        if user.is_staff:
            return 'Администратор'
        return 'Пользователь'

    def _handle_status(self, chat_id: int, tg_user: dict, token: str, webapp_url: str):
        profile = self._get_profile(tg_user)
        if not profile:
            self._send(chat_id, '⚠️ Сначала привяжите аккаунт: <code>/link логин пароль</code>', token)
            return

        user = profile.user
        role_label = self._role_title(user)
        roles = set(user.groups.values_list('name', flat=True))
        lines = [
            f'👤 <b>Профиль S.C.A.L.E</b>',
            f'• Пользователь: <b>{user.first_name or user.username}</b> (<code>{user.username}</code>)',
            f'• Роль: <b>{role_label}</b>',
        ]

        if hasattr(user, 'teacher'):
            t = user.teacher
            total = teacher_total(t)
            lines.append(f'• Кафедра: <b>{t.department.name}</b>')
            if t.position:
                lines.append(f'• Должность: {t.position.position} (ставка {t.rate})')
            lines.append(f'• Назначено часов: <b>{total} ч</b> (норма {t.effective_min_hours}–{t.effective_max_hours} ч)')

        if 'deputy_dean' in roles:
            lines.append('\n🏛 <b>Сводка по факультету:</b>')
            lines.append(f'• Специальностей: <b>{Specialty.objects.count()}</b>')
            lines.append(f'• Кафедр: <b>{Department.objects.count()}</b>')
            lines.append(f'• Академических групп: <b>{AcademicGroup.objects.count()}</b>')
            lines.append(f'• Дисциплин: <b>{Discipline.objects.count()}</b>')
            lines.append(f'• Преподавателей: <b>{Teacher.objects.count()}</b>')
        elif 'head' in roles and hasattr(user, 'teacher'):
            dept = user.teacher.department
            total_items = LoadItem.objects.filter(discipline__department=dept).count()
            unassigned = LoadItem.objects.filter(discipline__department=dept, teacher__isnull=True).count()
            lines.append(f'\n🏢 <b>Сводка по кафедре «{dept.name}»:</b>')
            lines.append(f'• Преподавателей: <b>{dept.teachers.count()}</b>')
            lines.append(f'• Элементов нагрузки: <b>{total_items}</b> (нераспределённых: <b>{unassigned}</b>)')

        self._send(chat_id, '\n'.join(lines), token, self._build_keyboard(webapp_url, True))

    def _handle_workload(self, chat_id: int, tg_user: dict, token: str, webapp_url: str):
        profile = self._get_profile(tg_user)
        if not profile:
            self._send(chat_id, '⚠️ Сначала привяжите аккаунт: <code>/link логин пароль</code>', token)
            return

        user = profile.user
        if not hasattr(user, 'teacher'):
            self._send(
                chat_id,
                'ℹ️ У вашей учётной записи нет карточки преподавателя. Используйте /status для сводки по деканату.',
                token,
                self._build_keyboard(webapp_url, True),
            )
            return

        teacher = user.teacher
        wl = teacher_workload(teacher)
        total = wl['grand_totals_total']
        warnings = check_teacher(teacher)

        lines = [
            f'📊 <b>Учебная нагрузка · {teacher.full_name}</b>',
            f'Кафедра: {teacher.department.name}',
            f'Норма на ставку {teacher.rate}: <b>{teacher.effective_min_hours}–{teacher.effective_max_hours} ч</b>\n',
            f'🍂 Осенний семестр: <b>{wl["fall_totals_total"]} ч</b>',
            f'🌱 Весенний семестр: <b>{wl["spring_totals_total"]} ч</b>',
            f'∑ <b>Итого за год: {total} ч</b>',
        ]

        if wl['rows']:
            lines.append('\n<b>Дисциплины:</b>')
            for r in wl['rows'][:12]:
                disc_name = r['discipline'].name if r['discipline'] else 'Дисциплина'
                lines.append(f'• Сем. {r["semester"]} — {disc_name} ({r["groups"]}): <b>{r["total"]} ч</b>')

        if warnings:
            lines.append('\n⚠️ <b>Замечания по нормативам:</b>')
            for w in warnings:
                lines.append(f'• {w}')
        else:
            lines.append('\n✅ Нагрузка соответствует нормативам должности.')

        self._send(chat_id, '\n'.join(lines), token, self._build_keyboard(webapp_url, True))

    def _handle_report(self, chat_id: int, tg_user: dict, token: str, webapp_url: str):
        profile = self._get_profile(tg_user)
        if not profile:
            self._send(chat_id, '⚠️ Сначала привяжите аккаунт: <code>/link логин пароль</code>', token)
            return

        user = profile.user
        year = date.today().year
        cur_month = date.today().month
        roles = set(user.groups.values_list('name', flat=True))

        if 'head' in roles and hasattr(user, 'teacher'):
            dept = user.teacher.department
            teachers = list(Teacher.objects.filter(department=dept))
            title = f'Кафедра «{dept.name}»'
        elif hasattr(user, 'teacher'):
            teachers = [user.teacher]
            title = user.teacher.full_name
        else:
            teachers = list(Teacher.objects.all())
            title = 'Все кафедры факультета'

        matrix = report_matrix(teachers, year)
        year_tot = matrix['totals']['total']
        month_row = next((r for r in matrix['rows'] if r['month'] == cur_month), None)

        lines = [
            f'📈 <b>Отчёт План / Факт ({year} г.)</b>',
            f'Объект: <b>{title}</b>\n',
        ]
        if month_row:
            mt = month_row['total']
            lines.append(
                f'📅 <b>{MONTH_NAMES[cur_month - 1]}:</b> факт <b>{mt["fact"]:.2f} ч</b> / план <b>{mt["plan"]:.2f} ч</b> '
                f'(отклонение: {mt["delta"]:+.2f} ч)'
            )

        lines.append(
            f'∑ <b>Итого за {year} год:</b> факт <b>{year_tot["fact"]:.2f} ч</b> / план <b>{year_tot["plan"]:.2f} ч</b> '
            f'(отклонение: {year_tot["delta"]:+.2f} ч)'
        )

        self._send(chat_id, '\n'.join(lines), token, self._build_keyboard(webapp_url, True))
