from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


class ActionCallback(CallbackData, prefix='a'):
    action: str
    item_id: int = 0
    page: int = 0


class ChoiceCallback(CallbackData, prefix='c'):
    action: str
    object_id: int = 0


class ReportCallback(CallbackData, prefix='r'):
    action: str
    teacher_id: int = 0
    year: int = 0
    page: int = 0


MENU_LABELS = {
    'workload': '📖 Моя нагрузка',
    'journal_add': '📝 Добавить запись в журнал',
    'journal': '🗒 Мой журнал',
    'report': '📊 Мой отчёт',
    'teachers': '👥 Преподаватели',
    'distribution': '🧩 Распределение',
    'department_report': '📊 Отчёт кафедры',
    'recalculate': '🔄 Пересчитать нагрузку',
    'exports': '📤 Экспорт',
    'dean': '🏛 Сводка',
    'dean_recalculate': '🔄 Пересчитать кафедру',
    'import': '📥 Импорт Excel',
    'menu': '⬅️ Меню',
}


def main_menu(roles):
    keys = []
    if 'teacher' in roles:
        keys.extend([MENU_LABELS['workload'], MENU_LABELS['journal_add'], MENU_LABELS['journal'], MENU_LABELS['report']])
    if 'head' in roles:
        keys.extend([MENU_LABELS['teachers'], MENU_LABELS['distribution'], MENU_LABELS['department_report'], MENU_LABELS['recalculate'], MENU_LABELS['exports']])
    if 'deputy_dean' in roles:
        keys.extend([MENU_LABELS['dean'], MENU_LABELS['dean_recalculate'], MENU_LABELS['import']])
    rows = [[KeyboardButton(text=key)] for key in keys]
    rows.append([KeyboardButton(text='/help'), KeyboardButton(text='/whoami'), KeyboardButton(text='/unlink'), KeyboardButton(text='/notify off')])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def inline_buttons(rows):
    buttons = []
    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                text=text,
                url=data if data.startswith(('https://', 'http://')) else None,
                callback_data=None if data.startswith(('https://', 'http://')) else data,
            )
            for text, data in row
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def paginated_buttons(action, ids, page, page_size=8, labeler=str):
    start = page * page_size
    page_ids = ids[start:start + page_size]
    rows = [[(labeler(item), ActionCallback(action=action, item_id=item.pk, page=page).pack())]
            for item in page_ids]
    navigation = []
    if page > 0:
        navigation.append(('‹ Назад', ActionCallback(action=f'{action}_page', page=page - 1).pack()))
    if start + page_size < len(ids):
        navigation.append(('Вперёд ›', ActionCallback(action=f'{action}_page', page=page + 1).pack()))
    if navigation:
        rows.append(navigation)
    return inline_buttons(rows)
