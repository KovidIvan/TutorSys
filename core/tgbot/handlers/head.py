from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import Http404

from core.calculations import recalculate_department, report_matrix, teacher_total
from core.models import CalcSettings, Department, LoadItem, Specialty, Teacher
from core.services import ServiceError
from core.services.distribution import assign_load_item
from core.services.exports import build_xlsx_bytes, department_load_table, report_tables, teachers_table
from core.tgbot.auth import valid_head_department
from core.tgbot.handlers.common import authorized_user
from core.tgbot.keyboards import ActionCallback, ChoiceCallback, MENU_LABELS, ReportCallback, inline_buttons, main_menu
from core.tgbot.texts import button_text, display_hours, display_name, escape, format_report, privacy_safe_excel_table
from core.tgbot.urls import site_link
from core.views import _teacher_panel_data, check_teacher_status

router = Router(name='head')
PAGE_SIZE = 8


def _department(user):
    return valid_head_department(user)


def _teacher_rows(department):
    return _teacher_panel_data(department)


def _assigned_items(department, page=0, filters=None):
    query = LoadItem.objects.filter(teacher__isnull=False, discipline__department=department).select_related(
        'discipline__curriculum__specialty', 'group', 'teacher'
    )
    filters = filters or {}
    if filters.get('specialty_id'):
        query = query.filter(discipline__curriculum__specialty_id=filters['specialty_id'])
    if filters.get('semester'):
        query = query.filter(discipline__semester=filters['semester'])
    if filters.get('kind'):
        query = query.filter(kind=filters['kind'])
    query = query.order_by('discipline__curriculum__specialty__code', 'discipline__semester', 'discipline__name', 'kind')
    total = query.count()
    return list(query[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]), total


def _undistributed(department, page=0, filters=None):
    query = LoadItem.objects.filter(teacher__isnull=True, discipline__department=department).select_related(
        'discipline__curriculum__specialty', 'group'
    )
    filters = filters or {}
    if filters.get('specialty_id'):
        query = query.filter(discipline__curriculum__specialty_id=filters['specialty_id'])
    if filters.get('semester'):
        query = query.filter(discipline__semester=filters['semester'])
    if filters.get('kind'):
        query = query.filter(kind=filters['kind'])
    query = query.order_by('discipline__curriculum__specialty__code', 'discipline__semester', 'discipline__name', 'kind')
    total = query.count()
    return list(query[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]), total


def _distribution_filter_choices(department, category):
    load_items = LoadItem.objects.filter(discipline__department=department)
    if category == 1:
        ids = load_items.order_by().values_list('discipline__curriculum__specialty_id', flat=True).distinct()
        return list(Specialty.objects.filter(pk__in=ids).order_by('code'))
    if category == 2:
        return list(load_items.order_by().values_list('discipline__semester', flat=True).distinct().order_by('discipline__semester'))
    if category == 3:
        kinds = set(load_items.order_by().values_list('kind', flat=True).distinct())
        return [(value, label) for value, label in LoadItem.KIND_CHOICES if value in kinds]
    return []


def _filter_category_label(category):
    return {1: 'специальности', 2: 'семестр', 3: 'вид работы'}.get(category, 'фильтр')


def _distribution_filter_rows(choices, category, page):
    page_size = 8
    start = page * page_size
    rows = [[('Все', ActionCallback(action='distribution_filter_value', item_id=0, page=category).pack())]]
    for choice in choices[start:start + page_size]:
        if category == 1:
            item_id, label = choice.pk, f'{choice.code} · {choice.abbr}'
        elif category == 2:
            item_id, label = choice, f'{choice} семестр'
        else:
            item_id, label = next(index for index, (kind, _) in enumerate(LoadItem.KIND_CHOICES, 1) if kind == choice[0]), choice[1]
        rows.append([(_short_button_label(label), ActionCallback(action='distribution_filter_value', item_id=item_id, page=category).pack())])
    navigation = []
    if page:
        navigation.append(('‹ Назад', ActionCallback(action='distribution_filter_options', item_id=category, page=page - 1).pack()))
    if start + page_size < len(choices):
        navigation.append(('Вперёд ›', ActionCallback(action='distribution_filter_options', item_id=category, page=page + 1).pack()))
    if navigation:
        rows.append(navigation)
    return rows


def _teachers_for_item(department, item_id):
    item = LoadItem.objects.filter(pk=item_id, discipline__department=department).select_related('discipline').first()
    if item is None:
        return None, []
    teachers = list(Teacher.objects.filter(department=department).order_by('full_name'))
    specialized_ids = set(Teacher.objects.filter(
        department=department, specialization__pk=item.discipline_id
    ).values_list('pk', flat=True))
    choices = [(teacher, teacher.pk in specialized_ids) for teacher in teachers]
    choices.sort(key=lambda pair: (not pair[1], pair[0].full_name.casefold()))
    return item, choices


async def _show_distribution_item(message, user, department, item_id, page=0, *, edit=False):
    item, candidates = await sync_to_async(_teachers_for_item, thread_sensitive=True)(department, item_id)
    if item is None or item.teacher_id is not None:
        await message.answer('Данные устарели, откройте раздел заново.')
        return
    page_size = 8
    total_pages = max(1, (len(candidates) + page_size - 1) // page_size)
    if page < 0 or page >= total_pages:
        await message.answer('Список преподавателей устарел, откройте элемент заново.')
        return
    rows = []
    for teacher, specialized in candidates[page * page_size:(page + 1) * page_size]:
        mark = '✓ ' if specialized else ''
        label = _short_button_label(f'{mark}{display_name(teacher, user)}')
        rows.append([(label, ActionCallback(action='assign', item_id=item.pk, page=teacher.pk).pack())])
    nav = []
    if page:
        nav.append(('‹ Назад', ActionCallback(action='distribution_candidates', item_id=item.pk, page=page - 1).pack()))
    if page + 1 < total_pages:
        nav.append(('Вперёд ›', ActionCallback(action='distribution_candidates', item_id=item.pk, page=page + 1).pack()))
    if nav:
        rows.append(nav)
    rows.append([('‹ К нераспределённым', ActionCallback(action='distribution_page', page=0).pack())])
    title = f'{_format_load_item(item)} · преподаватели {page + 1}/{total_pages}'
    if edit:
        await message.edit_text(title, reply_markup=inline_buttons(rows))
    else:
        await message.answer(title, reply_markup=inline_buttons(rows))


def _format_load_item(item):
    group = item.group.name if item.group_id else 'Поток'
    if item.subgroup_no:
        group += f'-{item.subgroup_no}'
    return f'{escape(item.discipline.name)} · {escape(item.get_kind_display())} · {escape(group)}'


def _short_button_label(label):
    return button_text(label)


def _assign(actor, item_id, teacher_id):
    department = valid_head_department(actor)
    previous_teacher_id = LoadItem.objects.filter(
        pk=item_id, discipline__department=department
    ).values_list('teacher_id', flat=True).first()
    item = assign_load_item(actor, item_id, teacher_id or None)
    affected_ids = sorted({teacher_id, previous_teacher_id} - {None, 0})
    statuses = [
        data for data in (_teacher_detail(pk, department) for pk in affected_ids)
        if data is not None
    ]
    return item, statuses


def _recalculate(department_id):
    department = Department.objects.get(pk=department_id)
    recalculate_department(department, CalcSettings.get())
    return department.name


def _teacher_detail(teacher_id, department):
    teacher = Teacher.objects.filter(pk=teacher_id, department=department).select_related('position').first()
    if teacher is None:
        return None
    status, warnings = check_teacher_status(teacher)
    return {
        'teacher': teacher,
        'status': status,
        'warnings': warnings,
        'total_hours': teacher_total(teacher),
    }


async def _show_teachers(message, user, department, page=0, *, edit=False):
    data = await sync_to_async(_teacher_rows, thread_sensitive=True)(department)
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        counts = {key: sum(row['status'] == key for row in data) for key in ('ok', 'warning', 'critical')}
        text = f'👥 <b>Преподаватели</b>\nВсего: {len(data)}\n🟢 {counts["ok"]} · 🟡 {counts["warning"]} · 🔴 {counts["critical"]}'
        markup = None
    else:
        page_size = 8
        total_pages = max(1, (len(data) + page_size - 1) // page_size)
        if page < 0 or page >= total_pages:
            await message.answer('Данные устарели, откройте раздел заново.')
            return
        icons = {'ok': '🟢', 'warning': '🟡', 'critical': '🔴'}
        rows = []
        for entry in data[page * page_size:(page + 1) * page_size]:
            teacher = entry['teacher']
            label = (f'{icons[entry["status"]]} {display_name(teacher, user)} · '
                     f'{display_hours(entry["total_hours"], user, teacher)}/'
                     f'{display_hours(teacher.effective_max_hours, user, teacher)} ч')
            rows.append([(_short_button_label(label), ActionCallback(action='teacher_card', item_id=teacher.pk, page=page).pack())])
        nav = []
        if page:
            nav.append(('‹ Назад', ActionCallback(action='teacher_page', page=page - 1).pack()))
        if page + 1 < total_pages:
            nav.append(('Вперёд ›', ActionCallback(action='teacher_page', page=page + 1).pack()))
        if nav:
            rows.append(nav)
        markup = inline_buttons(rows) if rows else None
        text = f'👥 <b>Преподаватели · страница {page + 1}/{total_pages}</b>'
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


def _report_teachers(department, teacher_id=None):
    teachers = Teacher.objects.filter(department=department).select_related('position').order_by('full_name')
    if teacher_id:
        teachers = teachers.filter(pk=teacher_id)
    return teachers


@router.message(F.text == MENU_LABELS['teachers'])
async def teachers_menu(message: Message):
    _, user, roles = await authorized_user(message.from_user.id)
    if user is None:
        await message.answer('Сначала привяжите аккаунт через сайт TutorSys.')
        return
    if 'head' not in roles:
        await message.answer(
            'У связанной учётной записи сейчас нет роли заведующего кафедрой. '
            'Проверьте аккаунт командой /whoami; клавиатура меню обновлена.',
            reply_markup=main_menu(roles),
        )
        return
    department = await sync_to_async(_department, thread_sensitive=True)(user)
    if department is None:
        await message.answer('Для вашей учётной записи не указана кафедра.')
        return
    await _show_teachers(message, user, department)


@router.callback_query(ActionCallback.filter(F.action == 'teacher_page'))
async def teacher_page(callback: CallbackQuery, callback_data: ActionCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    await _show_teachers(callback.message, user, department, callback_data.page, edit=True)
    await callback.answer()


@router.callback_query(ActionCallback.filter(F.action == 'teacher_card'))
async def teacher_card(callback: CallbackQuery, callback_data: ActionCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await callback.answer('Подробности скрыты настройкой приватности.', show_alert=True)
        return
    data = await sync_to_async(_teacher_detail, thread_sensitive=True)(callback_data.item_id, department)
    if data is None:
        await callback.answer('Данные устарели, откройте раздел заново.', show_alert=True)
        return
    teacher = data['teacher']
    icons = {'ok': '🟢', 'warning': '🟡', 'critical': '🔴'}
    text = (
        f'{icons[data["status"]]} <b>{display_name(teacher, user)}</b>\n'
        f'Назначено: {display_hours(data["total_hours"], user, teacher)} ч\n'
        f'Норма: {display_hours(teacher.effective_min_hours, user, teacher)}–'
        f'{display_hours(teacher.effective_max_hours, user, teacher)} ч'
    )
    if data['warnings']:
        text += '\n\n⚠️ <b>Предупреждения</b>\n' + '\n'.join(f'• {escape(warning)}' for warning in data['warnings'][:8])
    await callback.message.edit_text(text, reply_markup=inline_buttons([
        [('‹ К преподавателям', ActionCallback(action='teacher_page', page=callback_data.page).pack())]
    ]))
    await callback.answer()


@router.message(F.text == MENU_LABELS['distribution'])
async def distribution_menu(message: Message, state: FSMContext):
    _, user, roles = await authorized_user(message.from_user.id)
    if user is None:
        await message.answer('Сначала привяжите аккаунт через сайт TutorSys.')
        return
    if 'head' not in roles:
        await message.answer(
            'У связанной учётной записи сейчас нет роли заведующего кафедрой. '
            'Проверьте аккаунт командой /whoami; клавиатура меню обновлена.',
            reply_markup=main_menu(roles),
        )
        return
    department = await sync_to_async(_department, thread_sensitive=True)(user)
    if department is None:
        await message.answer('Для учётной записи не указана кафедра.')
        return
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await message.answer('Подробное распределение отключено в режиме приватности minimal; используйте сайт TutorSys.')
        return
    await state.update_data(distribution_filters={'specialty_id': None, 'semester': None, 'kind': None})
    await _show_unassigned(message, department, 0, state=state)


async def _show_unassigned(message, department, page, *, state, edit=False):
    data = await state.get_data()
    filters = data.get('distribution_filters') or {'specialty_id': None, 'semester': None, 'kind': None}
    items, total = await sync_to_async(_undistributed, thread_sensitive=True)(department, page, filters)
    rows = [[(_short_button_label(_format_load_item(item)), ActionCallback(action='distribution_item', item_id=item.pk, page=page).pack())]
            for item in items]
    rows.append([('⚙️ Фильтры', ActionCallback(action='distribution_filters').pack())])
    rows.append([('Назначенные элементы', ActionCallback(action='distribution_assigned').pack())])
    site_url = site_link('/distribution/')
    if site_url:
        rows.append([('🌐 Открыть на сайте', site_url)])
    if any(filters.values()):
        rows.append([('Сбросить фильтры', ActionCallback(action='distribution_filter_clear').pack())])
    nav = []
    if page:
        nav.append(('‹ Назад', ActionCallback(action='distribution_page', page=page - 1).pack()))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(('Вперёд ›', ActionCallback(action='distribution_page', page=page + 1).pack()))
    if nav:
        rows.append(nav)
    text = f'🧩 Нераспределённая нагрузка · {total}' if total else 'Нераспределённых элементов по этим фильтрам нет.'
    markup = inline_buttons(rows)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(ActionCallback.filter(F.action == 'distribution_page'))
async def distribution_page(callback: CallbackQuery, callback_data: ActionCallback, state: FSMContext):
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await callback.answer('Распределение отключено в режиме приватности minimal.', show_alert=True)
        return
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    if callback_data.page < 0:
        await callback.answer('Некорректная страница.', show_alert=True)
        return
    await _show_unassigned(callback.message, department, callback_data.page, state=state, edit=True)
    await callback.answer()


@router.callback_query(ActionCallback.filter(F.action == 'distribution_assigned'))
async def distribution_assigned(callback: CallbackQuery, state: FSMContext):
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await callback.answer('Распределение отключено в режиме приватности minimal.', show_alert=True)
        return
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    await _show_assigned(callback.message, department, 0, state=state, edit=True)
    await callback.answer()


async def _show_assigned(message, department, page, *, state, edit=False):
    if page < 0:
        await message.answer('Некорректная страница.')
        return
    data = await state.get_data()
    filters = data.get('distribution_filters') or {'specialty_id': None, 'semester': None, 'kind': None}
    items, total = await sync_to_async(_assigned_items, thread_sensitive=True)(department, page, filters)
    rows = [[(
        _short_button_label(f'{_format_load_item(item)} · {display_name(item.teacher, None)}'),
        ActionCallback(action='distribution_unassign', item_id=item.pk, page=page).pack(),
    )] for item in items]
    nav = []
    if page:
        nav.append(('‹ Назад', ActionCallback(action='distribution_assigned_page', page=page - 1).pack()))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(('Вперёд ›', ActionCallback(action='distribution_assigned_page', page=page + 1).pack()))
    if nav:
        rows.append(nav)
    rows.append([('⚙️ Фильтры', ActionCallback(action='distribution_filters').pack())])
    if any(filters.values()):
        rows.append([('Сбросить фильтры', ActionCallback(action='distribution_filter_clear').pack())])
    rows.append([('‹ К нераспределённым', ActionCallback(action='distribution_page', page=0).pack())])
    site_url = site_link('/distribution/')
    if site_url:
        rows.append([('🌐 Открыть на сайте', site_url)])
    text = f'Назначенные элементы · {total}' if total else 'Назначенных элементов по этим фильтрам нет.'
    markup = inline_buttons(rows)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(ActionCallback.filter(F.action == 'distribution_assigned_page'))
async def distribution_assigned_page(callback: CallbackQuery, callback_data: ActionCallback, state: FSMContext):
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await callback.answer('Распределение отключено в режиме приватности minimal.', show_alert=True)
        return
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None or callback_data.page < 0:
        await callback.answer('Нет доступа или некорректная страница.', show_alert=True)
        return
    await _show_assigned(callback.message, department, callback_data.page, state=state, edit=True)
    await callback.answer()


@router.callback_query(ActionCallback.filter(F.action == 'distribution_unassign'))
async def distribution_unassign(callback: CallbackQuery, callback_data: ActionCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'head' not in roles:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await callback.answer('Распределение отключено в режиме приватности minimal.', show_alert=True)
        return
    try:
        item, statuses = await sync_to_async(_assign, thread_sensitive=True)(user, callback_data.item_id, 0)
    except ServiceError as exc:
        await callback.answer(escape(exc.message), show_alert=True)
        return
    except Http404:
        await callback.answer('Данные устарели, откройте раздел заново.', show_alert=True)
        return
    text = f'Назначение снято: {_format_load_item(item)}'
    for data in statuses:
        teacher = data['teacher']
        text += f'\n{display_name(teacher, user)}: {display_hours(data["total_hours"], user, teacher)} ч'
    await callback.message.edit_text(text)
    await callback.answer('Сохранено')


@router.callback_query(ActionCallback.filter(F.action == 'distribution_filters'))
async def distribution_filters(callback: CallbackQuery):
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await callback.answer('Распределение отключено в режиме приватности minimal.', show_alert=True)
        return
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    await callback.message.edit_text('Выберите параметр фильтрации:', reply_markup=inline_buttons([
        [('Специальность', ActionCallback(action='distribution_filter_category', item_id=1).pack())],
        [('Семестр', ActionCallback(action='distribution_filter_category', item_id=2).pack())],
        [('Вид работы', ActionCallback(action='distribution_filter_category', item_id=3).pack())],
        [('‹ К нагрузке', ActionCallback(action='distribution_page', page=0).pack())],
    ]))
    await callback.answer()


@router.callback_query(ActionCallback.filter(F.action == 'distribution_filter_category'))
@router.callback_query(ActionCallback.filter(F.action == 'distribution_filter_options'))
async def distribution_filter_options(callback: CallbackQuery, callback_data: ActionCallback):
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await callback.answer('Распределение отключено в режиме приватности minimal.', show_alert=True)
        return
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None or callback_data.item_id not in (1, 2, 3) or callback_data.page < 0:
        await callback.answer('Нет доступа или некорректный фильтр.', show_alert=True)
        return
    category = callback_data.item_id
    choices = await sync_to_async(_distribution_filter_choices, thread_sensitive=True)(department, category)
    if not choices:
        await callback.answer('Нет доступных значений для фильтра.', show_alert=True)
        return
    page = callback_data.page if callback_data.action == 'distribution_filter_options' else 0
    rows = _distribution_filter_rows(choices, category, page)
    rows.append([('‹ К фильтрам', ActionCallback(action='distribution_filters').pack())])
    await callback.message.edit_text(f'Выберите {_filter_category_label(category)}:', reply_markup=inline_buttons(rows))
    await callback.answer()


@router.callback_query(ActionCallback.filter(F.action == 'distribution_filter_value'))
async def distribution_filter_value(callback: CallbackQuery, callback_data: ActionCallback, state: FSMContext):
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await callback.answer('Распределение отключено в режиме приватности minimal.', show_alert=True)
        return
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    category = callback_data.page
    if department is None or category not in (1, 2, 3):
        await callback.answer('Нет доступа или некорректный фильтр.', show_alert=True)
        return
    key = {1: 'specialty_id', 2: 'semester', 3: 'kind'}[category]
    selected = None
    if callback_data.item_id:
        choices = await sync_to_async(_distribution_filter_choices, thread_sensitive=True)(department, category)
        if category == 1:
            valid_ids = {choice.pk for choice in choices}
            selected = callback_data.item_id if callback_data.item_id in valid_ids else None
        elif category == 2:
            valid_values = set(choices)
            selected = callback_data.item_id if callback_data.item_id in valid_values else None
        else:
            valid_values = {index: kind for index, (kind, _) in enumerate(LoadItem.KIND_CHOICES, 1)}
            selected = valid_values.get(callback_data.item_id)
            if selected not in {kind for kind, _ in choices}:
                selected = None
        if selected is None:
            await callback.answer('Значение фильтра устарело.', show_alert=True)
            return
    data = await state.get_data()
    filters = data.get('distribution_filters') or {'specialty_id': None, 'semester': None, 'kind': None}
    filters[key] = selected
    await state.update_data(distribution_filters=filters)
    await _show_unassigned(callback.message, department, 0, state=state, edit=True)
    await callback.answer('Фильтр обновлён.')


@router.callback_query(ActionCallback.filter(F.action == 'distribution_filter_clear'))
async def distribution_filter_clear(callback: CallbackQuery, state: FSMContext):
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await callback.answer('Распределение отключено в режиме приватности minimal.', show_alert=True)
        return
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    await state.update_data(distribution_filters={'specialty_id': None, 'semester': None, 'kind': None})
    await _show_unassigned(callback.message, department, 0, state=state, edit=True)
    await callback.answer('Фильтры сброшены.')


@router.callback_query(ActionCallback.filter(F.action == 'distribution_item'))
@router.callback_query(ActionCallback.filter(F.action == 'distribution_candidates'))
async def distribution_item(callback: CallbackQuery, callback_data: ActionCallback):
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await callback.answer('Распределение отключено в режиме приватности minimal.', show_alert=True)
        return
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    await _show_distribution_item(
        callback.message, user, department, callback_data.item_id,
        callback_data.page if callback_data.action == 'distribution_candidates' else 0,
        edit=True,
    )
    await callback.answer()


@router.callback_query(ActionCallback.filter(F.action == 'assign'))
async def assign(callback: CallbackQuery, callback_data: ActionCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'head' not in roles:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await callback.answer('Распределение отключено в режиме приватности minimal.', show_alert=True)
        return
    try:
        item, statuses = await sync_to_async(_assign, thread_sensitive=True)(user, callback_data.item_id, callback_data.page)
    except ServiceError as exc:
        await callback.answer(escape(exc.message), show_alert=True)
        return
    except Http404:
        await callback.answer('Данные устарели, откройте раздел заново.', show_alert=True)
        return
    text = f'Назначение обновлено: {_format_load_item(item)}'
    icons = {'ok': '🟢', 'warning': '🟡', 'critical': '🔴'}
    for data in statuses:
        teacher = data['teacher']
        text += (
            f'\n{icons[data["status"]]} {display_name(teacher, user)}: '
            f'{display_hours(data["total_hours"], user, teacher)}/'
            f'{display_hours(teacher.effective_max_hours, user, teacher)} ч'
        )
        if data['warnings']:
            text += '\n' + '\n'.join(f'• {escape(warning)}' for warning in data['warnings'][:3])
    await callback.message.edit_text(text)
    await callback.answer('Сохранено')


@router.message(F.text == MENU_LABELS['recalculate'])
async def recalculate_menu(message: Message):
    _, user, roles = await authorized_user(message.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None:
        await message.answer('Пересчёт доступен заведующему своей кафедры.')
        return
    await message.answer(f'Пересчитать нагрузку кафедры «{escape(department.name)}»?', reply_markup=inline_buttons([
        [('Подтвердить', ChoiceCallback(action='recalc_head_yes', object_id=department.pk).pack()),
         ('Отмена', ChoiceCallback(action='recalc_no').pack())]
    ]))


@router.callback_query(ChoiceCallback.filter(F.action == 'recalc_head_yes'))
async def recalculate_confirm(callback: CallbackQuery, callback_data: ChoiceCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None or department.pk != callback_data.object_id:
        await callback.answer('Данные устарели или нет доступа.', show_alert=True)
        return
    await sync_to_async(_recalculate, thread_sensitive=True)(department.pk)
    await callback.message.edit_text('Нагрузка пересчитана. Назначения преподавателей сохранены.')
    await callback.answer()


@router.callback_query(ChoiceCallback.filter(F.action == 'recalc_no'))
async def recalculate_cancel(callback: CallbackQuery):
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    await callback.message.edit_text('Пересчёт отменён.')
    await callback.answer()


def _report_controls(all_teachers, user, teacher_id, year, page):
    rows = []
    year_buttons = []
    if year > 1900:
        year_buttons.append(('Год ◀', ReportCallback(action='year', teacher_id=teacher_id, year=year - 1, page=page).pack()))
    if year < 2100:
        year_buttons.append(('Год ▶', ReportCallback(action='year', teacher_id=teacher_id, year=year + 1, page=page).pack()))
    if year_buttons:
        rows.append(year_buttons)
    if settings.TELEGRAM_PRIVACY_MODE != 'minimal':
        if teacher_id:
            rows.append([('Отчёт всей кафедры', ReportCallback(action='choose', year=year).pack())])
        page_size = 8
        total_pages = max(1, (len(all_teachers) + page_size - 1) // page_size)
        for teacher in all_teachers[page * page_size:(page + 1) * page_size]:
            label = button_text(display_name(teacher, user), 60)
            rows.append([(label, ReportCallback(action='choose', teacher_id=teacher.pk, year=year, page=page).pack())])
        navigation = []
        if page:
            navigation.append(('‹ Преподаватели', ReportCallback(action='select_page', teacher_id=teacher_id, year=year, page=page - 1).pack()))
        if page + 1 < total_pages:
            navigation.append(('Ещё преподаватели ›', ReportCallback(action='select_page', teacher_id=teacher_id, year=year, page=page + 1).pack()))
        if navigation:
            rows.append(navigation)
    if settings.TELEGRAM_PRIVACY_MODE != 'minimal':
        rows.append([('📥 Excel', ReportCallback(action='excel', teacher_id=teacher_id, year=year, page=page).pack())])
    site_url = site_link('/report/')
    if site_url:
        rows.append([('🌐 Открыть на сайте', site_url)])
    return inline_buttons(rows)


async def _show_department_report(message, user, department, year, teacher_id=0, page=0, *, edit=False):
    if not 1900 <= year <= 2100:
        await message.answer('Некорректный год отчёта.')
        return
    all_teachers = await sync_to_async(lambda: list(_report_teachers(department)), thread_sensitive=True)()
    total_pages = max(1, (len(all_teachers) + PAGE_SIZE - 1) // PAGE_SIZE)
    if page < 0 or page >= total_pages:
        await message.answer('Список преподавателей устарел, откройте отчёт заново.')
        return
    if teacher_id:
        teachers = await sync_to_async(lambda: list(_report_teachers(department, teacher_id)), thread_sensitive=True)()
        if not teachers:
            await message.answer('Преподаватель не найден в вашей кафедре.')
            return
        title = display_name(teachers[0], user)
    else:
        teachers = all_teachers
        title = escape(department.name)
    matrix = await sync_to_async(report_matrix, thread_sensitive=True)(teachers, year)
    text = f'📊 <b>{title} · {year}</b>\n{format_report(matrix, user, teacher=teachers[0] if teacher_id else None)}'
    markup = _report_controls(all_teachers, user, teacher_id, year, page)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text == MENU_LABELS['department_report'])
async def department_report(message: Message):
    _, user, roles = await authorized_user(message.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None:
        await message.answer('Доступно только заведующему кафедрой.')
        return
    await _show_department_report(message, user, department, date.today().year)


@router.callback_query(ReportCallback.filter(F.action == 'year'))
@router.callback_query(ReportCallback.filter(F.action == 'choose'))
async def head_report_view(callback: CallbackQuery, callback_data: ReportCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if (department is None or not 1900 <= callback_data.year <= 2100
            or (callback_data.teacher_id and settings.TELEGRAM_PRIVACY_MODE == 'minimal')):
        await callback.answer('Нет доступа, некорректный год или ограничение приватности.', show_alert=True)
        return
    await _show_department_report(
        callback.message, user, department, callback_data.year,
        callback_data.teacher_id, callback_data.page, edit=True,
    )
    await callback.answer()


@router.callback_query(ReportCallback.filter(F.action == 'select_page'))
async def head_report_select_page(callback: CallbackQuery, callback_data: ReportCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None or not 1900 <= callback_data.year <= 2100:
        await callback.answer('Нет доступа или некорректный год.', show_alert=True)
        return
    all_teachers = await sync_to_async(lambda: list(_report_teachers(department)), thread_sensitive=True)()
    total_pages = max(1, (len(all_teachers) + PAGE_SIZE - 1) // PAGE_SIZE)
    if (callback_data.page < 0 or callback_data.page >= total_pages
            or (callback_data.teacher_id and callback_data.teacher_id not in {teacher.pk for teacher in all_teachers})
            or (callback_data.teacher_id and settings.TELEGRAM_PRIVACY_MODE == 'minimal')):
        await callback.answer('Список устарел или отчёт недоступен.', show_alert=True)
        return
    markup = _report_controls(all_teachers, user, callback_data.teacher_id, callback_data.year, callback_data.page)
    await callback.message.edit_reply_markup(reply_markup=markup)
    await callback.answer()


@router.callback_query(ReportCallback.filter(F.action == 'excel'))
async def head_report_excel(callback: CallbackQuery, callback_data: ReportCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None or not 1900 <= callback_data.year <= 2100:
        await callback.answer('Нет доступа или некорректный год.', show_alert=True)
        return
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await callback.answer('Экспорт недоступен в режиме приватности minimal.', show_alert=True)
        return
    teachers = await sync_to_async(lambda: list(_report_teachers(department, callback_data.teacher_id or None)), thread_sensitive=True)()
    if callback_data.teacher_id and not teachers:
        await callback.answer('Преподаватель не найден в вашей кафедре.', show_alert=True)
        return
    table = await sync_to_async(report_tables, thread_sensitive=True)(teachers, callback_data.year, len(teachers) == 1)
    table = privacy_safe_excel_table(table)
    filename, sheet_name, headers, rows, extra_sheets = table
    content = await sync_to_async(build_xlsx_bytes, thread_sensitive=True)(sheet_name, headers, rows, extra_sheets)
    await callback.message.answer_document(BufferedInputFile(content, filename=filename))
    await callback.answer()


@router.message(F.text == MENU_LABELS['exports'])
async def exports_menu(message: Message):
    _, user, roles = await authorized_user(message.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None:
        await message.answer('Экспорт доступен заведующему кафедрой.')
        return
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        await message.answer('Экспорт с персональными данными отключён в режиме приватности minimal.')
        return
    await message.answer('Выберите файл:', reply_markup=inline_buttons([
        [('Нагрузка кафедры', ChoiceCallback(action='export_load').pack())],
        [('Преподаватели', ChoiceCallback(action='export_teachers').pack())],
    ]))


async def _send_export(callback, service, user, department):
    table = await sync_to_async(service, thread_sensitive=True)(department)
    table = privacy_safe_excel_table(table)
    if table is None:
        await callback.answer('Экспорт недоступен в режиме приватности minimal.', show_alert=True)
        return
    filename, sheet_name, headers, rows, extra_sheets = table
    content = await sync_to_async(build_xlsx_bytes, thread_sensitive=True)(sheet_name, headers, rows, extra_sheets)
    await callback.message.answer_document(BufferedInputFile(content, filename=filename))


@router.callback_query(ChoiceCallback.filter(F.action == 'export_load'))
async def export_load(callback: CallbackQuery):
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    await _send_export(callback, department_load_table, user, department)
    await callback.answer()


@router.callback_query(ChoiceCallback.filter(F.action == 'export_teachers'))
async def export_teachers(callback: CallbackQuery):
    _, user, roles = await authorized_user(callback.from_user.id)
    department = await sync_to_async(_department, thread_sensitive=True)(user) if user and 'head' in roles else None
    if department is None:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    await _send_export(callback, teachers_table, user, department)
    await callback.answer()
