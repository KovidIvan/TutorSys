"""
Представления (views) приложения core.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
from datetime import date
from decimal import Decimal, InvalidOperation
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from .models import (
    CalcSettings, PositionRule, Discipline, Department, Teacher, LoadItem, JournalEntry, Adjustment
)
from .forms import AddPositionForm, CalcSettingsForm, JournalEntryForm, PositionRuleForm
from .calculations import (
    calc_department_load, recalculate_department, check_teachers, teacher_total,
    report_for_teacher
)


# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============

def has_role(user, role_name):
    """Проверить, есть ли у пользователя указанная роль."""
    if not hasattr(user, '_role_names'):
        user._role_names = set(user.groups.values_list('name', flat=True))
    return role_name in user._role_names


def get_user_role(user):
    """Определить основную роль пользователя (для контекста шаблона)."""
    if has_role(user, 'deputy_dean'):
        return 'deputy_dean'
    elif has_role(user, 'head'):
        return 'head'
    elif has_role(user, 'teacher'):
        return 'teacher'
    elif user.is_staff:
        return 'admin'
    return None


def add_role_context(context, request):
    """Добавить информацию о ролях пользователя в контекст шаблона."""
    user = request.user
    context['user_role'] = get_user_role(user)
    context['is_deputy_dean'] = has_role(user, 'deputy_dean')
    context['is_head'] = has_role(user, 'head')
    context['is_teacher'] = has_role(user, 'teacher')
    context['is_authenticated'] = user.is_authenticated
    return context


def add_form_errors(request, form):
    for field_name, errors in form.errors.items():
        field = form.fields.get(field_name)
        label = field.label if field else 'Форма'
        for error in errors:
            messages.error(request, f'{label}: {error}')


# ============ ПАРАМЕТРЫ РАСЧЁТА ============

@login_required
def settings_view(request):
    """
    Страница настроек параметров расчёта и норм по должностям.

    Доступ: зам. декана и зав. кафедрой.
    """
    # Проверка прав доступа
    if not (has_role(request.user, 'deputy_dean') or has_role(request.user, 'head')):
        return render(request, 'core/error.html', {
            'message': '❌ Доступ запрещён. Только зам. декана и завед. кафедрой могут менять параметры.',
            'code': 403
        }, status=403)

    settings = CalcSettings.get()
    position_rules = PositionRule.objects.all()

    if request.method == 'POST':
        action = request.POST.get('action')

        # ============ ОБНОВЛЕНИЕ CALCSETTINGS ============
        if action == 'update_settings':
            form = CalcSettingsForm(request.POST, instance=settings)
            if form.is_valid():
                form.save()
                messages.success(
                    request,
                    'Параметры расчёта обновлены. '
                    'Для пересчёта нагрузки кафедр используйте кнопку ниже.'
                )
            else:
                add_form_errors(request, form)

        # ============ ПЕРЕСЧЁТ НАГРУЗКИ ============
        elif action == 'recalculate_all':
            if not has_role(request.user, 'deputy_dean'):
                return render(request, 'core/error.html', {
                    'message': '❌ Доступ запрещён. Пересчитывать нагрузку всех кафедр может только зам. декана.',
                    'code': 403
                }, status=403)
            try:
                departments = Department.objects.all()
                recalculation_totals = {
                    'created': 0,
                    'updated': 0,
                    'deleted': 0,
                    'deleted_with_teacher': 0,
                }
                for dept in departments:
                    summary = recalculate_department(dept, settings)
                    for key in recalculation_totals:
                        recalculation_totals[key] += summary[key]
                messages.success(
                    request,
                    f'Нагрузка пересчитана для {departments.count()} кафедр. '
                    f"Создано: {recalculation_totals['created']}; "
                    f"обновлено: {recalculation_totals['updated']}; "
                    f"удалено: {recalculation_totals['deleted']} "
                    f"(с назначенным преподавателем: {recalculation_totals['deleted_with_teacher']}). "
                    'Элементы с записями в журнале сохранены.'
                )
            except Exception:
                messages.error(request, 'Не удалось пересчитать нагрузку. Проверьте данные и повторите попытку.')

        # ============ ОБНОВЛЕНИЕ ДОЛЖНОСТИ ============
        elif action == 'update_position':
            position_id = request.POST.get('position_id')
            position = get_object_or_404(PositionRule, pk=position_id)
            form = PositionRuleForm(request.POST, instance=position)
            if form.is_valid():
                form.save()
                messages.success(request, f'Должность "{position.position}" обновлена.')
            else:
                add_form_errors(request, form)

        # ============ ДОБАВЛЕНИЕ НОВОЙ ДОЛЖНОСТИ ============
        elif action == 'add_position':
            form = AddPositionForm(request.POST)
            if form.is_valid():
                position = PositionRule.objects.create(**form.cleaned_data)
                messages.success(request, f'Должность "{position.position}" добавлена.')
            else:
                add_form_errors(request, form)

        return redirect('core:settings')

    context = {
        'settings': settings,
        'settings_form': CalcSettingsForm(instance=settings),
        'position_rules': position_rules,
    }
    context = add_role_context(context, request)
    return render(request, 'core/settings.html', context)


# ============ РАСПРЕДЕЛЕНИЕ НАГРУЗКИ ============

@login_required
def distribution_view(request):
    """
    Страница распределения нагрузки по преподавателям.
    Доступ: зав. кафедрой.
    """
    # Проверка прав доступа
    if not has_role(request.user, 'head'):
        return render(request, 'core/error.html', {
            'message': '❌ Доступ запрещён. Только зав. кафедрой может распределять нагрузку.',
            'code': 403
        }, status=403)

    # Получить кафедру пользователя
    user_department = None
    try:
        user_department = request.user.teacher.department
    except Teacher.DoesNotExist:
        pass

    if user_department:
        disciplines = Discipline.objects.filter(department=user_department).order_by('name')
    else:
        disciplines = Discipline.objects.none()

    # Элементы нагрузки этой кафедры
    load_items = LoadItem.objects.filter(
        discipline__department=user_department
    ).select_related('discipline', 'group', 'teacher').order_by('discipline', 'kind') if user_department else LoadItem.objects.none()

    # Преподаватели кафедры
    teachers = list(Teacher.objects.filter(
        department=user_department
    ).select_related('position').prefetch_related('specialization') if user_department else Teacher.objects.none())

    # Обновление назначения преподавателя
    if request.method == 'POST':
        load_item_id = request.POST.get('load_item_id')
        teacher_id = request.POST.get('teacher_id')

        try:
            load_item = get_object_or_404(
                LoadItem,
                pk=load_item_id,
                discipline__department=user_department
            )
            if teacher_id:
                teacher = Teacher.objects.filter(
                    pk=teacher_id,
                    department=user_department
                ).first()
                if teacher is None:
                    messages.error(request, 'Нельзя назначить нагрузку преподавателю другой кафедры.')
                    return redirect('core:distribution')
                load_item.teacher = teacher
            else:
                load_item.teacher = None
            load_item.save()

            messages.success(request, 'Назначение обновлено.')
        except (ValueError, LoadItem.DoesNotExist, Teacher.DoesNotExist):
            messages.error(request, 'Не удалось изменить назначение. Проверьте выбранные данные.')

        return redirect('core:distribution')

    # Подготовить данные для панели преподавателей
    totals = {
        row['teacher_id']: row['total'] or Decimal('0')
        for row in LoadItem.objects.filter(teacher__in=teachers)
        .values('teacher_id').annotate(total=Sum('hours'))
    }
    warnings_by_teacher = check_teachers(teachers)
    teachers_data = []
    for teacher in teachers:
        warnings = warnings_by_teacher[teacher.pk]
        status = 'critical' if len(warnings) > 1 else 'warning' if warnings else 'ok'
        teachers_data.append({
            'teacher': teacher,
            'total_hours': totals.get(teacher.pk, Decimal('0')),
            'status': status,
            'warnings': warnings,
        })

    context = {
        'disciplines': disciplines,
        'load_items': load_items,
        'teachers': teachers,
        'teachers_data': teachers_data,
        'user_department': user_department,
    }
    context = add_role_context(context, request)
    return render(request, 'core/distribution.html', context)


@login_required
@require_http_methods(["GET"])
def load_teacher_panel_view(request):
    """
    AJAX endpoint: обновить панель преподавателей (для HTMX).
    """
    if not has_role(request.user, 'head'):
        return render(request, 'core/error.html', {
            'message': '❌ Доступ запрещён.',
            'code': 403
        }, status=403)

    user_department = None
    try:
        user_department = request.user.teacher.department
    except Teacher.DoesNotExist:
        pass

    if not user_department:
        return render(request, 'core/_teacher_panel.html', {'teachers_data': []})

    teachers = list(Teacher.objects.filter(
        department=user_department
    ).select_related('position').prefetch_related('specialization'))

    totals = {
        row['teacher_id']: row['total'] or Decimal('0')
        for row in LoadItem.objects.filter(teacher__in=teachers)
        .values('teacher_id').annotate(total=Sum('hours'))
    }
    warnings_by_teacher = check_teachers(teachers)
    teachers_data = []
    for teacher in teachers:
        warnings = warnings_by_teacher[teacher.pk]
        status = 'critical' if len(warnings) > 1 else 'warning' if warnings else 'ok'
        teachers_data.append({
            'teacher': teacher,
            'total_hours': totals.get(teacher.pk, Decimal('0')),
            'status': status,
            'warnings': warnings,
        })

    return render(request, 'core/_teacher_panel.html', {'teachers_data': teachers_data})


# ============ НАГРУЗКА ПРЕПОДАВАТЕЛЯ ============

@login_required
def workload_view(request):
    """
    Страница собственной нагрузки преподавателя.
    Доступ: преподаватель.
    """
    # Проверка прав доступа
    if not has_role(request.user, 'teacher'):
        return render(request, 'core/error.html', {
            'message': '❌ Доступ запрещён. Это страница только для преподавателей.',
            'code': 403
        }, status=403)

    try:
        teacher = request.user.teacher
    except Teacher.DoesNotExist:
        teacher = None

    if not teacher:
        return render(request, 'core/error.html', {
            'message': 'Вы не зарегистрированы как преподаватель.',
            'code': 404
        }, status=404)

    load_items = LoadItem.objects.filter(
        teacher=teacher
    ).select_related('discipline', 'group').order_by('discipline')

    # Группировать по дисциплинам
    load_by_discipline = {}
    for item in load_items:
        disc = item.discipline
        if disc not in load_by_discipline:
            load_by_discipline[disc] = []
        load_by_discipline[disc].append(item)

    # Подсчитать всего часов
    total_hours = load_items.aggregate(total=Sum('hours'))['total'] or 0

    context = {
        'teacher': teacher,
        'load_items': load_items,
        'load_by_discipline': load_by_discipline,
        'total_hours': total_hours,
    }
    context = add_role_context(context, request)
    return render(request, 'core/workload.html', context)


# ============ ЖУРНАЛ ============

@login_required
def journal_view(request):
    """
    Страница журнала проведённых занятий для преподавателя.
    Доступ: преподаватель.
    """
    # Проверка прав доступа
    if not has_role(request.user, 'teacher'):
        return render(request, 'core/error.html', {
            'message': '❌ Доступ запрещён. Это страница только для преподавателей.',
            'code': 403
        }, status=403)

    try:
        teacher = request.user.teacher
    except Teacher.DoesNotExist:
        teacher = None

    if not teacher:
        return render(request, 'core/error.html', {
            'message': 'Вы не зарегистрированы как преподаватель.',
            'code': 404
        }, status=404)

    # Получить нагрузку преподавателя
    load_items = LoadItem.objects.filter(
        teacher=teacher
    ).select_related('discipline', 'group').order_by('discipline')

    # Запросы в журнале
    journal_entries = JournalEntry.objects.filter(
        teacher=teacher
    ).select_related('load_item', 'load_item__discipline').order_by('-date')

    entry_form = JournalEntryForm(teacher=teacher)
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_entry':
            entry_form = JournalEntryForm(request.POST, teacher=teacher)
            if entry_form.is_valid():
                entry = entry_form.save(commit=False)
                entry.teacher = teacher
                entry.save()
                messages.success(request, 'Запись в журнал добавлена.')
            else:
                add_form_errors(request, entry_form)

        return redirect('core:journal')

    context = {
        'teacher': teacher,
        'load_items': load_items,
        'journal_entries': journal_entries,
        'entry_form': entry_form,
    }
    context = add_role_context(context, request)
    return render(request, 'core/journal.html', context)


# ============ ОТЧЁТ ПЛАН/ФАКТ ============

@login_required
def report_view(request):
    """Помесячный отчёт план/факт для преподавателя или заведующего кафедрой."""
    is_head = has_role(request.user, 'head')
    is_teacher = has_role(request.user, 'teacher')
    if not (is_head or is_teacher):
        return render(request, 'core/error.html', {
            'message': '❌ Доступ запрещён. Отчёт доступен преподавателям и заведующим кафедрой.',
            'code': 403
        }, status=403)

    current_year = date.today().year
    valid_year = True
    try:
        year = int(request.POST.get('year') if request.method == 'POST' else request.GET.get('year', current_year))
        if year < 1900 or year > 2100:
            raise ValueError
    except (TypeError, ValueError):
        messages.error(request, 'Укажите корректный год отчёта.')
        year = current_year
        valid_year = False

    if is_head:
        try:
            department = request.user.teacher.department
        except Teacher.DoesNotExist:
            return render(request, 'core/error.html', {
                'message': 'Для вашей учётной записи не указана кафедра.',
                'code': 404
            }, status=404)
        teachers = Teacher.objects.filter(department=department).select_related('department')
        teacher_id = request.POST.get('teacher_id') if request.method == 'POST' else request.GET.get('teacher_id')
        try:
            teacher = teachers.filter(pk=int(teacher_id)).first() if teacher_id else teachers.first()
        except (TypeError, ValueError):
            teacher = None
        if teacher is None:
            return render(request, 'core/error.html', {
                'message': 'На кафедре пока нет преподавателей для отчёта.',
                'code': 404
            }, status=404)
    else:
        try:
            teacher = request.user.teacher
        except Teacher.DoesNotExist:
            return render(request, 'core/error.html', {
                'message': 'Вы не зарегистрированы как преподаватель.',
                'code': 404
            }, status=404)
        teachers = None

    load_items = LoadItem.objects.filter(teacher=teacher).select_related('discipline')
    if request.method == 'POST':
        if request.POST.get('action') == 'add_adjustment' and valid_year:
            try:
                discipline_id = request.POST.get('discipline_id')
                kind = request.POST.get('kind')
                month = int(request.POST.get('month', ''))
                hours_delta = Decimal(request.POST.get('hours_delta', '').strip())
                reason = request.POST.get('reason', '').strip()
                if (
                    not 1 <= month <= 12
                    or not hours_delta.is_finite()
                    or not hours_delta
                    or abs(hours_delta) > Decimal('999.99')
                    or max(-hours_delta.as_tuple().exponent, 0) > 2
                    or not reason
                ):
                    raise ValueError
                discipline = Discipline.objects.get(
                    pk=discipline_id,
                    load_items__teacher=teacher,
                    load_items__kind=kind
                )
                Adjustment.objects.create(
                    teacher=teacher,
                    discipline=discipline,
                    kind=kind,
                    month=month,
                    year=year,
                    hours_delta=hours_delta,
                    reason=reason,
                    planned=request.POST.get('planned') == 'on'
                )
                messages.success(request, 'Корректировка нагрузки сохранена.')
            except (ValueError, InvalidOperation, Discipline.DoesNotExist):
                messages.error(request, 'Проверьте месяц, часы и выбранную дисциплину с видом работы.')

        redirect_url = f'{request.path}?year={year}'
        if is_head:
            redirect_url += f'&teacher_id={teacher.pk}'
        return redirect(redirect_url)

    report_data = report_for_teacher(teacher, year)
    adjustments = Adjustment.objects.filter(
        teacher=teacher, year=year
    ).select_related('discipline')
    adjustments_by_month_kind = {}
    for adjustment in adjustments:
        adjustments_by_month_kind.setdefault((adjustment.month, adjustment.kind), []).append(adjustment)

    kind_labels = dict(LoadItem.KIND_CHOICES)
    month_names = (
        'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
        'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'
    )
    months = []
    for month, kinds in report_data['months'].items():
        rows = []
        for kind, values in kinds.items():
            rows.append({
                'kind': kind,
                'kind_display': kind_labels.get(kind, kind),
                'values': values,
                'adjustments': adjustments_by_month_kind.get((month, kind), []),
            })
        months.append({'number': month, 'name': month_names[month - 1], 'rows': rows})

    disciplines = Discipline.objects.filter(load_items__teacher=teacher).distinct()
    kind_choices = [
        (kind, label) for kind, label in LoadItem.KIND_CHOICES
        if load_items.filter(kind=kind).exists()
    ]
    context = {
        'teacher': teacher,
        'teachers': teachers,
        'year': year,
        'months': months,
        'disciplines': disciplines,
        'kind_choices': kind_choices,
        'years': sorted(set(range(current_year - 2, current_year + 2)) | {year}),
        'has_load': bool(kind_choices),
    }
    context = add_role_context(context, request)
    return render(request, 'core/report.html', context)


def xlsx_response(filename, sheet_name, headers, rows):
    """Сформировать XLSX-файл из заголовков и строк данных."""
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name[:31]
    worksheet.append(headers)

    for cell in worksheet[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill(fill_type='solid', fgColor='1F4E78')

    for row in rows:
        safe_row = []
        for value in row:
            if isinstance(value, str) and value.lstrip(' \t\r\n')[:1] in ('=', '+', '-', '@'):
                value = "'" + value
            safe_row.append(value)
        worksheet.append(safe_row)

    worksheet.freeze_panes = 'A2'
    worksheet.auto_filter.ref = worksheet.dimensions
    for column in worksheet.columns:
        column_letter = column[0].column_letter
        width = max((len(str(cell.value or '')) for cell in column), default=0)
        worksheet.column_dimensions[column_letter].width = min(max(width + 2, 12), 60)

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    workbook.save(response)
    return response


@login_required
def export_excel_view(request, export_type):
    """Экспорт нагрузки, преподавателей, журнала или отчёта в XLSX."""
    export_types = {
        'department-load', 'teachers', 'workload', 'journal', 'report'
    }
    if export_type not in export_types:
        return render(request, 'core/error.html', {
            'message': 'Неизвестный тип экспорта.',
            'code': 404
        }, status=404)

    is_head = has_role(request.user, 'head')
    is_teacher = has_role(request.user, 'teacher')

    if export_type in {'department-load', 'teachers'}:
        if not is_head:
            return render(request, 'core/error.html', {
                'message': 'Экспорт кафедральных данных доступен только заведующему кафедрой.',
                'code': 403
            }, status=403)
        try:
            department = request.user.teacher.department
        except Teacher.DoesNotExist:
            return render(request, 'core/error.html', {
                'message': 'Для вашей учётной записи не указана кафедра.',
                'code': 404
            }, status=404)

        if export_type == 'department-load':
            items = LoadItem.objects.filter(
                discipline__department=department
            ).select_related('discipline', 'group', 'teacher').order_by('discipline', 'kind')
            rows = [
                (
                    item.discipline.name,
                    item.get_kind_display(),
                    item.group.name if item.group else 'Поток',
                    item.subgroup_no or '',
                    item.hours,
                    item.teacher.full_name if item.teacher else ''
                )
                for item in items
            ]
            return xlsx_response(
                'department_load.xlsx', 'Нагрузка кафедры',
                ['Дисциплина', 'Вид работы', 'Группа', 'Подгруппа', 'Часы', 'Преподаватель'], rows
            )

        teachers = Teacher.objects.filter(department=department).select_related('position')
        totals = {
            row['teacher_id']: row['total'] or Decimal('0')
            for row in LoadItem.objects.filter(teacher__in=teachers)
            .values('teacher_id').annotate(total=Sum('hours'))
        }
        rows = [
            (
                teacher.full_name,
                teacher.position.position if teacher.position else '',
                teacher.get_degree_display(),
                teacher.rank,
                teacher.rate,
                teacher.effective_min_hours,
                teacher.effective_max_hours,
                totals.get(teacher.pk, Decimal('0'))
            )
            for teacher in teachers
        ]
        return xlsx_response(
            'department_teachers.xlsx', 'Преподаватели',
            ['ФИО', 'Должность', 'Учёная степень', 'Звание', 'Ставка', 'Мин. часы', 'Макс. часы', 'Назначено часов'], rows
        )

    if export_type in {'workload', 'journal'}:
        if not is_teacher:
            return render(request, 'core/error.html', {
                'message': 'Личные данные доступны только преподавателю.',
                'code': 403
            }, status=403)
        try:
            teacher = request.user.teacher
        except Teacher.DoesNotExist:
            return render(request, 'core/error.html', {
                'message': 'Вы не зарегистрированы как преподаватель.',
                'code': 404
            }, status=404)

        if export_type == 'workload':
            items = LoadItem.objects.filter(teacher=teacher).select_related(
                'discipline', 'group'
            ).order_by('discipline', 'kind')
            rows = [
                (
                    item.discipline.name,
                    item.get_kind_display(),
                    item.group.name if item.group else 'Поток',
                    item.subgroup_no or '',
                    item.hours
                )
                for item in items
            ]
            return xlsx_response(
                'my_workload.xlsx', 'Моя нагрузка',
                ['Дисциплина', 'Вид работы', 'Группа', 'Подгруппа', 'Часы'], rows
            )

        entries = JournalEntry.objects.filter(teacher=teacher).select_related(
            'load_item__discipline', 'load_item__group'
        ).order_by('-date', '-time')
        rows = [
            (
                entry.date,
                entry.time,
                entry.load_item.discipline.name,
                entry.load_item.get_kind_display(),
                entry.load_item.group.name if entry.load_item.group else 'Поток',
                entry.hours,
                entry.topic
            )
            for entry in entries
        ]
        return xlsx_response(
            'my_journal.xlsx', 'Журнал',
            ['Дата', 'Время', 'Дисциплина', 'Вид работы', 'Группа', 'Часы', 'Тема'], rows
        )

    if is_head:
        try:
            department = request.user.teacher.department
        except Teacher.DoesNotExist:
            return render(request, 'core/error.html', {
                'message': 'Для вашей учётной записи не указана кафедра.',
                'code': 404
            }, status=404)
        teachers = Teacher.objects.filter(department=department)
        teacher_id = request.GET.get('teacher_id')
        try:
            teacher = teachers.filter(pk=int(teacher_id)).first() if teacher_id else teachers.first()
        except (TypeError, ValueError):
            teacher = None
        if teacher is None:
            return render(request, 'core/error.html', {
                'message': 'Преподаватель не найден на вашей кафедре.',
                'code': 404
            }, status=404)
    elif is_teacher:
        try:
            teacher = request.user.teacher
        except Teacher.DoesNotExist:
            return render(request, 'core/error.html', {
                'message': 'Вы не зарегистрированы как преподаватель.',
                'code': 404
            }, status=404)
    else:
        return render(request, 'core/error.html', {
            'message': 'Отчёт доступен преподавателям и заведующим кафедрой.',
            'code': 403
        }, status=403)

    try:
        year = int(request.GET.get('year', date.today().year))
        if not 1900 <= year <= 2100:
            raise ValueError
    except (TypeError, ValueError):
        return render(request, 'core/error.html', {
            'message': 'Укажите корректный год отчёта.',
            'code': 400
        }, status=400)

    report_data = report_for_teacher(teacher, year)
    adjustments = Adjustment.objects.filter(teacher=teacher, year=year).select_related('discipline')
    reasons_by_month_kind = {}
    for adjustment in adjustments:
        reasons_by_month_kind.setdefault((adjustment.month, adjustment.kind), []).append(
            f'{adjustment.discipline.name}: {adjustment.hours_delta} ч — {adjustment.reason}'
        )

    month_names = (
        'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
        'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'
    )
    kind_labels = dict(LoadItem.KIND_CHOICES)
    rows = []
    for month, kinds in report_data['months'].items():
        for kind, values in kinds.items():
            reasons = reasons_by_month_kind.get((month, kind), [])
            rows.append((
                month_names[month - 1],
                kind_labels.get(kind, kind),
                values['plan'],
                values['fact'],
                values['delta'],
                values['status'],
                '\n'.join(reasons)
            ))

    filename = f'workload_report_{year}.xlsx'
    return xlsx_response(
        filename, 'План и факт',
        ['Месяц', 'Вид работы', 'План, ч', 'Факт, ч', 'Отклонение, ч', 'Статус', 'Причины'], rows
    )


