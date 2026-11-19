from io import BytesIO
from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile

from core.calculations import check_teacher, recalculate_department, teacher_total
from core.importers import MAX_WORKBOOK_SIZE, WorkloadImportError, import_workload_xlsx
from core.models import CalcSettings, Department, Discipline, Group, Curriculum, LoadItem, Specialty, Teacher
from core.tgbot.handlers.common import authorized_user
from core.tgbot.keyboards import ActionCallback, ChoiceCallback, MENU_LABELS, inline_buttons
from core.tgbot.states import ImportForm
from core.tgbot.texts import display_hours, display_name, escape
from core.views import check_teacher_status

router = Router(name='dean')


def _dashboard():
    departments = list(Department.objects.order_by('name'))
    rows = []
    for department in departments:
        total = LoadItem.objects.filter(discipline__department=department).count()
        unassigned = LoadItem.objects.filter(discipline__department=department, teacher__isnull=True).count()
        critical = sum(1 for teacher in Teacher.objects.filter(department=department).select_related('position')
                       if check_teacher_status(teacher)[0] == 'critical')
        rows.append((department, total, unassigned, critical))
    return {
        'specialties': Specialty.objects.count(),
        'departments': len(departments),
        'groups': Group.objects.count(),
        'curricula': Curriculum.objects.count(),
        'disciplines': Discipline.objects.count(),
        'rows': rows,
    }


def _recalculate_department(department_id):
    department = Department.objects.get(pk=department_id)
    recalculate_department(department, CalcSettings.get())
    return department.name


def _recalculate_all():
    departments = list(Department.objects.all())
    for department in departments:
        recalculate_department(department, CalcSettings.get())
    return len(departments)


def _dashboard_text(data, page):
    page_size = 8
    total_pages = max(1, (len(data['rows']) + page_size - 1) // page_size)
    if page < 0 or page >= total_pages:
        return None, None
    lines = [
        '🏛 <b>Сводка</b>',
        f'Специальностей: {data["specialties"]} · Кафедр: {data["departments"]}',
        f'Групп: {data["groups"]} · Учебных планов: {data["curricula"]} · Дисциплин: {data["disciplines"]}',
        f'<b>Кафедры · страница {page + 1}/{total_pages}</b>',
    ]
    for department, total, unassigned, critical in data['rows'][page * page_size:(page + 1) * page_size]:
        name = escape(department.name[:60]) if settings.TELEGRAM_PRIVACY_MODE != 'minimal' else f'Кафедра {department.pk}'
        share = (unassigned / total * 100) if total else 0
        lines.append(f'• {name}: элементов {total}, не распределено {share:.0f}%, 🔴 преподавателей {critical}')
    nav = []
    if page:
        nav.append(('‹ Назад', ActionCallback(action='dean_dashboard_page', page=page - 1).pack()))
    if page + 1 < total_pages:
        nav.append(('Вперёд ›', ActionCallback(action='dean_dashboard_page', page=page + 1).pack()))
    rows = [nav] if nav else []
    return '\n'.join(lines), inline_buttons(rows) if rows else None


def _department_choice_rows(departments, page, action, page_action):
    page_size = 8
    total_pages = max(1, (len(departments) + page_size - 1) // page_size)
    if page < 0 or page >= total_pages:
        return None
    rows = []
    for department in departments[page * page_size:(page + 1) * page_size]:
        label = department.name if len(department.name) <= 64 else department.name[:63] + '…'
        rows.append([(label, ChoiceCallback(action=action, object_id=department.pk).pack())])
    nav = []
    if page:
        nav.append(('‹ Назад', ActionCallback(action=page_action, page=page - 1).pack()))
    if page + 1 < total_pages:
        nav.append(('Вперёд ›', ActionCallback(action=page_action, page=page + 1).pack()))
    if nav:
        rows.append(nav)
    return rows


def _import_file(contents, filename, department_id, year):
    department = Department.objects.get(pk=department_id)
    upload = SimpleUploadedFile(filename, contents, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    return import_workload_xlsx(upload, department, year)


async def _show_dashboard(message, data, page=0, *, edit=False):
    text, markup = _dashboard_text(data, page)
    if text is None:
        await message.answer('Страница сводки устарела, откройте раздел заново.')
        return
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text == MENU_LABELS['dean'])
async def dashboard(message: Message):
    _, user, roles = await authorized_user(message.from_user.id)
    if user is None or 'deputy_dean' not in roles:
        await message.answer('Сводка доступна только заместителю декана.')
        return
    data = await sync_to_async(_dashboard, thread_sensitive=True)()
    await _show_dashboard(message, data)


@router.callback_query(ActionCallback.filter(F.action == 'dean_dashboard_page'))
async def dashboard_page(callback: CallbackQuery, callback_data: ActionCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'deputy_dean' not in roles:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    data = await sync_to_async(_dashboard, thread_sensitive=True)()
    await _show_dashboard(callback.message, data, callback_data.page, edit=True)
    await callback.answer()


async def _show_recalculate_menu(message, page=0, *, edit=False):
    departments = await sync_to_async(lambda: list(Department.objects.order_by('name')), thread_sensitive=True)()
    rows = _department_choice_rows(departments, page, 'dean_recalc_confirm', 'dean_recalc_page')
    if rows is None:
        await message.answer('Список кафедр устарел, откройте раздел заново.')
        return
    rows.append([('Пересчитать все кафедры', ChoiceCallback(action='dean_recalc_all_confirm').pack())])
    markup = inline_buttons(rows)
    if edit:
        await message.edit_text('Выберите кафедру для пересчёта:', reply_markup=markup)
    else:
        await message.answer('Выберите кафедру для пересчёта:', reply_markup=markup)


@router.message(F.text == MENU_LABELS['dean_recalculate'])
async def recalculate_menu(message: Message):
    _, user, roles = await authorized_user(message.from_user.id)
    if user is None or 'deputy_dean' not in roles:
        await message.answer('Пересчёт доступен только заместителю декана.')
        return
    await _show_recalculate_menu(message)


@router.callback_query(ActionCallback.filter(F.action == 'dean_recalc_page'))
async def recalculate_page(callback: CallbackQuery, callback_data: ActionCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'deputy_dean' not in roles:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    await _show_recalculate_menu(callback.message, callback_data.page, edit=True)
    await callback.answer()


@router.callback_query(ChoiceCallback.filter(F.action == 'dean_recalc_confirm'))
async def recalculate_confirm(callback: CallbackQuery, callback_data: ChoiceCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'deputy_dean' not in roles:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    department = await sync_to_async(lambda: Department.objects.filter(pk=callback_data.object_id).first(), thread_sensitive=True)()
    if department is None:
        await callback.answer('Данные устарели, откройте раздел заново.', show_alert=True)
        return
    await callback.message.edit_text(
        f'Пересчитать нагрузку кафедры «{escape(department.name)}»?',
        reply_markup=inline_buttons([[
            ('Подтвердить', ChoiceCallback(action='dean_recalc_yes', object_id=department.pk).pack()),
            ('Отмена', ChoiceCallback(action='dean_recalc_all_no').pack()),
        ]]),
    )
    await callback.answer()


@router.callback_query(ChoiceCallback.filter(F.action == 'dean_recalc_yes'))
async def recalculate_yes(callback: CallbackQuery, callback_data: ChoiceCallback):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'deputy_dean' not in roles:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    try:
        name = await sync_to_async(_recalculate_department, thread_sensitive=True)(callback_data.object_id)
    except Department.DoesNotExist:
        await callback.answer('Данные устарели, откройте раздел заново.', show_alert=True)
        return
    await callback.message.edit_text(f'Нагрузка кафедры «{escape(name)}» пересчитана. Назначения сохранены.')
    await callback.answer()


@router.callback_query(ChoiceCallback.filter(F.action == 'dean_recalc_all_confirm'))
async def recalculate_all_confirm(callback: CallbackQuery):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'deputy_dean' not in roles:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    await callback.message.edit_text('Пересчитать нагрузку всех кафедр?', reply_markup=inline_buttons([
        [('Подтвердить', ChoiceCallback(action='dean_recalc_all_yes').pack()),
         ('Отмена', ChoiceCallback(action='dean_recalc_all_no').pack())]
    ]))
    await callback.answer()


@router.callback_query(ChoiceCallback.filter(F.action == 'dean_recalc_all_yes'))
async def recalculate_all_yes(callback: CallbackQuery):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'deputy_dean' not in roles:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    count = await sync_to_async(_recalculate_all, thread_sensitive=True)()
    await callback.message.edit_text(f'Пересчитаны все кафедры: {count}.')
    await callback.answer()


@router.callback_query(ChoiceCallback.filter(F.action == 'dean_recalc_all_no'))
async def recalculate_all_no(callback: CallbackQuery):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'deputy_dean' not in roles:
        await callback.answer('Нет доступа.', show_alert=True)
        return
    await callback.message.edit_text('Пересчёт отменён.')
    await callback.answer()


async def _show_import_departments(message, state, page=0, *, edit=False):
    departments = await sync_to_async(lambda: list(Department.objects.order_by('name')), thread_sensitive=True)()
    rows = _department_choice_rows(departments, page, 'import_department', 'import_department_page')
    if rows is None:
        await message.answer('Список кафедр устарел, откройте импорт заново.')
        return
    markup = inline_buttons(rows)
    if edit:
        await message.edit_text('Выберите кафедру для импорта:', reply_markup=markup)
    else:
        await message.answer('Выберите кафедру для импорта:', reply_markup=markup)


@router.message(F.text == MENU_LABELS['import'])
async def import_menu(message: Message, state: FSMContext):
    _, user, roles = await authorized_user(message.from_user.id)
    if user is None or 'deputy_dean' not in roles:
        await message.answer('Импорт доступен только заместителю декана.')
        return
    departments_exist = await sync_to_async(Department.objects.exists, thread_sensitive=True)()
    if not departments_exist:
        await message.answer('Сначала создайте кафедру в TutorSys.')
        return
    await state.clear()
    await state.set_state(ImportForm.department)
    await _show_import_departments(message, state)


@router.callback_query(ActionCallback.filter(F.action == 'import_department_page'))
async def import_department_page(callback: CallbackQuery, callback_data: ActionCallback, state: FSMContext):
    _, user, roles = await authorized_user(callback.from_user.id)
    if (user is None or 'deputy_dean' not in roles
            or await state.get_state() != ImportForm.department.state):
        await callback.answer('Данные устарели или нет доступа.', show_alert=True)
        return
    await _show_import_departments(callback.message, state, callback_data.page, edit=True)
    await callback.answer()


@router.callback_query(ChoiceCallback.filter(F.action == 'import_department'))
async def import_department(callback: CallbackQuery, callback_data: ChoiceCallback, state: FSMContext):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'deputy_dean' not in roles or await state.get_state() != ImportForm.department.state:
        await callback.answer('Данные устарели или нет доступа.', show_alert=True)
        return
    exists = await sync_to_async(Department.objects.filter(pk=callback_data.object_id).exists, thread_sensitive=True)()
    if not exists:
        await callback.answer('Данные устарели.', show_alert=True)
        return
    await state.update_data(department_id=callback_data.object_id)
    await state.set_state(ImportForm.year)
    await callback.message.answer('Введите год начала учебного года, например 2025:')
    await callback.answer()


@router.message(ImportForm.year, F.text & ~F.text.startswith('/'))
async def import_year(message: Message, state: FSMContext):
    _, user, roles = await authorized_user(message.from_user.id)
    if user is None or 'deputy_dean' not in roles:
        await state.clear()
        await message.answer('Нет доступа.')
        return
    try:
        year = int((message.text or '').strip())
        if not 1900 <= year <= 2100:
            raise ValueError
    except ValueError:
        await message.answer('Введите корректный год от 1900 до 2100.')
        return
    await state.update_data(academic_year=year)
    await state.set_state(ImportForm.document)
    await message.answer('Теперь отправьте файл Excel формата .xlsx размером не более 2 МБ.')


@router.message(ImportForm.document, F.document)
async def import_document(message: Message, state: FSMContext):
    _, user, roles = await authorized_user(message.from_user.id)
    if user is None or 'deputy_dean' not in roles:
        await state.clear()
        await message.answer('Нет доступа.')
        return
    document = message.document
    filename = document.file_name or ''
    if not filename.lower().endswith('.xlsx'):
        await message.answer('Допускаются только файлы .xlsx.')
        return
    if document.file_size is None:
        await message.answer('Не удалось определить размер файла; отправьте документ повторно.')
        return
    if document.file_size > MAX_WORKBOOK_SIZE:
        await message.answer('Размер файла превышает 2 МБ.')
        return
    buffer = BytesIO()
    await message.bot.download(document, destination=buffer)
    contents = buffer.getvalue()
    if len(contents) > MAX_WORKBOOK_SIZE:
        await message.answer('Размер файла превышает 2 МБ.')
        return
    data = await state.get_data()
    try:
        department_name = await sync_to_async(lambda: Department.objects.get(pk=data['department_id']).name, thread_sensitive=True)()
    except Department.DoesNotExist:
        await state.clear()
        await message.answer('Кафедра больше не существует. Начните импорт заново.')
        return
    await state.update_data(import_contents=contents, import_filename=filename)
    await state.set_state(ImportForm.confirm)
    await message.answer(
        f'Импорт изменит данные кафедры «{escape(department_name)}» за год {data["academic_year"]}. Продолжить?',
        reply_markup=inline_buttons([[('Импортировать', ChoiceCallback(action='import_confirm').pack()),
                                      ('Отмена', ChoiceCallback(action='import_cancel').pack())]])
    )


@router.message(ImportForm.document)
async def import_document_required(message: Message):
    await message.answer('Отправьте документ Excel .xlsx.')


@router.callback_query(ChoiceCallback.filter(F.action == 'import_cancel'))
async def import_cancel(callback: CallbackQuery, state: FSMContext):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'deputy_dean' not in roles or await state.get_state() != ImportForm.confirm.state:
        await callback.answer('Данные устарели или нет доступа.', show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text('Импорт отменён.')
    await callback.answer()


@router.callback_query(ChoiceCallback.filter(F.action == 'import_confirm'))
async def import_confirm(callback: CallbackQuery, state: FSMContext):
    _, user, roles = await authorized_user(callback.from_user.id)
    if user is None or 'deputy_dean' not in roles or await state.get_state() != ImportForm.confirm.state:
        await callback.answer('Данные устарели или нет доступа.', show_alert=True)
        return
    data = await state.get_data()
    await state.clear()
    try:
        result = await sync_to_async(_import_file, thread_sensitive=True)(
            data['import_contents'], data['import_filename'], data['department_id'], data['academic_year']
        )
    except WorkloadImportError as exc:
        await callback.message.edit_text(escape(str(exc))[:3900])
        await callback.answer()
        return
    except Department.DoesNotExist:
        await callback.message.edit_text('Кафедра больше не существует. Начните импорт заново.')
        await callback.answer()
        return
    await callback.message.edit_text(
        'Импорт завершён.\n'
        f'Строк: {result["rows"]} · специальностей: {result["specialties"]} · '
        f'групп: {result["groups"]} · дисциплин: {result["disciplines"]} · '
        f'предупреждений: {result["warnings"]}'
    )
    await callback.answer()
