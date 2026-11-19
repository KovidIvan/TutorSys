import html
import re
from decimal import Decimal

from django.conf import settings


CONSENT_TEXT = (
    'Бот будет присылать вам учебные данные: нагрузку, часы и ФИО коллег в пределах вашей роли. '
    'Данные передаются через сервис Telegram. Продолжая, вы соглашаетесь на такую обработку.'
)
LINK_HELP = 'Чтобы подключить аккаунт, войдите на сайт TutorSys, откройте раздел «Telegram» и создайте одноразовый код привязки.'


def escape(value):
    return html.escape(str(value or ''), quote=True)


def button_text(value, max_length=64):
    text = html.unescape(str(value or ''))
    return text if len(text) <= max_length else text[:max_length - 1] + '…'


def display_name(teacher, viewer, *, max_length=None):
    mode = settings.TELEGRAM_PRIVACY_MODE
    if mode == 'minimal':
        return 'Преподаватель'
    if mode != 'masked':
        name = teacher.full_name
    else:
        parts = teacher.full_name.split()
        if not parts:
            return 'Преподаватель'
        initials = ''.join(f'{part[0]}.' for part in parts[1:] if part)
        name = f'{parts[0]} {initials}'.strip()
    if max_length is not None:
        name = name[:max_length]
    return escape(name)


def display_hours(hours, viewer, teacher=None):
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        return 'скрыто'
    value = Decimal(str(hours or 0)).quantize(Decimal('0.01'))
    return format(value.normalize(), 'f')


def format_load_item(item, max_name_length=None, max_group_length=None):
    group = item.group.name if item.group_id else 'Поток'
    if max_group_length is not None and len(group) > max_group_length:
        group = group[:max_group_length - 1] + '…'
    if item.subgroup_no:
        group += f'-{item.subgroup_no}'
    name = item.discipline.name
    if max_name_length is not None and len(name) > max_name_length:
        name = name[:max_name_length - 1] + '…'
    return f'{escape(name)} · {escape(item.get_kind_display())} · {escape(group)}'


def format_workload(workload, teacher, viewer, page=0, page_size=6):
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        return (
            f'📖 <b>Моя нагрузка</b>\nДисциплин: {len(workload["rows"])}\n'
            f'Назначено часов: {display_hours(workload["grand_totals_total"], viewer, teacher)}'
        )
    total_pages = max(1, (len(workload['rows']) + page_size - 1) // page_size)
    lines = [f'📖 <b>Моя нагрузка · {display_name(teacher, viewer, max_length=80)}</b>']
    if total_pages > 1:
        lines.append(f'Страница {page + 1} из {total_pages}')
    for row in workload['rows'][page * page_size:(page + 1) * page_size]:
        name = row['discipline'].name if row['discipline'] else 'Дисциплина'
        if len(name) > 45:
            name = name[:44] + '…'
        name = escape(name)
        groups = str(row['groups'])
        if len(groups) > 40:
            groups = groups[:39] + '…'
        lines.append(f'• {name} — {display_hours(row["total"], viewer, teacher)} ч; {escape(groups)}')
    lines.extend([
        f'🍂 Осень: {display_hours(workload["fall_totals_total"], viewer, teacher)} ч',
        f'🌱 Весна: {display_hours(workload["spring_totals_total"], viewer, teacher)} ч',
        f'<b>Год: {display_hours(workload["grand_totals_total"], viewer, teacher)} ч</b>',
    ])
    return '\n'.join(lines)


def format_report(matrix, viewer, *, teacher=None):
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        return f'📊 <b>Отчёт</b>\nМесяцев: {len(matrix["rows"])}'
    lines = ['📊 <b>План / факт за год</b>', '<pre>Месяц       План     Факт']
    for row in matrix['rows']:
        total = row['total']
        lines.append(f'{row["name"]:<10} {total["plan"]:>7.2f} {total["fact"]:>7.2f}')
    total = matrix['totals']['total']
    marker = '⚪' if not total['has_data'] else ('🔴' if total['status'] == 'under' else '🟢')
    lines.append(f'Итого: план {total["plan"]:.2f} / факт {total["fact"]:.2f} ч {marker}</pre>')
    return '\n'.join(lines)


def initials_name(name):
    parts = name.split()
    if not parts:
        return 'Преподаватель'
    return f'{parts[0]} ' + ''.join(f'{part[0]}.' for part in parts[1:] if part)


def privacy_safe_excel_table(table):
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        return None
    filename, sheet_name, headers, rows, extra_sheets = table
    safe_rows = [list(row) for row in rows]
    safe_sheets = []
    if settings.TELEGRAM_PRIVACY_MODE == 'masked':
        if sheet_name == 'Нагрузка кафедры':
            for row in safe_rows:
                row[5] = initials_name(str(row[5])) if row[5] else ''
        elif sheet_name == 'Преподаватели':
            for row in safe_rows:
                row[0] = initials_name(str(row[0]))
        elif sheet_name == 'Журнал':
            for row in safe_rows:
                row[-1] = ''
        for extra_name, extra_headers, extra_rows in extra_sheets:
            if extra_name == 'Корректировки':
                safe_headers = list(extra_headers[:-1])
                safe_adjustments = []
                for row in extra_rows:
                    safe_row = list(row[:-1])
                    if len(safe_row) > 1:
                        safe_row[1] = initials_name(str(safe_row[1]))
                    safe_adjustments.append(safe_row)
                safe_sheets.append((extra_name, safe_headers, safe_adjustments))
            else:
                safe_sheets.append((extra_name, extra_headers, extra_rows))
    else:
        safe_sheets = list(extra_sheets)
    return filename, sheet_name, headers, safe_rows, safe_sheets


def safe_topic(topic):
    if settings.TELEGRAM_PRIVACY_MODE != 'full':
        return ''
    return escape(topic)
