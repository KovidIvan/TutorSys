from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from core.calculations import report_matrix, teacher_workload
from core.models import JournalEntry, LoadItem, Teacher
from core.services import ServiceError
from core.services.exports import build_xlsx_bytes, report_tables, workload_table
from core.services.journal import add_journal_entry
from core.tgbot.handlers.common import authorized_user
from core.tgbot.keyboards import ActionCallback, ChoiceCallback, MENU_LABELS, inline_buttons
from core.tgbot.states import JournalForm
from core.tgbot.texts import button_text, display_hours, display_name, escape, format_load_item, format_report, format_workload, privacy_safe_excel_table, safe_topic
from core.tgbot.urls import site_link
from core.views import check_teacher_status

router = Router(name='teacher')
PAGE_SIZE = 8


def _get_teacher(user):
    try:
        return user.teacher
    except Teacher.DoesNotExist:
        return None


async def _require_teacher(telegram_user_id, state=None):
    _, user, roles = await authorized_user(telegram_user_id)
    if user is None or 'teacher' not in roles:
        if state is not None:
            await state.clear()
        return None
    return user


def _load_items(teacher, page=0):
    items = list(LoadItem.objects.filter(teacher=teacher).select_related('discipline', 'group').order_by('discipline__name', 'kind', 'group__name', 'subgroup_no'))
    start = page * PAGE_SIZE
    return items[start:start + PAGE_SIZE], len(items)


def _journal_entries(teacher, page=0):
    return list(JournalEntry.objects.filter(teacher=teacher).select_related('load_item__discipline', 'load_item__group').order_by('-date', '-time')[page * 10:page * 10 + 10]), JournalEntry.objects.filter(teacher=teacher).count()


def _short_button_label(label):
    return button_text(label)


def _journal_picker(items, page, total):
    rows = [(_short_button_label(format_load_item(item)), ActionCallback(action='journal_item', item_id=item.pk).pack())
            for item in items]
    rows = [[row] for row in rows]
    nav = []
    if page:
        nav.append(('‹ Назад', ActionCallback(action='journal_items_page', page=page - 1).pack()))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(('Вперёд ›', ActionCallback(action='journal_items_page', page=page + 1).pack()))
    if nav:
        rows.append(nav)
    rows.append([('Отмена', ActionCallback(action='journal_abort').pack())])
    return inline_buttons(rows)


def _date_keyboard():
    return inline_buttons([
        [('Сегодня', ChoiceCallback(action='date_today').pack()), ('Вчера', ChoiceCallback(action='date_yesterday').pack())],
        [('Ввести дату', ChoiceCallback(action='date_manual').pack())],
    ])


def _save_journal_entry(teacher_id, user, data):
    teacher = _get_teacher(user)
    if teacher is None or teacher.pk != teacher_id:
        raise ServiceError('Преподаватель не найден.', code=403)
    return add_journal_entry(
        teacher, data['load_item_id'], data['date'], data['time'], data['hours'], data['topic']
    )


async def _show_workload(message, user, page=0, *, edit=False):
    teacher = await sync_to_async(_get_teacher, thread_sensitive=True)(user)
    if teacher is None:
        await message.answer('Для учётной записи не найден профиль преподавателя.')
        return
    workload = await sync_to_async(teacher_workload, thread_sensitive=True)(teacher)
    _, warnings = await sync_to_async(check_teacher_status, thread_sensitive=True)(teacher)
    total_pages = max(1, (len(workload['rows']) + 5) // 6)
    if page < 0 or page >= total_pages:
        await message.answer('Данные устарели, откройте раздел заново.')
        return
    text = format_workload(workload, teacher, user, page=page)
    if warnings and settings.TELEGRAM_PRIVACY_MODE != 'minimal':
        text += '\n\n⚠️ <b>Предупреждения</b>\n' + '\n'.join(f'• {escape(warning)}' for warning in warnings[:4])
    rows = []
    if total_pages > 1:
        navigation = []
        if page:
            navigation.append(('‹ Назад', ActionCallback(action='workload_page', page=page - 1).pack()))
        if page + 1 < total_pages:
            navigation.append(('Вперёд ›', ActionCallback(action='workload_page', page=page + 1).pack()))
        rows.append(navigation)
    if settings.TELEGRAM_PRIVACY_MODE != 'minimal':
        rows.append([('📥 Excel', ChoiceCallback(action='workload_excel').pack())])
    site_url = site_link('/workload/')
    if site_url:
        rows.append([('🌐 Открыть на сайте', site_url)])
    markup = inline_buttons(rows)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text == MENU_LABELS['workload'])
async def workload_menu(message: Message):
    _, user, roles = await authorized_user(message.from_user.id)
    if user is None:
        await message.answer('Сначала привяжите аккаунт через сайт TutorSys.')
    elif 'teacher' not in roles:
        await message.answer('Доступно только преподавателю.')
    else:
        await _show_workload(message, user)


@router.callback_query(ActionCallback.filter(F.action == 'workload_page'))
async def workload_page(callback: CallbackQuery, callback_data: ActionCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'teacher' not in roles:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    await _show_workload(callback.message, user, callback_data.page, edit=True)
    await callback.answer()


@router.callback_query(ChoiceCallback.filter(F.action == 'workload_excel'))
async def workload_excel(callback: CallbackQuery):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'teacher' not in roles:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    teacher = await sync_to_async(_get_teacher, thread_sensitive=True)(user)
    if teacher is None:
        await callback.answer('Профиль преподавателя не найден.', show_alert=True)
        return
    table = await sync_to_async(workload_table, thread_sensitive=True)(teacher)
    table = privacy_safe_excel_table(table)
    if table is None:
        await callback.answer('Экспорт с персональными данными отключён в режиме приватности minimal.', show_alert=True)
        return
    filename, sheet_name, headers, rows, extra_sheets = table
    content = await sync_to_async(build_xlsx_bytes, thread_sensitive=True)(sheet_name, headers, rows, extra_sheets)
    await callback.message.answer_document(BufferedInputFile(content, filename=filename))
    await callback.answer()


@router.message(F.text == MENU_LABELS['journal_add'])
async def journal_add(message: Message, state: FSMContext):
    _, user, roles = await authorized_user(message.from_user.id)
    if user is None or 'teacher' not in roles:
        await message.answer('Добавление записи доступно привязанному преподавателю.')
        return
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await message.answer('Добавление записи недоступно в режиме приватности minimal; используйте сайт TutorSys.')
        return
    teacher = await sync_to_async(_get_teacher, thread_sensitive=True)(user)
    if teacher is None:
        await message.answer('Профиль преподавателя не найден.')
        return
    items, total = await sync_to_async(_load_items, thread_sensitive=True)(teacher, 0)
    if not total:
        await message.answer('Сначала заведующий должен назначить вам элементы нагрузки.')
        return
    await state.clear()
    await state.update_data(teacher_id=teacher.pk)
    await state.set_state(JournalForm.load_item)
    await message.answer('Выберите элемент нагрузки:', reply_markup=_journal_picker(items, 0, total))


@router.callback_query(ActionCallback.filter(F.action == 'journal_items_page'))
async def journal_items_page(callback: CallbackQuery, callback_data: ActionCallback, state: FSMContext):
    _, user, roles = await authorized_user(callback.from_user.id)
    if (callback_data.page < 0 or user is None or 'teacher' not in roles
            or await state.get_state() != JournalForm.load_item.state):
        await callback.answer('Данные устарели, откройте раздел заново.', show_alert=True)
        return
    teacher = await sync_to_async(_get_teacher, thread_sensitive=True)(user)
    if teacher is None:
        await callback.answer('Профиль преподавателя не найден.', show_alert=True)
        return
    items, total = await sync_to_async(_load_items, thread_sensitive=True)(teacher, callback_data.page)
    if callback_data.page * PAGE_SIZE >= total:
        await callback.answer('Данные устарели, откройте раздел заново.', show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=_journal_picker(items, callback_data.page, total))
    await callback.answer()


@router.callback_query(ActionCallback.filter(F.action == 'journal_item'))
async def journal_item(callback: CallbackQuery, callback_data: ActionCallback, state: FSMContext):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'teacher' not in roles or await state.get_state() != JournalForm.load_item.state:
        await callback.answer('Данные устарели, откройте раздел заново.', show_alert=True)
        return
    teacher = await sync_to_async(_get_teacher, thread_sensitive=True)(user)
    item = await sync_to_async(lambda: LoadItem.objects.filter(pk=callback_data.item_id, teacher=teacher).select_related('discipline', 'group').first(), thread_sensitive=True)()
    if item is None:
        await callback.answer('Данные устарели, откройте раздел заново.', show_alert=True)
        return
    await state.update_data(load_item_id=item.pk)
    await state.set_state(JournalForm.entry_date)
    await callback.message.edit_text(f'Элемент: {format_load_item(item)}\nВыберите дату:', reply_markup=_date_keyboard())
    await callback.answer()


@router.callback_query(ActionCallback.filter(F.action == 'journal_abort'))
async def journal_abort(callback: CallbackQuery, state: FSMContext):
    if await _require_teacher(callback.from_user.id, state) is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text('Добавление записи отменено.')
    await callback.answer()


async def _set_entry_date(event, state, entry_date):
    await state.update_data(date=entry_date.isoformat())
    await state.set_state(JournalForm.entry_time)
    await event.answer(f'Дата: {entry_date:%d.%m.%Y}\nВведите время начала в формате ЧЧ:ММ или нажмите «Пропустить».',
                       reply_markup=inline_buttons([[('Пропустить', ChoiceCallback(action='time_skip').pack())]]))


@router.callback_query(ChoiceCallback.filter(F.action == 'date_today'))
async def date_today(callback: CallbackQuery, state: FSMContext):
    if await _require_teacher(callback.from_user.id, state) is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    if await state.get_state() != JournalForm.entry_date.state:
        await callback.answer('Данные устарели.', show_alert=True)
        return
    await callback.answer()
    await _set_entry_date(callback.message, state, timezone.localdate())


@router.callback_query(ChoiceCallback.filter(F.action == 'date_yesterday'))
async def date_yesterday(callback: CallbackQuery, state: FSMContext):
    if await _require_teacher(callback.from_user.id, state) is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    if await state.get_state() != JournalForm.entry_date.state:
        await callback.answer('Данные устарели.', show_alert=True)
        return
    await callback.answer()
    await _set_entry_date(callback.message, state, timezone.localdate() - timedelta(days=1))


@router.callback_query(ChoiceCallback.filter(F.action == 'date_manual'))
async def date_manual(callback: CallbackQuery, state: FSMContext):
    if await _require_teacher(callback.from_user.id, state) is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    if await state.get_state() != JournalForm.entry_date.state:
        await callback.answer('Данные устарели.', show_alert=True)
        return
    await callback.answer()
    await callback.message.answer('Введите дату в формате ДД.ММ.ГГГГ:')


@router.message(JournalForm.entry_date, F.text & ~F.text.startswith('/'))
async def date_input(message: Message, state: FSMContext):
    if await _require_teacher(message.from_user.id, state) is None:
        await message.answer('Доступно только привязанному преподавателю.')
        return
    try:
        entry_date = datetime.strptime((message.text or '').strip(), '%d.%m.%Y').date()
    except ValueError:
        await message.answer('Неверная дата. Введите дату в формате ДД.ММ.ГГГГ или выберите Сегодня / Вчера.')
        return
    await state.update_data(date=entry_date.isoformat())
    await state.set_state(JournalForm.entry_time)
    await message.answer(f'Дата: {entry_date:%d.%m.%Y}\nВведите время начала в формате ЧЧ:ММ или нажмите «Пропустить».',
                         reply_markup=inline_buttons([[('Пропустить', ChoiceCallback(action='time_skip').pack())]]))


@router.callback_query(ChoiceCallback.filter(F.action == 'time_skip'))
async def time_skip(callback: CallbackQuery, state: FSMContext):
    if await _require_teacher(callback.from_user.id, state) is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    if await state.get_state() != JournalForm.entry_time.state:
        await callback.answer('Данные устарели.', show_alert=True)
        return
    await state.update_data(time='')
    await state.set_state(JournalForm.hours)
    await callback.message.answer('Введите количество часов (например, 1,5):')
    await callback.answer()


@router.message(JournalForm.entry_time, F.text & ~F.text.startswith('/'))
async def time_input(message: Message, state: FSMContext):
    if await _require_teacher(message.from_user.id, state) is None:
        await message.answer('Доступно только привязанному преподавателю.')
        return
    raw = (message.text or '').strip()
    try:
        datetime.strptime(raw, '%H:%M')
    except ValueError:
        await message.answer('Неверное время. Введите ЧЧ:ММ или нажмите «Пропустить».')
        return
    await state.update_data(time=raw)
    await state.set_state(JournalForm.hours)
    await message.answer('Введите количество часов (например, 1,5):')


@router.message(JournalForm.hours, F.text & ~F.text.startswith('/'))
async def hours_input(message: Message, state: FSMContext):
    if await _require_teacher(message.from_user.id, state) is None:
        await message.answer('Доступно только привязанному преподавателю.')
        return
    raw = (message.text or '').strip().replace(',', '.')
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        value = Decimal('NaN')
    if not value.is_finite() or value <= 0 or value > Decimal('999.99') or value.as_tuple().exponent < -2:
        await message.answer('Укажите конечное число часов больше 0 и не более 999,99, максимум два знака после запятой.')
        return
    await state.update_data(hours=format(value, 'f'))
    await state.set_state(JournalForm.topic)
    await message.answer('Введите тему занятия (до 1000 символов):')


@router.message(JournalForm.topic, F.text & ~F.text.startswith('/'))
async def topic_input(message: Message, state: FSMContext):
    if await _require_teacher(message.from_user.id, state) is None:
        await message.answer('Доступно только привязанному преподавателю.')
        return
    topic = (message.text or '').strip()
    if not topic or len(topic) > 1000:
        await message.answer('Тема должна быть непустой и содержать не более 1000 символов.')
        return
    await state.update_data(topic=topic)
    data = await state.get_data()
    item = await sync_to_async(LoadItem.objects.select_related('discipline', 'group').get, thread_sensitive=True)(pk=data['load_item_id'])
    shown_topic = safe_topic(topic) or 'Тема скрыта настройкой приватности'
    await state.set_state(JournalForm.confirm)
    await message.answer(
        f'<b>Проверьте запись</b>\n{format_load_item(item)}\nДата: {data["date"]}\n'
        f'Время: {escape(data["time"] or "не указано")}\nЧасы: {escape(data["hours"])}\nТема: {shown_topic}',
        reply_markup=inline_buttons([[('Сохранить', ChoiceCallback(action='journal_save').pack()),
                                      ('Отмена', ChoiceCallback(action='journal_cancel').pack())]])
    )


@router.callback_query(ChoiceCallback.filter(F.action == 'journal_cancel'))
async def journal_cancel(callback: CallbackQuery, state: FSMContext):
    if await _require_teacher(callback.from_user.id, state) is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text('Запись не сохранена.')
    await callback.answer()


@router.callback_query(ChoiceCallback.filter(F.action == 'journal_save'))
async def journal_save(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != JournalForm.confirm.state:
        await callback.answer('Данные устарели, запись уже обработана.', show_alert=True)
        return
    _, user, roles = await authorized_user(callback.from_user.id)
    data = await state.get_data()
    await state.clear()
    if user is None or 'teacher' not in roles:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    try:
        entry = await sync_to_async(_save_journal_entry, thread_sensitive=True)(data['teacher_id'], user, data)
    except ServiceError as exc:
        await callback.message.edit_text(f'Не удалось добавить запись: {escape(exc.message)}')
        await callback.answer()
        return
    await callback.message.edit_text(f'Запись добавлена: {entry.date:%d.%m.%Y}, {display_hours(entry.hours, user)} ч.')
    await callback.answer('Сохранено')


@router.message(F.text == MENU_LABELS['journal'])
async def journal_menu(message: Message):
    _, user, roles = await authorized_user(message.from_user.id)
    if user is None or 'teacher' not in roles:
        await message.answer('Доступно только привязанному преподавателю.')
        return
    teacher = await sync_to_async(_get_teacher, thread_sensitive=True)(user)
    if teacher is None:
        await message.answer('Профиль преподавателя не найден.')
        return
    entries, total = await sync_to_async(_journal_entries, thread_sensitive=True)(teacher, 0)
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await message.answer(f'🗒 <b>Мой журнал</b>\nЗаписей: {total}')
        return
    if not entries:
        await message.answer('Записей в журнале пока нет.')
        return
    await message.answer(_format_journal(entries, total, 0, user), reply_markup=_journal_pagination(total, 0))


def _format_journal(entries, total, page, user):
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        return f'🗒 <b>Мой журнал</b>\nЗаписей: {total} · Страница {page + 1}'

    def render(field_limit):
        lines = [f'🗒 <b>Мой журнал · страница {page + 1}</b>']
        for entry in entries:
            load_item = format_load_item(
                entry.load_item, max_name_length=field_limit, max_group_length=field_limit,
            )
            lines.append(f'{entry.date:%d.%m.%Y} · {load_item} · {display_hours(entry.hours, user)} ч')
            topic = safe_topic(entry.topic[:field_limit])
            if topic:
                lines.append(f'  {topic}')
        return '\n'.join(lines)

    for field_limit in (120, 80, 60, 40, 20, 10):
        text = render(field_limit)
        if len(text) <= 3900:
            return text
    return render(10)


def _journal_pagination(total, page):
    rows = []
    nav = []
    if page:
        nav.append(('‹ Назад', ActionCallback(action='journal_page', page=page - 1).pack()))
    if (page + 1) * 10 < total:
        nav.append(('Вперёд ›', ActionCallback(action='journal_page', page=page + 1).pack()))
    if nav:
        rows.append(nav)
    return inline_buttons(rows) if rows else None


@router.callback_query(ActionCallback.filter(F.action == 'journal_page'))
async def journal_page(callback: CallbackQuery, callback_data: ActionCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    if callback_data.page < 0 or user is None or 'teacher' not in roles:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    teacher = await sync_to_async(_get_teacher, thread_sensitive=True)(user)
    if teacher is None:
        await callback.answer('Профиль преподавателя не найден.', show_alert=True)
        return
    entries, total = await sync_to_async(_journal_entries, thread_sensitive=True)(teacher, callback_data.page)
    if not entries:
        await callback.answer('Данные устарели, откройте журнал заново.', show_alert=True)
        return
    await callback.message.edit_text(_format_journal(entries, total, callback_data.page, user),
                                     reply_markup=_journal_pagination(total, callback_data.page))
    await callback.answer()


@router.message(F.text == MENU_LABELS['report'])
async def report_menu(message: Message):
    _, user, roles = await authorized_user(message.from_user.id)
    if user is None or 'teacher' not in roles:
        await message.answer('Доступно только привязанному преподавателю.')
        return
    await _send_report(message, user, date.today().year)


def _report_buttons(year):
    rows = []
    navigation = []
    if year > 1900:
        navigation.append(('Год ◀', ChoiceCallback(action='report_year', object_id=year - 1).pack()))
    if year < 2100:
        navigation.append(('Год ▶', ChoiceCallback(action='report_year', object_id=year + 1).pack()))
    if navigation:
        rows.append(navigation)
    if settings.TELEGRAM_PRIVACY_MODE != 'minimal':
        rows.append([('📥 Excel', ChoiceCallback(action='report_excel', object_id=year).pack())])
    site_url = site_link('/report/')
    if site_url:
        rows.append([('🌐 Открыть на сайте', site_url)])
    return inline_buttons(rows)


async def _send_report(message, user, year):
    teacher = await sync_to_async(_get_teacher, thread_sensitive=True)(user)
    if teacher is None:
        await message.answer('Профиль преподавателя не найден.')
        return
    matrix = await sync_to_async(report_matrix, thread_sensitive=True)([teacher], year)
    markup = _report_buttons(year)
    text = format_report(matrix, user, teacher=teacher)
    await message.answer(f'<b>{year}</b>\n{text}', reply_markup=markup)


@router.callback_query(ChoiceCallback.filter(F.action == 'report_year'))
async def report_year(callback: CallbackQuery, callback_data: ChoiceCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'teacher' not in roles:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    if not 1900 <= callback_data.object_id <= 2100:
        await callback.answer('Некорректный год.', show_alert=True)
        return
    teacher = await sync_to_async(_get_teacher, thread_sensitive=True)(user)
    if teacher is None:
        await callback.answer('Профиль преподавателя не найден.', show_alert=True)
        return
    matrix = await sync_to_async(report_matrix, thread_sensitive=True)([teacher], callback_data.object_id)
    await callback.message.edit_text(f'<b>{callback_data.object_id}</b>\n{format_report(matrix, user, teacher=teacher)}',
                                     reply_markup=_report_buttons(callback_data.object_id))
    await callback.answer()


@router.callback_query(ChoiceCallback.filter(F.action == 'report_excel'))
async def report_excel(callback: CallbackQuery, callback_data: ChoiceCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'teacher' not in roles:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    if not 1900 <= callback_data.object_id <= 2100:
        await callback.answer('Некорректный год.', show_alert=True)
        return
    teacher = await sync_to_async(_get_teacher, thread_sensitive=True)(user)
    if teacher is None:
        await callback.answer('Профиль преподавателя не найден.', show_alert=True)
        return
    table = await sync_to_async(report_tables, thread_sensitive=True)([teacher], callback_data.object_id, True)
    table = privacy_safe_excel_table(table)
    if table is None:
        await callback.answer('Экспорт с персональными данными отключён в режиме приватности minimal.', show_alert=True)
        return
    filename, sheet_name, headers, rows, extra_sheets = table
    content = await sync_to_async(build_xlsx_bytes, thread_sensitive=True)(sheet_name, headers, rows, extra_sheets)
    await callback.message.answer_document(BufferedInputFile(content, filename=filename))
    await callback.answer()
