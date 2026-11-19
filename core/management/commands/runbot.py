from django.core.management.base import BaseCommand
from django.contrib.auth import authenticate
from django.conf import settings
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import html
import os
import time
import urllib.request

from core.models import (
    TelegramProfile, Teacher, Department, Specialty, Group as AcademicGroup,
    Discipline, LoadItem, JournalEntry
)
from core.calculations import teacher_total, teacher_workload, report_matrix, check_teacher, MONTH_NAMES
from core.telegram_utils import telegram_api_call, telegram_send_document, build_xlsx_bytes


class Command(BaseCommand):
    help = 'Запуск Telegram-бота и кнопки Telegram Mini App (WebApp) для платформы S.C.A.L.E'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Состояния пошагового добавления записи в журнал: {chat_id: {'step': ..., ...}}
        self.user_states = {}

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
        self.site_url = self._resolve_site_url(webapp_base)

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
            {'command': 'start', 'description': 'Главное меню и кнопки разделов'},
            {'command': 'workload', 'description': 'Таблица «Моя нагрузка» + выгрузка Excel'},
            {'command': 'journal', 'description': 'Журнал занятий и добавление записи'},
            {'command': 'report', 'description': 'Матрица План / Факт + выгрузка Excel'},
            {'command': 'export', 'description': 'Скачать Excel-таблицы в чат'},
            {'command': 'status', 'description': 'Мой профиль и сводка'},
            {'command': 'link', 'description': 'Привязать аккаунт: /link логин пароль'},
            {'command': 'unlink', 'description': 'Отвязать Telegram от аккаунта'},
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

    def _resolve_site_url(self, webapp_base: str) -> str:
        """Определить прямую ссылку на веб-сайт проекта для кнопки в Telegram."""
        env_site = os.getenv('SITE_URL', '').strip().rstrip('/')
        if env_site.startswith(('http://', 'https://')):
            return env_site
        if webapp_base.startswith(('http://', 'https://')):
            return webapp_base
        port = os.getenv('PORT', '8000').strip() or '8000'
        for host in getattr(settings, 'ALLOWED_HOSTS', []):
            h = host.strip()
            if h and h not in {'*', 'localhost', '127.0.0.1', 'testserver', '0.0.0.0'}:
                scheme = 'https' if h.endswith('.sslip.io') else 'http'
                return f'{scheme}://{h}' if scheme == 'https' or ':' in h else f'{scheme}://{h}:{port}'
        try:
            with urllib.request.urlopen('https://api.ipify.org', timeout=3) as resp:
                public_ip = resp.read().decode('utf-8').strip()
                if public_ip and '.' in public_ip:
                    return f'http://{public_ip}:{port}'
        except Exception:
            pass
        return ''

    def _nav_footer_rows(self, webapp_url: str, include_profile: bool = True):
        """Нижний ряд кнопок для любого экрана: возврат в свой профиль и прямой переход на сайт."""
        rows = []
        nav_row = []
        if include_profile:
            nav_row.append({'text': '👤 В мой профиль', 'callback_data': 'cmd_status'})
        if getattr(self, 'site_url', ''):
            nav_row.append({'text': '🌐 Перейти на сайт S.C.A.L.E', 'url': self.site_url})
        if nav_row:
            rows.append(nav_row)
        return rows

    def _build_keyboard(self, webapp_url: str, is_linked: bool, user=None):
        inline_rows = []
        if webapp_url:
            inline_rows.append([
                {'text': '🚀 Открыть приложение S.C.A.L.E в чате', 'web_app': {'url': webapp_url}}
            ])
        if is_linked:
            inline_rows.append([
                {'text': '📊 Моя нагрузка', 'callback_data': 'cmd_workload'},
                {'text': '📓 Журнал занятий', 'callback_data': 'cmd_journal'},
            ])
            inline_rows.append([
                {'text': '➕ Добавить занятие', 'callback_data': 'j_add_start'},
                {'text': '📈 План / Факт', 'callback_data': 'cmd_report'},
            ])
            inline_rows.append([
                {'text': '📥 Скачать Excel', 'callback_data': 'cmd_export'},
                {'text': '👤 В мой профиль', 'callback_data': 'cmd_status'},
            ])
        if getattr(self, 'site_url', ''):
            inline_rows.append([
                {'text': '🌐 Перейти на сайт S.C.A.L.E', 'url': self.site_url}
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
            self._handle_callback(chat_id, tg_user, data, token, webapp_url)
            return

        msg = upd.get('message')
        if not msg or 'text' not in msg:
            return

        chat_id = msg['chat']['id']
        tg_user = msg.get('from', {})
        text = msg['text'].strip()

        if text.startswith('/cancel'):
            self.user_states.pop(chat_id, None)
            self._send(chat_id, '✖️ Действие отменено.', token, self._build_keyboard(webapp_url, bool(self._get_profile(tg_user))))
            return

        # Если пользователь находится в пошаговом мастере добавления занятия в журнал
        if chat_id in self.user_states and not text.startswith('/'):
            self._handle_journal_wizard_input(chat_id, tg_user, text, token, webapp_url)
            return

        # Сбрасываем незавершённый мастер при вызове новой команды
        self.user_states.pop(chat_id, None)

        if text.startswith('/link'):
            try:
                telegram_api_call('deleteMessage', {'chat_id': chat_id, 'message_id': msg['message_id']}, bot_token=token)
            except Exception:
                pass
            self._handle_link(chat_id, tg_user, text, token, webapp_url)
        elif text.startswith('/unlink'):
            self._handle_unlink(chat_id, tg_user, token, webapp_url)
        elif text.startswith('/workload'):
            self._handle_workload(chat_id, tg_user, token, webapp_url)
        elif text.startswith('/journal'):
            self._handle_journal(chat_id, tg_user, token, webapp_url)
        elif text.startswith('/report'):
            self._handle_report(chat_id, tg_user, token, webapp_url)
        elif text.startswith('/export'):
            self._handle_export_menu(chat_id, tg_user, token, webapp_url)
        elif text.startswith('/status'):
            self._handle_status(chat_id, tg_user, token, webapp_url)
        else:
            self._handle_start(chat_id, tg_user, token, webapp_url)

    def _handle_callback(self, chat_id: int, tg_user: dict, data: str, token: str, webapp_url: str):
        if data == 'cmd_workload':
            self._handle_workload(chat_id, tg_user, token, webapp_url)
        elif data == 'cmd_journal':
            self._handle_journal(chat_id, tg_user, token, webapp_url)
        elif data == 'cmd_report':
            self._handle_report(chat_id, tg_user, token, webapp_url)
        elif data == 'cmd_export':
            self._handle_export_menu(chat_id, tg_user, token, webapp_url)
        elif data == 'cmd_status':
            self._handle_status(chat_id, tg_user, token, webapp_url)
        elif data == 'j_add_start':
            self._start_journal_wizard(chat_id, tg_user, token, webapp_url)
        elif data == 'j_cancel':
            self.user_states.pop(chat_id, None)
            self._handle_status(chat_id, tg_user, token, webapp_url)
        elif data.startswith('j_item:'):
            item_id = int(data.split(':', 1)[1])
            self._wizard_select_item(chat_id, tg_user, item_id, token)
        elif data.startswith('j_date:'):
            date_val = data.split(':', 1)[1]
            self._wizard_select_date(chat_id, date_val, token)
        elif data.startswith('j_hours:'):
            hours_val = data.split(':', 1)[1]
            self._wizard_select_hours(chat_id, hours_val, token)
        elif data.startswith('exp:'):
            export_kind = data.split(':', 1)[1]
            self._send_excel_export(chat_id, tg_user, export_kind, token, webapp_url)

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

    def _handle_start(self, chat_id: int, tg_user: dict, token: str, webapp_url: str):
        profile = self._get_profile(tg_user)
        first_name = html.escape(tg_user.get('first_name', 'пользователь'))

        if profile:
            user = profile.user
            role_label = self._role_title(user)
            text = (
                f'🏛 <b>S.C.A.L.E — Staff Curriculum &amp; Allocation Ledger Engine</b>\n\n'
                f'Здравствуйте, <b>{html.escape(user.first_name or user.username)}</b>!\n'
                f'Ваш аккаунт привязан: <code>{html.escape(user.username)}</code> ({role_label}).\n\n'
                f'Выберите нужный раздел кнопками ниже:\n'
                f'• <b>📊 Моя нагрузка</b> — таблица нагрузки по видам работ и семестрам\n'
                f'• <b>📓 Журнал занятий</b> — просмотр и автоматическое добавление занятий (синхронизировано с сайтом)\n'
                f'• <b>📈 План / Факт</b> — выполнение нагрузки по месяцам\n'
                f'• <b>📥 Скачать Excel</b> — выгрузка всех `.xlsx` таблиц прямо в этот чат'
            )
        else:
            text = (
                f'🏛 <b>S.C.A.L.E — Staff Curriculum &amp; Allocation Ledger Engine</b>\n\n'
                f'Здравствуйте, <b>{first_name}</b>!\n'
                f'Чтобы пользоваться ботом, журналом, таблицами нагрузки и выгрузкой Excel, привяжите свою учётную запись:\n\n'
                f'<code>/link ваш_логин ваш_пароль</code>\n\n'
                f'<i>Демо-аккаунты:</i>\n'
                f'• <code>/link teacher pass123</code> (Преподаватель)\n'
                f'• <code>/link head pass123</code> (Зав. кафедрой)\n'
                f'• <code>/link deputy_dean pass123</code> (Зам. декана)'
            )

        if getattr(self, 'site_url', ''):
            text += f'\n\n🌐 Веб-сайт проекта: <a href="{html.escape(self.site_url)}">{html.escape(self.site_url)}</a>'

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
            f'✅ Аккаунт <b>{html.escape(user.first_name or user.username)}</b> (<code>{html.escape(user.username)}</code> · {role_label}) успешно привязан к вашему Telegram!\n\n'
            f'Сообщение с паролем автоматически удалено из чата.',
            token,
            self._build_keyboard(webapp_url, True),
        )

    def _handle_unlink(self, chat_id: int, tg_user: dict, token: str, webapp_url: str):
        profile = self._get_profile(tg_user)
        if not profile:
            self._send(chat_id, 'ℹ️ Ваш Telegram сейчас не привязан ни к одной учётной записи.', token)
            return
        uname = html.escape(profile.user.username)
        profile.delete()
        self._send(
            chat_id,
            f'🔓 Привязка к учётной записи <code>{uname}</code> удалена.',
            token,
            self._build_keyboard(webapp_url, False),
        )

    # ============ РАЗДЕЛ 1: ТАБЛИЦА «МОЯ НАГРУЗКА» ============

    def _handle_workload(self, chat_id: int, tg_user: dict, token: str, webapp_url: str):
        profile = self._get_profile(tg_user)
        if not profile:
            self._send(chat_id, '⚠️ Сначала привяжите аккаунт: <code>/link логин пароль</code>', token)
            return

        user = profile.user
        if not hasattr(user, 'teacher'):
            self._send(
                chat_id,
                'ℹ️ У вашей учётной записи нет личной карточки преподавателя. Используйте раздел «📥 Скачать Excel» или «📈 План / Факт» для просмотра кафедральных данных.',
                token,
                self._build_keyboard(webapp_url, True),
            )
            return

        teacher = user.teacher
        wl = teacher_workload(teacher)
        total = wl['grand_totals_total']
        warnings = check_teacher(teacher)

        lines = [
            f'📊 <b>Моя учебная нагрузка — {html.escape(teacher.full_name)}</b>',
            f'Кафедра: <b>{html.escape(teacher.department.name)}</b>',
            f'Годовая норма (ставка {teacher.rate}): <b>{teacher.effective_min_hours}–{teacher.effective_max_hours} ч</b>\n',
        ]

        if wl['has_data']:
            # Формируем текстовую таблицу <pre> со столбцами: Сем | Дисциплина | Группы | Лек | Пр | Лаб | Проч | Всего
            kind_codes = [k for k, _ in wl['kinds']]
            idx_lec = kind_codes.index('lec') if 'lec' in kind_codes else -1
            idx_pract = kind_codes.index('pract') if 'pract' in kind_codes else -1
            idx_lab = kind_codes.index('lab') if 'lab' in kind_codes else -1

            table_lines = [
                f'{"Сем":<3} | {"Дисциплина":<18} | {"Лек":>5} | {"Пр":>5} | {"Лаб":>5} | {"Проч":>5} | {"Всего":>6}',
                '-' * 62,
            ]
            for r in wl['rows']:
                dname = (r['discipline'].name if r['discipline'] else '—')[:18]
                hl = r['hours_list']
                h_lec = hl[idx_lec] if idx_lec >= 0 else Decimal('0')
                h_pr = hl[idx_pract] if idx_pract >= 0 else Decimal('0')
                h_lab = hl[idx_lab] if idx_lab >= 0 else Decimal('0')
                h_other = r['total'] - (h_lec + h_pr + h_lab)
                table_lines.append(
                    f'{r["semester"]:<3} | {dname:<18} | {h_lec:>5.1f} | {h_pr:>5.1f} | {h_lab:>5.1f} | {h_other:>5.1f} | {r["total"]:>6.2f}'
                )
            table_lines.append('-' * 62)
            table_lines.append(
                f'{"Осень (нечёт. сем.)":<24} | {"":>5} | {"":>5} | {"":>5} | {"":>5} | {wl["fall_totals_total"]:>6.2f}'
            )
            table_lines.append(
                f'{"Весна (чёт. сем.)":<24} | {"":>5} | {"":>5} | {"":>5} | {"":>5} | {wl["spring_totals_total"]:>6.2f}'
            )
            table_lines.append(
                f'{"ИТОГО ЗА ГОД":<24} | {"":>5} | {"":>5} | {"":>5} | {"":>5} | {total:>6.2f}'
            )
            lines.append('<pre>' + html.escape('\n'.join(table_lines)) + '</pre>')

            lines.append('\n<b>Подробно по группам и видам работ:</b>')
            kind_labels = [lbl for _, lbl in wl['kinds']]
            for r in wl['rows']:
                dname = html.escape(r['discipline'].name if r['discipline'] else '—')
                parts = [
                    f'{kind_labels[i]}: {h:.2f} ч'
                    for i, h in enumerate(r['hours_list'])
                    if h > 0
                ]
                lines.append(
                    f'• <b>{dname}</b> (сем. {r["semester"]}, {html.escape(r["groups"])}) — <b>{r["total"]:.2f} ч</b> '
                    f'<i>({", ".join(parts)})</i>'
                )
        else:
            lines.append('<i>Вам пока не назначена учебная нагрузка.</i>')

        if warnings:
            lines.append('\n⚠️ <b>Замечания по нормативам:</b>')
            for w in warnings:
                lines.append(f'• {html.escape(w)}')
        elif wl['has_data']:
            lines.append('\n✅ Нагрузка соответствует нормативам должности.')

        kb = {
            'inline_keyboard': [
                [{'text': '📥 Скачать «Моя нагрузка» (.xlsx)', 'callback_data': 'exp:workload'}],
                [
                    {'text': '📓 Журнал занятий', 'callback_data': 'cmd_journal'},
                    {'text': '➕ Добавить занятие', 'callback_data': 'j_add_start'},
                ],
            ] + self._nav_footer_rows(webapp_url, include_profile=True)
        }
        if webapp_url:
            kb['inline_keyboard'].insert(0, [{'text': '🚀 Открыть таблицу в Mini App', 'web_app': {'url': webapp_url}}])

        self._send(chat_id, '\n'.join(lines), token, kb)

    # ============ РАЗДЕЛ 2: ЖУРНАЛ ЗАНЯТИЙ И АВТОМАТИЧЕСКОЕ ДОБАВЛЕНИЕ ============

    def _handle_journal(self, chat_id: int, tg_user: dict, token: str, webapp_url: str):
        profile = self._get_profile(tg_user)
        if not profile:
            self._send(chat_id, '⚠️ Сначала привяжите аккаунт: <code>/link логин пароль</code>', token)
            return

        user = profile.user
        if not hasattr(user, 'teacher'):
            self._send(
                chat_id,
                'ℹ️ Ведение личного журнала доступно для учётных записей преподавателей.',
                token,
                self._build_keyboard(webapp_url, True),
            )
            return

        teacher = user.teacher
        entries = list(
            JournalEntry.objects.filter(teacher=teacher)
            .select_related('load_item__discipline', 'load_item__group')
            .order_by('-date', '-time', '-id')[:12]
        )
        total_entries = JournalEntry.objects.filter(teacher=teacher).count()

        lines = [
            f'📓 <b>Журнал учебных занятий — {html.escape(teacher.full_name)}</b>',
            f'Всего записей в базе (синхронизировано с сайтом): <b>{total_entries}</b>\n',
        ]

        if entries:
            table_lines = [
                f'{"Дата":<10} | {"Дисциплина":<16} | {"Вид":<6} | {"Группа":<8} | {"Часы":>5}',
                '-' * 56,
            ]
            for e in entries:
                d_str = e.date.strftime('%d.%m.%Y')
                disc = e.load_item.discipline.name[:16]
                kind = e.load_item.get_kind_display()[:6]
                grp = (
                    f'{e.load_item.group.name}-{e.load_item.subgroup_no}'
                    if e.load_item.group and e.load_item.subgroup_no
                    else (e.load_item.group.name if e.load_item.group else 'Поток')
                )[:8]
                table_lines.append(f'{d_str:<10} | {disc:<16} | {kind:<6} | {grp:<8} | {e.hours:>5.2f}')
            lines.append('<pre>' + html.escape('\n'.join(table_lines)) + '</pre>')

            lines.append('\n<b>Последние темы занятий:</b>')
            for e in entries[:5]:
                d_str = e.date.strftime('%d.%m.%Y')
                lines.append(
                    f'• <b>{d_str}</b> · {html.escape(e.load_item.discipline.name)} '
                    f'({html.escape(e.load_item.get_kind_display())}, <b>{e.hours:.2f} ч</b>): '
                    f'<i>{html.escape(e.topic[:60])}</i>'
                )
        else:
            lines.append('<i>В журнале пока нет записей. Нажмите «➕ Добавить занятие», чтобы внести первую запись.</i>')

        kb = {
            'inline_keyboard': [
                [{'text': '➕ Добавить запись в журнал', 'callback_data': 'j_add_start'}],
                [{'text': '📥 Скачать журнал в Excel (.xlsx)', 'callback_data': 'exp:journal'}],
                [
                    {'text': '📊 Моя нагрузка', 'callback_data': 'cmd_workload'},
                    {'text': '📈 План / Факт', 'callback_data': 'cmd_report'},
                ],
            ] + self._nav_footer_rows(webapp_url, include_profile=True)
        }
        self._send(chat_id, '\n'.join(lines), token, kb)

    def _start_journal_wizard(self, chat_id: int, tg_user: dict, token: str, webapp_url: str):
        profile = self._get_profile(tg_user)
        if not profile or not hasattr(profile.user, 'teacher'):
            self._send(chat_id, '⚠️ Добавление записей в журнал доступно только авторизованному преподавателю.', token)
            return

        teacher = profile.user.teacher
        items = list(
            LoadItem.objects.filter(teacher=teacher)
            .select_related('discipline', 'group')
            .order_by('discipline__name', 'kind')
        )
        if not items:
            self._send(chat_id, 'ℹ️ Вам пока не назначены элементы нагрузки. Обратитесь к заведующему кафедрой.', token)
            return

        self.user_states[chat_id] = {'step': 'select_item', 'teacher_id': teacher.id}

        buttons = []
        for item in items[:25]:
            grp = (
                f'{item.group.name}-{item.subgroup_no}'
                if item.group and item.subgroup_no
                else (item.group.name if item.group else 'Поток')
            )
            label = f'{item.discipline.name[:24]} · {item.get_kind_display()} ({grp})'
            buttons.append([{'text': label, 'callback_data': f'j_item:{item.id}'}])
        buttons.append([{'text': '👤 Отмена · В мой профиль', 'callback_data': 'j_cancel'}])
        if getattr(self, 'site_url', ''):
            buttons.append([{'text': '🌐 Перейти на сайт S.C.A.L.E', 'url': self.site_url}])

        self._send(
            chat_id,
            '➕ <b>Добавление занятия в журнал (Шаг 1 из 4)</b>\n\nВыберите дисциплину и вид учебной работы:',
            token,
            {'inline_keyboard': buttons},
        )

    def _wizard_select_item(self, chat_id: int, tg_user: dict, item_id: int, token: str):
        profile = self._get_profile(tg_user)
        if not profile or not hasattr(profile.user, 'teacher'):
            return
        teacher = profile.user.teacher
        item = LoadItem.objects.filter(pk=item_id, teacher=teacher).select_related('discipline', 'group').first()
        if not item:
            self._send(chat_id, '❌ Элемент нагрузки не найден.', token)
            return

        today_str = date.today().isoformat()
        self.user_states[chat_id] = {
            'step': 'enter_date',
            'teacher_id': teacher.id,
            'load_item_id': item.id,
            'item_label': str(item),
        }

        rows = [
            [{'text': f'📅 Сегодня ({date.today().strftime("%d.%m.%Y")})', 'callback_data': f'j_date:{today_str}'}],
            [{'text': '👤 Отмена · В мой профиль', 'callback_data': 'j_cancel'}],
        ]
        if getattr(self, 'site_url', ''):
            rows.append([{'text': '🌐 Перейти на сайт S.C.A.L.E', 'url': self.site_url}])
        kb = {'inline_keyboard': rows}
        self._send(
            chat_id,
            f'➕ <b>Шаг 2 из 4 · Дата проведения</b>\n'
            f'Выбрано: <b>{html.escape(str(item))}</b>\n\n'
            f'Нажмите кнопку <b>«Сегодня»</b> или отправьте дату сообщением в формате <code>ДД.ММ.ГГГГ</code> (например, <code>{date.today().strftime("%d.%m.%Y")}</code>):',
            token,
            kb,
        )

    def _wizard_select_date(self, chat_id: int, date_iso: str, token: str):
        state = self.user_states.get(chat_id)
        if not state:
            return
        state['date'] = date_iso
        state['step'] = 'enter_hours'

        rows = [
            [
                {'text': '2 ч (пара)', 'callback_data': 'j_hours:2'},
                {'text': '1.5 ч', 'callback_data': 'j_hours:1.5'},
                {'text': '1 ч', 'callback_data': 'j_hours:1'},
                {'text': '4 ч', 'callback_data': 'j_hours:4'},
            ],
            [
                {'text': '0.5 ч', 'callback_data': 'j_hours:0.5'},
                {'text': '0.25 ч', 'callback_data': 'j_hours:0.25'},
            ],
            [{'text': '👤 Отмена · В мой профиль', 'callback_data': 'j_cancel'}],
        ]
        if getattr(self, 'site_url', ''):
            rows.append([{'text': '🌐 Перейти на сайт S.C.A.L.E', 'url': self.site_url}])
        kb = {'inline_keyboard': rows}
        self._send(
            chat_id,
            f'➕ <b>Шаг 3 из 4 · Количество часов</b>\n'
            f'Дата: <b>{date_iso}</b>\n\n'
            f'Выберите количество часов кнопкой или отправьте любое положительное число в чат (например, <code>2</code> или <code>1.25</code>):',
            token,
            kb,
        )

    def _wizard_select_hours(self, chat_id: int, hours_str: str, token: str):
        state = self.user_states.get(chat_id)
        if not state:
            return
        state['hours'] = hours_str
        state['step'] = 'enter_topic'

        rows = [[{'text': '👤 Отмена · В мой профиль', 'callback_data': 'j_cancel'}]]
        if getattr(self, 'site_url', ''):
            rows.append([{'text': '🌐 Перейти на сайт S.C.A.L.E', 'url': self.site_url}])
        kb = {'inline_keyboard': rows}
        self._send(
            chat_id,
            f'➕ <b>Шаг 4 из 4 · Тема занятия</b>\n'
            f'Часы: <b>{hours_str} ч</b>\n\n'
            f'Отправьте тему проведённого занятия текстовым сообщением:',
            token,
            kb,
        )

    def _handle_journal_wizard_input(self, chat_id: int, tg_user: dict, text: str, token: str, webapp_url: str):
        state = self.user_states.get(chat_id)
        if not state:
            return

        step = state.get('step')
        if step == 'enter_date':
            parsed_date = None
            for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y'):
                try:
                    parsed_date = datetime.strptime(text, fmt).date()
                    break
                except ValueError:
                    continue
            if not parsed_date:
                self._send(chat_id, '⚠️ Неверный формат даты. Введите дату как <code>ДД.ММ.ГГГГ</code> (например, <code>19.11.2026</code>):', token)
                return
            self._wizard_select_date(chat_id, parsed_date.isoformat(), token)

        elif step == 'enter_hours':
            try:
                hours_val = Decimal(text.replace(',', '.'))
                if hours_val <= 0 or hours_val > Decimal('24'):
                    raise ValueError
            except (InvalidOperation, ValueError):
                self._send(chat_id, '⚠️ Укажите положительное число часов (от 0.01 до 24):', token)
                return
            self._wizard_select_hours(chat_id, str(hours_val), token)

        elif step == 'enter_topic':
            topic = text.strip()
            if not topic:
                self._send(chat_id, '⚠️ Тема занятия не может быть пустой. Введите тему:', token)
                return

            profile = self._get_profile(tg_user)
            if not profile or not hasattr(profile.user, 'teacher'):
                self.user_states.pop(chat_id, None)
                return

            teacher = profile.user.teacher
            load_item = LoadItem.objects.filter(pk=state['load_item_id'], teacher=teacher).first()
            if not load_item:
                self.user_states.pop(chat_id, None)
                self._send(chat_id, '❌ Элемент нагрузки не найден.', token)
                return

            entry = JournalEntry.objects.create(
                teacher=teacher,
                load_item=load_item,
                date=date.fromisoformat(state['date']),
                hours=Decimal(state['hours']),
                topic=topic,
            )
            self.user_states.pop(chat_id, None)

            self._send(
                chat_id,
                f'✅ <b>Запись успешно добавлена в журнал и синхронизирована с сайтом!</b>\n\n'
                f'• Дисциплина: <b>{html.escape(str(load_item))}</b>\n'
                f'• Дата: <b>{entry.date.strftime("%d.%m.%Y")}</b>\n'
                f'• Часы: <b>{entry.hours:.2f} ч</b>\n'
                f'• Тема: <i>{html.escape(entry.topic)}</i>\n\n'
                f'Часы автоматически учтены в фактическом выполнении отчёта «План / Факт».',
                token,
                self._build_keyboard(webapp_url, True),
            )

    # ============ РАЗДЕЛ 3: ОТЧЁТ «ПЛАН / ФАКТ» ============

    def _handle_report(self, chat_id: int, tg_user: dict, token: str, webapp_url: str):
        profile = self._get_profile(tg_user)
        if not profile:
            self._send(chat_id, '⚠️ Сначала привяжите аккаунт: <code>/link логин пароль</code>', token)
            return

        user = profile.user
        year = date.today().year
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

        lines = [
            f'📈 <b>Отчёт План / Факт ({year} г.)</b>',
            f'Объект: <b>{html.escape(title)}</b>\n',
        ]

        table_lines = [
            f'{"Месяц":<10} | {"План, ч":>8} | {"Факт, ч":>8} | {"Откл., ч":>9}',
            '-' * 44,
        ]
        for row in matrix['rows']:
            tot = row['total']
            if tot['has_data']:
                table_lines.append(
                    f'{row["name"]:<10} | {tot["plan"]:>8.2f} | {tot["fact"]:>8.2f} | {tot["delta"]:>+9.2f}'
                )
        table_lines.append('-' * 44)
        table_lines.append(
            f'{"ИТОГО ГОД":<10} | {year_tot["plan"]:>8.2f} | {year_tot["fact"]:>8.2f} | {year_tot["delta"]:>+9.2f}'
        )
        lines.append('<pre>' + html.escape('\n'.join(table_lines)) + '</pre>')

        kb = {
            'inline_keyboard': [
                [{'text': f'📥 Скачать отчёт План/Факт {year} (.xlsx)', 'callback_data': 'exp:report'}],
                [
                    {'text': '📊 Моя нагрузка', 'callback_data': 'cmd_workload'},
                    {'text': '📓 Журнал занятий', 'callback_data': 'cmd_journal'},
                ],
            ] + self._nav_footer_rows(webapp_url, include_profile=True)
        }
        self._send(chat_id, '\n'.join(lines), token, kb)

    # ============ РАЗДЕЛ 4: ЭКСПОРТ EXCEL-ТАБЛИЦ ПРЯМО В TELEGRAM ============

    def _handle_export_menu(self, chat_id: int, tg_user: dict, token: str, webapp_url: str):
        profile = self._get_profile(tg_user)
        if not profile:
            self._send(chat_id, '⚠️ Сначала привяжите аккаунт: <code>/link логин пароль</code>', token)
            return

        user = profile.user
        roles = set(user.groups.values_list('name', flat=True))
        buttons = []

        if 'teacher' in roles or hasattr(user, 'teacher'):
            buttons.append([{'text': '📥 Моя учебная нагрузка (my_workload.xlsx)', 'callback_data': 'exp:workload'}])
            buttons.append([{'text': '📥 Мой журнал занятий (my_journal.xlsx)', 'callback_data': 'exp:journal'}])

        if 'head' in roles and hasattr(user, 'teacher'):
            buttons.append([{'text': '📥 Нагрузка кафедры (department_load.xlsx)', 'callback_data': 'exp:department-load'}])
            buttons.append([{'text': '📥 Преподаватели кафедры (department_teachers.xlsx)', 'callback_data': 'exp:teachers'}])

        buttons.append([{'text': f'📥 Отчёт План / Факт за {date.today().year} г. (.xlsx)', 'callback_data': 'exp:report'}])
        buttons.extend(self._nav_footer_rows(webapp_url, include_profile=True))

        self._send(
            chat_id,
            '📥 <b>Экспорт таблиц в Excel (.xlsx)</b>\n\n'
            'Выберите таблицу — бот мгновенно сформирует актуальный `.xlsx` файл и пришлёт его документом прямо в этот чат:',
            token,
            {'inline_keyboard': buttons},
        )

    def _send_excel_export(self, chat_id: int, tg_user: dict, export_kind: str, token: str, webapp_url: str):
        profile = self._get_profile(tg_user)
        if not profile:
            self._send(chat_id, '⚠️ Сначала привяжите аккаунт: <code>/link логин пароль</code>', token)
            return

        user = profile.user
        roles = set(user.groups.values_list('name', flat=True))
        cent = Decimal('0.01')

        try:
            if export_kind == 'workload':
                if not hasattr(user, 'teacher'):
                    self._send(chat_id, '❌ У вашей учётной записи нет профиля преподавателя.', token)
                    return
                teacher = user.teacher
                wl = teacher_workload(teacher)
                kind_labels = [label for _, label in wl['kinds']]
                headers = ['Дисциплина', 'Семестр', 'Группы'] + kind_labels + ['Всего']
                rows = []
                for row in wl['rows']:
                    rows.append(
                        [row['discipline'].name if row['discipline'] else '', row['semester'], row['groups']]
                        + [h.quantize(cent) for h in row['hours_list']]
                        + [row['total'].quantize(cent)]
                    )
                rows.append(['Всего за осенний семестр', '', ''] + [h.quantize(cent) for h in wl['fall_totals_list']] + [wl['fall_totals_total'].quantize(cent)])
                rows.append(['Всего за весенний семестр', '', ''] + [h.quantize(cent) for h in wl['spring_totals_list']] + [wl['spring_totals_total'].quantize(cent)])
                rows.append(['ИТОГО ЗА ГОД', '', ''] + [h.quantize(cent) for h in wl['grand_totals_list']] + [wl['grand_totals_total'].quantize(cent)])

                data = build_xlsx_bytes('Моя нагрузка', headers, rows)
                telegram_send_document(
                    chat_id,
                    'my_workload.xlsx',
                    data,
                    caption=f'📊 <b>Учебная нагрузка</b> · {html.escape(teacher.full_name)}',
                    bot_token=token,
                )
                return

            if export_kind == 'journal':
                if not hasattr(user, 'teacher'):
                    self._send(chat_id, '❌ У вашей учётной записи нет профиля преподавателя.', token)
                    return
                teacher = user.teacher
                entries = JournalEntry.objects.filter(teacher=teacher).select_related(
                    'load_item__discipline', 'load_item__group'
                ).order_by('-date', '-time')
                rows = [
                    (
                        entry.date.isoformat(),
                        entry.time.strftime('%H:%M') if entry.time else '',
                        entry.load_item.discipline.name,
                        entry.load_item.get_kind_display(),
                        entry.load_item.group.name if entry.load_item.group else 'Поток',
                        entry.hours.quantize(cent),
                        entry.topic,
                    )
                    for entry in entries
                ]
                data = build_xlsx_bytes(
                    'Журнал',
                    ['Дата', 'Время', 'Дисциплина', 'Вид работы', 'Группа', 'Часы', 'Тема'],
                    rows,
                )
                telegram_send_document(
                    chat_id,
                    'my_journal.xlsx',
                    data,
                    caption=f'📓 <b>Журнал учебных занятий</b> · {html.escape(teacher.full_name)}',
                    bot_token=token,
                )
                return

            if export_kind == 'department-load':
                if 'head' not in roles or not hasattr(user, 'teacher'):
                    self._send(chat_id, '❌ Выгрузка нагрузки кафедры доступна заведующему кафедрой.', token)
                    return
                department = user.teacher.department
                items = LoadItem.objects.filter(
                    discipline__department=department
                ).select_related('discipline', 'group', 'teacher').order_by('discipline', 'kind')
                rows = [
                    (
                        item.discipline.name,
                        item.get_kind_display(),
                        item.group.name if item.group else 'Поток',
                        item.subgroup_no or '',
                        item.hours.quantize(cent),
                        item.teacher.full_name if item.teacher else '',
                    )
                    for item in items
                ]
                data = build_xlsx_bytes(
                    'Нагрузка кафедры',
                    ['Дисциплина', 'Вид работы', 'Группа', 'Подгруппа', 'Часы', 'Преподаватель'],
                    rows,
                )
                telegram_send_document(
                    chat_id,
                    'department_load.xlsx',
                    data,
                    caption=f'🏢 <b>Нагрузка кафедры</b> · {html.escape(department.name)}',
                    bot_token=token,
                )
                return

            if export_kind == 'teachers':
                if 'head' not in roles or not hasattr(user, 'teacher'):
                    self._send(chat_id, '❌ Выгрузка штата кафедры доступна заведующему кафедрой.', token)
                    return
                department = user.teacher.department
                teachers = Teacher.objects.filter(department=department).select_related('position')
                rows = [
                    (
                        t.full_name,
                        t.position.position if t.position else '',
                        t.get_degree_display(),
                        t.rank,
                        t.rate,
                        t.effective_min_hours,
                        t.effective_max_hours,
                        teacher_total(t),
                    )
                    for t in teachers
                ]
                data = build_xlsx_bytes(
                    'Преподаватели',
                    ['ФИО', 'Должность', 'Учёная степень', 'Звание', 'Ставка', 'Мин. часы', 'Макс. часы', 'Назначено часов'],
                    rows,
                )
                telegram_send_document(
                    chat_id,
                    'department_teachers.xlsx',
                    data,
                    caption=f'👥 <b>Преподаватели кафедры</b> · {html.escape(department.name)}',
                    bot_token=token,
                )
                return

            if export_kind == 'report':
                year = date.today().year
                if 'head' in roles and hasattr(user, 'teacher'):
                    dept = user.teacher.department
                    teachers = list(Teacher.objects.filter(department=dept))
                    filename = f'department_report_{year}.xlsx'
                    caption = f'📈 <b>Отчёт План / Факт ({year})</b> · {html.escape(dept.name)}'
                elif hasattr(user, 'teacher'):
                    teachers = [user.teacher]
                    filename = f'workload_report_{year}.xlsx'
                    caption = f'📈 <b>Отчёт План / Факт ({year})</b> · {html.escape(user.teacher.full_name)}'
                else:
                    teachers = list(Teacher.objects.all())
                    filename = f'faculty_report_{year}.xlsx'
                    caption = f'📈 <b>Сводный отчёт План / Факт ({year})</b>'

                matrix = report_matrix(teachers, year)

                def matrix_rows(field):
                    rws = [
                        [row['name']]
                        + [cell[field].quantize(cent) for cell in row['cells']]
                        + [row['total'][field].quantize(cent)]
                        for row in matrix['rows']
                    ]
                    rws.append(
                        ['Итого за год']
                        + [cell[field].quantize(cent) for cell in matrix['totals']['cells']]
                        + [matrix['totals']['total'][field].quantize(cent)]
                    )
                    return rws

                headers = ['Месяц'] + [label for _, label in matrix['kinds']] + ['Всего']
                adjustment_rows = [
                    [
                        MONTH_NAMES[item['month'] - 1], item['teacher'], item['discipline'], item['kind'],
                        item['hours'].quantize(cent), 'Плановая' if item['planned'] else 'Внеплановая', item['reason'],
                    ]
                    for item in matrix['adjustments']
                ]
                data = build_xlsx_bytes(
                    'Факт',
                    headers,
                    matrix_rows('fact'),
                    extra_sheets=[
                        ('План', headers, matrix_rows('plan')),
                        ('Отклонение', headers, matrix_rows('delta')),
                        ('Корректировки', ['Месяц', 'Преподаватель', 'Дисциплина', 'Вид работы', 'Часы', 'Тип', 'Причина'], adjustment_rows),
                    ],
                )
                telegram_send_document(chat_id, filename, data, caption=caption, bot_token=token)
                return

        except Exception as exc:
            self._send(chat_id, f'❌ Ошибка при формировании Excel-файла: {html.escape(str(exc))}', token)

    # ============ РАЗДЕЛ 5: ПРОФИЛЬ И СТАТУС ============

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
            f'• Пользователь: <b>{html.escape(user.first_name or user.username)}</b> (<code>{html.escape(user.username)}</code>)',
            f'• Роль: <b>{role_label}</b>',
        ]

        if hasattr(user, 'teacher'):
            t = user.teacher
            total = teacher_total(t)
            lines.append(f'• Кафедра: <b>{html.escape(t.department.name)}</b>')
            if t.position:
                lines.append(f'• Должность: {html.escape(t.position.position)} (ставка {t.rate})')
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
            lines.append(f'\n🏢 <b>Сводка по кафедре «{html.escape(dept.name)}»:</b>')
            lines.append(f'• Преподавателей: <b>{dept.teachers.count()}</b>')
            lines.append(f'• Элементов нагрузки: <b>{total_items}</b> (нераспределённых: <b>{unassigned}</b>)')

        if getattr(self, 'site_url', ''):
            lines.append(f'\n🌐 Веб-сайт проекта: <a href="{html.escape(self.site_url)}">{html.escape(self.site_url)}</a>')

        self._send(chat_id, '\n'.join(lines), token, self._build_keyboard(webapp_url, True))
