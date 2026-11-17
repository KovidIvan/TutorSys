"""
Представления (views) приложения core.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django import forms
from django.contrib import messages
from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods
from datetime import date
from decimal import Decimal, InvalidOperation
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from .models import (
    CalcSettings, PositionRule, Discipline, Department, Teacher, LoadItem, JournalEntry,
    Adjustment
)
from .calculations import (
    recalculate_department, check_teacher, teacher_total, report_matrix,
    MONTH_NAMES, REPORT_TOLERANCE
)


# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============

def error_page(request, message, code=403):
    """Показать страницу ошибки (доступ запрещён, не найдено и т. п.)."""
    return render(request, 'core/error.html', {'message': message, 'code': code}, status=code)


def has_role(user, role_name):
    """Проверить, есть ли у пользователя указанная роль."""
    return user.groups.filter(name=role_name).exists()


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
    if user.is_authenticated and hasattr(user, 'teacher'):
        context['user_department'] = user.teacher.department
    return context


@login_required
def home_view(request):
    if has_role(request.user, 'deputy_dean'):
        return redirect('core:settings')
    if has_role(request.user, 'head'):
        return redirect('core:distribution')
    if has_role(request.user, 'teacher'):
        return redirect('core:workload')
    if request.user.is_staff:
        return redirect('/admin/')
    return error_page(request, 'Для вашей учётной записи не назначена роль.', 403)


def check_teacher_status(teacher):
    """
    Получить статус преподавателя (OK, Warning, Critical).
    Возвращает статус и список предупреждений.
    """
    total = teacher_total(teacher)
    warnings = check_teacher(teacher)

    if total > Decimal(str(teacher.effective_max_hours)):
        return 'critical', warnings
    if warnings:
        return 'warning', warnings
    return 'ok', []


def _teacher_panel_data(department):
    if department is None:
        return []

    teachers = Teacher.objects.filter(department=department).select_related('position')
    teachers_data = []
    for teacher in teachers:
        status, warnings = check_teacher_status(teacher)
        teachers_data.append({
            'teacher': teacher,
            'total_hours': teacher_total(teacher),
            'status': status,
            'warnings': warnings,
        })
    return teachers_data


# ============ ПАРАМЕТРЫ РАСЧЁТА ============

@login_required
def settings_view(request):
    """
    Страница настроек параметров расчёта и норм по должностям.

    Доступ: зам. декана и зав. кафедрой.
    """
    # Проверка прав доступа
    if not (has_role(request.user, 'deputy_dean') or has_role(request.user, 'head')):
        return error_page(request, '❌ Доступ запрещён. Только зам. декана и завед. кафедрой могут менять параметры.', 403)

    settings = CalcSettings.get()
    position_rules = PositionRule.objects.all()

    if request.method == 'POST':
        action = request.POST.get('action')

        # ============ ОБНОВЛЕНИЕ CALCSETTINGS ============
        if action == 'update_settings':
            try:
                settings.exam_hours_per_student = request.POST.get('exam_hours_per_student')
                settings.exam_consult_hours_per_group = request.POST.get('exam_consult_hours_per_group')
                settings.credit_hours_per_student = request.POST.get('credit_hours_per_student')
                settings.usr_control_hours_per_group = request.POST.get('usr_control_hours_per_group')
                settings.kr_hours_per_student = request.POST.get('kr_hours_per_student')
                settings.subgroups_per_group = request.POST.get('subgroups_per_group')
                settings.save()

                messages.success(
                    request,
                    'Параметры расчёта обновлены. '
                    'Для пересчёта нагрузки кафедр используйте кнопку ниже.'
                )
            except Exception as e:
                messages.error(request, f'Ошибка при сохранении параметров: {e}')

        # ============ ПЕРЕСЧЁТ НАГРУЗКИ ============
        elif action in ('recalculate_all', 'recalculate_department'):
            try:
                if has_role(request.user, 'head'):
                    # Зав. кафедрой пересчитывает часы ТОЛЬКО для своей кафедры
                    try:
                        dept = request.user.teacher.department
                    except (Teacher.DoesNotExist, AttributeError):
                        return error_page(request, 'Для вашей учётной записи не указана кафедра.', 404)
                    recalculate_department(dept, settings)
                    messages.success(
                        request,
                        f'Нагрузка успешно пересчитана только для кафедры «{dept.name}». '
                        'Назначенные преподаватели сохранены.'
                    )
                elif has_role(request.user, 'deputy_dean'):
                    dept_id = request.POST.get('department_id')
                    if dept_id:
                        dept = get_object_or_404(Department, pk=dept_id)
                        recalculate_department(dept, settings)
                        messages.success(
                            request,
                            f'Нагрузка пересчитана для кафедры «{dept.name}».'
                        )
                    else:
                        departments = Department.objects.all()
                        for dept in departments:
                            recalculate_department(dept, settings)
                        messages.success(
                            request,
                            f'Нагрузка пересчитана для всех кафедр ({departments.count()}). '
                            'Назначенные преподаватели сохранены.'
                        )
            except Exception as e:
                messages.error(request, f'Ошибка при пересчёте: {e}')

        # ============ ОБНОВЛЕНИЕ ДОЛЖНОСТИ ============
        elif action == 'update_position':
            position_id = request.POST.get('position_id')
            try:
                position = get_object_or_404(PositionRule, pk=position_id)
                position.min_hours = request.POST.get('min_hours')
                position.max_hours = request.POST.get('max_hours')
                position.min_lecture_hours = request.POST.get('min_lecture_hours')
                position.can_lecture = request.POST.get('can_lecture') == 'on'
                position.save()

                messages.success(request, f'Должность "{position.position}" обновлена.')
            except Exception as e:
                messages.error(request, f'Ошибка при сохранении должности: {e}')

        # ============ ДОБАВЛЕНИЕ НОВОЙ ДОЛЖНОСТИ ============
        elif action == 'add_position':
            try:
                position_name = request.POST.get('new_position_name')
                if PositionRule.objects.filter(position=position_name).exists():
                    messages.error(request, f'Должность "{position_name}" уже существует.')
                else:
                    PositionRule.objects.create(
                        position=position_name,
                        min_hours=int(request.POST.get('new_min_hours', 840)),
                        max_hours=int(request.POST.get('new_max_hours', 900)),
                        min_lecture_hours=int(request.POST.get('new_min_lecture_hours', 200)),
                        can_lecture=request.POST.get('new_can_lecture') == 'on'
                    )
                    messages.success(request, f'Должность "{position_name}" добавлена.')
            except Exception as e:
                messages.error(request, f'Ошибка при добавлении должности: {e}')

        return redirect('core:settings')

    context = {
        'settings': settings,
        'position_rules': position_rules,
        'all_departments': Department.objects.all(),
    }
    context = add_role_context(context, request)
    return render(request, 'core/settings.html', context)


# ============ ПРЕПОДАВАТЕЛИ (зав. кафедрой) ============

class TeacherForm(forms.ModelForm):
    """Форма преподавателя; логин и пароль нужны при создании, пароль при правке — по желанию."""
    username = forms.CharField(label='Логин', max_length=150, required=False)
    password = forms.CharField(
        label='Пароль', required=False, widget=forms.PasswordInput(render_value=False),
        help_text='При правке оставьте пустым, чтобы не менять пароль.'
    )

    class Meta:
        model = Teacher
        fields = ['full_name', 'position', 'degree', 'rank', 'rate', 'specialization']
        widgets = {'specialization': forms.CheckboxSelectMultiple}

    def __init__(self, *args, department=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.department = department
        self.fields['specialization'].queryset = Discipline.objects.filter(department=department).order_by('name')
        self.fields['specialization'].required = False
        self.fields['position'].required = True
        self.fields['position'].queryset = PositionRule.objects.all()
        if self.instance.pk:
            self.fields['username'].initial = self.instance.user.username
            self.fields['username'].disabled = True
        for name, field in self.fields.items():
            if name != 'specialization':
                field.widget.attrs.setdefault('class', 'form-select' if isinstance(field, forms.ModelChoiceField) or name == 'degree' else 'form-control')

    def clean_username(self):
        if self.instance.pk:
            return self.instance.user.username
        username = (self.cleaned_data.get('username') or '').strip()
        if not username:
            raise forms.ValidationError('Укажите логин.')
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('Такой логин уже занят.')
        return username

    def clean_rate(self):
        rate = self.cleaned_data['rate']
        if rate <= 0 or rate > Decimal('1.5'):
            raise forms.ValidationError('Ставка должна быть в диапазоне от 0,01 до 1,5.')
        return rate

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get('password')
        if not self.instance.pk and not password:
            self.add_error('password', 'Задайте пароль.')
        if password:
            user = self.instance.user if self.instance.pk else User(username=cleaned.get('username') or '')
            try:
                validate_password(password, user)
            except forms.ValidationError as exc:
                self.add_error('password', exc)
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        teacher = super().save(commit=False)
        teacher.department = self.department
        password = self.cleaned_data.get('password')
        if teacher.pk:
            if password:
                teacher.user.set_password(password)
                teacher.user.save()
        else:
            user = User.objects.create_user(username=self.cleaned_data['username'], password=password)
            user.groups.add(Group.objects.get_or_create(name='teacher')[0])
            teacher.user = user
        teacher.save()
        self.save_m2m()
        return teacher


@login_required
def teachers_view(request):
    """Список преподавателей кафедры, добавление, правка и удаление. Доступ: зав. кафедрой."""
    if not has_role(request.user, 'head'):
        return error_page(request, '❌ Доступ запрещён. Страница доступна только зав. кафедрой.', 403)
    try:
        department = request.user.teacher.department
    except Teacher.DoesNotExist:
        return error_page(request, 'Ваша учётная запись не привязана к кафедре.', 403)

    teachers_qs = Teacher.objects.filter(department=department)
    edit_id = request.GET.get('edit')
    instance = None

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete':
            target = get_object_or_404(teachers_qs, pk=request.POST.get('teacher_id'))
            if target.pk == request.user.teacher.pk or department.head_id == target.pk:
                messages.error(request, 'Нельзя удалить заведующего кафедрой.')
            else:
                name = target.full_name
                with transaction.atomic():
                    user = target.user
                    target.delete()
                    user.delete()
                messages.success(request, f'Преподаватель «{name}» удалён вместе с его нагрузкой и журналом.')
            return redirect('core:teachers')
        if action == 'save':
            raw_id = request.POST.get('teacher_id')
            instance = get_object_or_404(teachers_qs, pk=raw_id) if raw_id else None
            form = TeacherForm(request.POST, instance=instance, department=department)
            if form.is_valid():
                teacher = form.save()
                messages.success(request, f'Преподаватель «{teacher.full_name}» сохранён.')
                return redirect('core:teachers')
        else:
            return error_page(request, 'Неизвестное действие.', 400)
    else:
        if edit_id:
            instance = get_object_or_404(teachers_qs, pk=edit_id)
        form = TeacherForm(instance=instance, department=department)

    context = {
        'department': department,
        'teachers': teachers_qs.select_related('position', 'user').order_by('full_name'),
        'form': form,
        'editing': instance,
    }
    context = add_role_context(context, request)
    return render(request, 'core/teachers.html', context)


# ============ РАСПРЕДЕЛЕНИЕ НАГРУЗКИ ============

@login_required
def distribution_view(request):
    """
    Страница распределения нагрузки по преподавателям.
    Доступ: зав. кафедрой.
    """
    # Проверка прав доступа
    if not has_role(request.user, 'head'):
        return error_page(request, '❌ Доступ запрещён. Только зав. кафедрой может распределять нагрузку.', 403)

    try:
        user_department = request.user.teacher.department
    except Teacher.DoesNotExist:
        user_department = None

    disciplines = (
        Discipline.objects.filter(department=user_department).order_by('name')
        if user_department else Discipline.objects.none()
    )
    load_items = (
        LoadItem.objects.filter(discipline__department=user_department)
        .select_related('discipline', 'group', 'teacher').order_by('discipline', 'kind')
        if user_department else LoadItem.objects.none()
    )
    teachers = (
        Teacher.objects.filter(department=user_department).select_related('position')
        if user_department else Teacher.objects.none()
    )

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'recalculate':
            if not user_department:
                return error_page(request, 'Для вашей учётной записи не указана кафедра.', 404)
            settings = CalcSettings.get()
            recalculate_department(user_department, settings)
            messages.success(
                request,
                f'Нагрузка успешно пересчитана только для кафедры «{user_department.name}». '
                'Назначения преподавателей сохранены.'
            )
            return redirect('core:distribution')

        load_item = get_object_or_404(
            LoadItem.objects.select_related('discipline'),
            pk=request.POST.get('load_item_id')
        )
        if not user_department or load_item.discipline.department_id != user_department.pk:
            return error_page(request, 'Нельзя менять нагрузку другой кафедры.', 403)

        teacher_id = request.POST.get('teacher_id')
        teacher = get_object_or_404(Teacher, pk=teacher_id) if teacher_id else None
        if teacher and teacher.department_id != user_department.pk:
            return error_page(request, 'Нельзя назначить преподавателя другой кафедры.', 403)

        load_item.teacher = teacher
        load_item.save()

        if request.htmx:
            return render(request, 'core/_teacher_panel.html', {
                'teachers_data': _teacher_panel_data(user_department),
            })

        messages.success(request, 'Назначение обновлено.')
        return redirect('core:distribution')

    teachers_data = _teacher_panel_data(user_department)

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
        return error_page(request, '❌ Доступ запрещён.', 403)

    try:
        user_department = request.user.teacher.department
    except Teacher.DoesNotExist:
        user_department = None

    return render(request, 'core/_teacher_panel.html', {
        'teachers_data': _teacher_panel_data(user_department),
    })


# ============ НАГРУЗКА ПРЕПОДАВАТЕЛЯ ============

@login_required
def workload_view(request):
    """
    Страница собственной нагрузки преподавателя.
    Доступ: преподаватель.
    """
    # Проверка прав доступа
    if not has_role(request.user, 'teacher'):
        return error_page(request, '❌ Доступ запрещён. Это страница только для преподавателей.', 403)

    try:
        teacher = request.user.teacher
    except:
        teacher = None

    if not teacher:
        return error_page(request, 'Вы не зарегистрированы как преподаватель.', 404)

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
        return error_page(request, '❌ Доступ запрещён. Это страница только для преподавателей.', 403)

    try:
        teacher = request.user.teacher
    except:
        teacher = None

    if not teacher:
        return error_page(request, 'Вы не зарегистрированы как преподаватель.', 404)

    # Получить нагрузку преподавателя
    load_items = LoadItem.objects.filter(
        teacher=teacher
    ).select_related('discipline', 'group').order_by('discipline')

    # Запросы в журнале
    journal_entries = JournalEntry.objects.filter(
        teacher=teacher
    ).select_related('load_item', 'load_item__discipline').order_by('-date')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_entry':
            try:
                load_item_id = request.POST.get('load_item_id')
                date = request.POST.get('date')
                time = request.POST.get('time')
                hours = request.POST.get('hours')
                topic = request.POST.get('topic')

                load_item = get_object_or_404(LoadItem, pk=load_item_id, teacher=teacher)

                JournalEntry.objects.create(
                    teacher=teacher,
                    load_item=load_item,
                    date=date,
                    time=time if time else None,
                    hours=hours,
                    topic=topic
                )

                messages.success(request, 'Запись в журнал добавлена.')
            except Exception as e:
                messages.error(request, f'Ошибка при добавлении запроса: {e}')

        return redirect('core:journal')

    context = {
        'teacher': teacher,
        'load_items': load_items,
        'journal_entries': journal_entries,
    }
    context = add_role_context(context, request)
    return render(request, 'core/journal.html', context)


# ============ ОТЧЁТ ПЛАН/ФАКТ ============

def _report_subject(request, raw_teacher_id):
    """
    Определить, чей отчёт строить.
    Возвращает (кафедра, преподаватели кафедры или None, преподаватель или None, ответ-ошибка или None).
    Заведующий без teacher_id видит всю кафедру (преподаватель = None);
    преподаватель всегда видит только себя.
    """
    if has_role(request.user, 'head'):
        try:
            department = request.user.teacher.department
        except Teacher.DoesNotExist:
            return None, None, None, error_page(
                request, 'Для вашей учётной записи не указана кафедра.', 404
            )
        teachers = Teacher.objects.filter(department=department)
        if not raw_teacher_id:
            return department, teachers, None, None
        try:
            teacher = teachers.filter(pk=int(raw_teacher_id)).first()
        except (TypeError, ValueError):
            teacher = None
        if teacher is None:
            return None, None, None, error_page(
                request, 'Преподаватель не найден на вашей кафедре.', 404
            )
        return department, teachers, teacher, None

    try:
        teacher = request.user.teacher
    except Teacher.DoesNotExist:
        return None, None, None, error_page(
            request, 'Вы не зарегистрированы как преподаватель.', 404
        )
    return teacher.department, None, teacher, None


@login_required
def report_view(request):
    """
    Отчёт «Выполнение нагрузки»: матрица «месяц × виды работ» (план/факт) за год.
    Преподаватель видит свой отчёт, заведующий — всю кафедру или выбранного преподавателя.
    """
    is_head = has_role(request.user, 'head')
    is_teacher = has_role(request.user, 'teacher')
    if not (is_head or is_teacher):
        return error_page(request, '❌ Доступ запрещён. Отчёт доступен преподавателям и заведующим кафедрой.', 403)

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

    raw_teacher_id = request.POST.get('teacher_id') if request.method == 'POST' else request.GET.get('teacher_id')
    department, teachers, teacher, error = _report_subject(request, raw_teacher_id)
    if error:
        return error

    if request.method == 'POST':
        if teacher is None:
            messages.error(request, 'Выберите преподавателя, чтобы добавить корректировку.')
        elif request.POST.get('action') == 'add_adjustment' and valid_year:
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
        if is_head and teacher is not None:
            redirect_url += f'&teacher_id={teacher.pk}'
        return redirect(redirect_url)

    matrix = report_matrix([teacher] if teacher else teachers, year)
    context = {
        'teacher': teacher,
        'teachers': teachers,
        'department': department,
        'report_title': teacher.full_name if teacher else 'Вся кафедра',
        'year': year,
        'years': sorted(set(range(current_year - 2, current_year + 2)) | {year}),
        'matrix': matrix,
        'details': {
            'showTeacher': teacher is None,
            'tolerance': str(REPORT_TOLERANCE),
            'rows': matrix['details'],
        },
        'tolerance': REPORT_TOLERANCE,
        'month_choices': [(number, MONTH_NAMES[number - 1]) for number in range(1, 13)],
    }
    if teacher is not None:
        planned_kinds = set(LoadItem.objects.filter(teacher=teacher).values_list('kind', flat=True))
        context['disciplines'] = Discipline.objects.filter(load_items__teacher=teacher).distinct()
        context['kind_choices'] = [
            (kind, label) for kind, label in LoadItem.KIND_CHOICES if kind in planned_kinds
        ]
    context = add_role_context(context, request)
    return render(request, 'core/report.html', context)


def _fill_sheet(worksheet, headers, rows):
    """Заполнить лист: шапка, строки (защита от формул), фильтр и ширина столбцов."""
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


def xlsx_response(filename, sheet_name, headers, rows, extra_sheets=()):
    """
    Сформировать XLSX-файл из заголовков и строк данных.
    extra_sheets — дополнительные листы: [(название, заголовки, строки), ...].
    """
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name[:31]
    _fill_sheet(worksheet, headers, rows)

    for extra_name, extra_headers, extra_rows in extra_sheets:
        _fill_sheet(workbook.create_sheet(extra_name[:31]), extra_headers, extra_rows)

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
        return error_page(request, 'Неизвестный тип экспорта.', 404)

    is_head = has_role(request.user, 'head')
    is_teacher = has_role(request.user, 'teacher')

    if export_type in {'department-load', 'teachers'}:
        if not is_head:
            return error_page(request, 'Экспорт кафедральных данных доступен только заведующему кафедрой.', 403)
        try:
            department = request.user.teacher.department
        except Teacher.DoesNotExist:
            return error_page(request, 'Для вашей учётной записи не указана кафедра.', 404)

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
        rows = [
            (
                teacher.full_name,
                teacher.position.position if teacher.position else '',
                teacher.get_degree_display(),
                teacher.rank,
                teacher.rate,
                teacher.effective_min_hours,
                teacher.effective_max_hours,
                teacher_total(teacher)
            )
            for teacher in teachers
        ]
        return xlsx_response(
            'department_teachers.xlsx', 'Преподаватели',
            ['ФИО', 'Должность', 'Учёная степень', 'Звание', 'Ставка', 'Мин. часы', 'Макс. часы', 'Назначено часов'], rows
        )

    if export_type in {'workload', 'journal'}:
        if not is_teacher:
            return error_page(request, 'Личные данные доступны только преподавателю.', 403)
        try:
            teacher = request.user.teacher
        except Teacher.DoesNotExist:
            return error_page(request, 'Вы не зарегистрированы как преподаватель.', 404)

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

    if not (is_head or is_teacher):
        return error_page(request, 'Отчёт доступен преподавателям и заведующим кафедрой.', 403)

    department, teachers, teacher, error = _report_subject(request, request.GET.get('teacher_id'))
    if error:
        return error

    try:
        year = int(request.GET.get('year', date.today().year))
        if not 1900 <= year <= 2100:
            raise ValueError
    except (TypeError, ValueError):
        return error_page(request, 'Укажите корректный год отчёта.', 400)

    matrix = report_matrix([teacher] if teacher else teachers, year)
    cent = Decimal('0.01')

    def matrix_rows(field):
        rows = [
            [row['name']]
            + [cell[field].quantize(cent) for cell in row['cells']]
            + [row['total'][field].quantize(cent)]
            for row in matrix['rows']
        ]
        rows.append(
            ['Итого за год']
            + [cell[field].quantize(cent) for cell in matrix['totals']['cells']]
            + [matrix['totals']['total'][field].quantize(cent)]
        )
        return rows

    headers = ['Месяц'] + [label for _, label in matrix['kinds']] + ['Всего']
    adjustment_rows = [
        [
            MONTH_NAMES[item['month'] - 1], item['teacher'], item['discipline'], item['kind'],
            item['hours'], 'Плановая' if item['planned'] else 'Внеплановая', item['reason']
        ]
        for item in matrix['adjustments']
    ]
    filename = f'workload_report_{year}.xlsx' if teacher else f'department_report_{year}.xlsx'
    return xlsx_response(
        filename, 'Факт', headers, matrix_rows('fact'),
        extra_sheets=[
            ('План', headers, matrix_rows('plan')),
            ('Отклонение', headers, matrix_rows('delta')),
            ('Корректировки',
             ['Месяц', 'Преподаватель', 'Дисциплина', 'Вид работы', 'Часы', 'Тип', 'Причина'],
             adjustment_rows),
        ]
    )


@login_required
@require_http_methods(["GET"])
def load_kinds_for_discipline_view(request):
    """
    AJAX endpoint: получить виды работ для дисциплины (для зависимого списка).
    """
    if not has_role(request.user, 'teacher'):
        return JsonResponse({'error': 'Access denied'}, status=403)

    try:
        teacher = request.user.teacher
    except:
        return JsonResponse({'error': 'Not a teacher'}, status=404)

    load_item_id = request.GET.get('load_item_id')

    try:
        load_item = LoadItem.objects.get(
            pk=load_item_id,
            teacher=teacher
        )

        # Получить все элементы этой дисциплины для этого преподавателя
        discipline_items = LoadItem.objects.filter(
            discipline=load_item.discipline,
            teacher=teacher
        ).values_list('id', 'get_kind_display').distinct()

        items_list = [
            {'id': item[0], 'kind': item[1]}
            for item in discipline_items
        ]

        return JsonResponse({'items': items_list})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)
