"""
Представления (views) приложения core.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout, views as auth_views
from django.contrib.auth.decorators import login_required
from django import forms
from django.contrib import messages
from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse, Http404
from django.views.decorators.http import require_http_methods
from datetime import date
from decimal import Decimal, InvalidOperation
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from .models import (
    CalcSettings, PositionRule, Discipline, Department, Teacher, LoadItem, JournalEntry,
    Adjustment, Specialty, Curriculum, Group as AcademicGroup, TelegramProfile
)
from .telegram_utils import verify_telegram_webapp_data
from .forms import SpecialtyForm, DepartmentForm, GroupForm, CurriculumForm, DisciplineForm
from .calculations import (
    recalculate_department, auto_assign_department_load, check_teacher, teacher_total, teacher_workload,
    report_matrix, MONTH_NAMES, REPORT_TOLERANCE
)
from .importers import WorkloadImportError, import_workload_xlsx


# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============

def error_page(request, message, code=403):
    """Показать страницу ошибки (доступ запрещён, не найдено и т. п.)."""
    context = add_role_context({'message': message, 'code': code}, request)
    return render(request, 'core/error.html', context, status=code)


def csrf_failure_view(request, reason=''):
    """При истечении или сбросе CSRF-токена на странице входа — мягко перезагрузить форму входа."""
    messages.warning(request, 'Сессия формы обновилась. Пожалуйста, повторите вход.')
    if request.path.startswith('/accounts/login'):
        return redirect('login')
    return redirect(request.path or '/')


def login_view(request, *args, **kwargs):
    """Страница входа: при открытии страницы логина сбрасывает предыдущую активную сессию."""
    if request.method == 'GET' and request.user.is_authenticated:
        logout(request)
        from django.middleware.csrf import get_token
        get_token(request)
    return auth_views.LoginView.as_view()(request, *args, **kwargs)


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
        return redirect('core:dean_dashboard')
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
        return error_page(request, 'Доступ запрещён. Только зам. декана и завед. кафедрой могут менять параметры.', 403)

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
                        'Преподаватели автоматически подобраны по специализации, нормам лекций и лимитам часов.'
                    )
                elif has_role(request.user, 'deputy_dean'):
                    dept_id = request.POST.get('department_id')
                    if dept_id:
                        dept = get_object_or_404(Department, pk=dept_id)
                        recalculate_department(dept, settings)
                        messages.success(
                            request,
                            f'Нагрузка пересчитана и распределена по специализации для кафедры «{dept.name}».'
                        )
                    else:
                        departments = Department.objects.all()
                        for dept in departments:
                            recalculate_department(dept, settings)
                        messages.success(
                            request,
                            f'Нагрузка пересчитана и распределена для всех кафедр ({departments.count()}).'
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


@login_required
def data_import_view(request):
    """Загрузить исходные данные из Excel. Доступ: заместитель декана."""
    if not (has_role(request.user, 'deputy_dean') or request.user.is_staff):
        return error_page(request, 'Импортировать учебные данные может зам. декана или администратор.', 403)

    academic_year = str(date.today().year)
    selected_department = ''
    new_department_name = ''
    if request.method == 'POST':
        academic_year = request.POST.get('academic_year', '').strip()
        selected_department = request.POST.get('department_id', '').strip()
        new_department_name = request.POST.get('new_department_name', '').strip()
        upload = request.FILES.get('workload_file')
        error = None

        try:
            year = int(academic_year)
            if not 1900 <= year <= 2100:
                raise ValueError
        except ValueError:
            error = 'Укажите год начала учебного года от 1900 до 2100.'

        try:
            department_id = int(selected_department) if selected_department else None
        except ValueError:
            department_id = None
        department = Department.objects.filter(pk=department_id).first() if department_id else None
        if error is None and department and new_department_name:
            error = 'Выберите существующую кафедру или укажите новую, но не оба варианта.'
        if error is None and department is None and not new_department_name:
            error = 'Выберите существующую кафедру или укажите название новой.'
        if error is None and len(new_department_name) > 255:
            error = 'Название кафедры не должно превышать 255 символов.'
        if error is None and upload is None:
            error = 'Выберите файл Excel для загрузки.'
        if error is None and not upload.name.lower().endswith('.xlsx'):
            error = 'Поддерживаются только файлы Excel с расширением .xlsx.'

        if error:
            messages.error(request, error)
        else:
            try:
                result = import_workload_xlsx(
                    upload, department, year, department_name=new_department_name or None
                )
            except WorkloadImportError as exc:
                messages.error(request, str(exc))
            else:
                success = (
                    f"Импорт завершён: обработано строк — {result['rows']}, "
                    f"специальностей — {result['specialties']}, групп — {result['groups']}, "
                    f"дисциплин — {result['disciplines']}. Нагрузка кафедры пересчитана."
                )
                if result['warnings']:
                    success += (
                        f" Строк без номера семестра: {result['warnings']}; "
                        'они отнесены к первому семестру соответствующего курса.'
                    )
                messages.success(request, success)
                return redirect('core:data_import')

    context = {
        'departments': Department.objects.order_by('name'),
        'academic_year': academic_year,
        'selected_department': selected_department,
        'new_department_name': new_department_name,
    }
    context = add_role_context(context, request)
    return render(request, 'core/data_import.html', context)


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
        specialization_field = self.fields['specialization']
        specialization_field.queryset = Discipline.objects.filter(
            department=department
        ).select_related('curriculum__specialty').order_by(
            'name', 'curriculum__specialty__abbr', 'curriculum__admission_year', 'semester', 'pk'
        )
        self.specialization_groups = {}
        self.specialization_choice_keys = {}
        choices = []
        for discipline in specialization_field.queryset:
            specialty_abbr = discipline.curriculum.specialty.abbr
            key = (discipline.name.strip().casefold(), specialty_abbr.strip().casefold())
            group = self.specialization_groups.setdefault(key, [])
            group.append(discipline)
            if len(group) == 1:
                choices.append((discipline.pk, f'{discipline.name} ({specialty_abbr})'))
                self.specialization_choice_keys[discipline.pk] = key
        specialization_field.choices = choices
        specialization_field.required = False
        if self.instance.pk:
            existing_keys = set(self.instance.specialization.filter(
                department=department
            ).values_list('name', 'curriculum__specialty__abbr'))
            existing_keys = {(name.strip().casefold(), abbr.strip().casefold())
                             for name, abbr in existing_keys}
            specialization_field.initial = [
                choice_value for choice_value, key in self.specialization_choice_keys.items()
                if key in existing_keys
            ]
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
        selected = cleaned.get('specialization')
        if selected is not None:
            selected_keys = {
                (discipline.name.strip().casefold(),
                 discipline.curriculum.specialty.abbr.strip().casefold())
                for discipline in selected
            }
            cleaned['specialization'] = [
                discipline
                for key in selected_keys
                for discipline in self.specialization_groups[key]
            ]
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
        return error_page(request, 'Доступ запрещён. Страница доступна только зав. кафедрой.', 403)
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
                auto_assign_department_load(department)
                messages.success(
                    request,
                    f'Преподаватель «{teacher.full_name}» сохранён, нагрузка кафедры автоматически подобрана по специализации и нормам.'
                )
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
        return error_page(request, 'Доступ запрещён. Только зав. кафедрой может распределять нагрузку.', 403)

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
        .select_related(
            'discipline__curriculum__specialty',
            'discipline',
            'group',
            'teacher'
        )
        .order_by(
            'discipline__curriculum__specialty__code',
            'discipline__semester',
            'discipline__name',
            'kind'
        )
        if user_department else LoadItem.objects.none()
    )
    teachers = (
        Teacher.objects.filter(department=user_department).select_related('position')
        if user_department else Teacher.objects.none()
    )
    teacher_list = list(teachers.prefetch_related('specialization'))
    for item in load_items:
        disc_id = item.discipline_id
        candidates = []
        for t in teacher_list:
            is_spec = disc_id in {d.id for d in t.specialization.all()}
            prefix = '✓ ' if is_spec else ''
            candidates.append({
                'id': t.id,
                'name': f"{prefix}{t.full_name}",
                'is_specialized': is_spec,
                'is_current': item.teacher_id == t.id,
            })
        candidates.sort(key=lambda c: (not c['is_specialized'], c['name']))
        item.candidates = candidates

    load_item_groups = []
    for item in load_items:
        specialty = item.discipline.curriculum.specialty
        if not load_item_groups or load_item_groups[-1]['specialty'].pk != specialty.pk:
            load_item_groups.append({'specialty': specialty, 'items': []})
        load_item_groups[-1]['items'].append(item)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'recalculate':
            if not user_department:
                return error_page(request, 'Для вашей учётной записи не указана кафедра.', 404)
            settings = CalcSettings.get()
            recalculate_department(user_department, settings)
            messages.success(
                request,
                f'Нагрузка успешно пересчитана для кафедры «{user_department.name}». '
                'Преподаватели автоматически подобраны по специализации, нормам лекций и часам.'
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
        'load_item_groups': load_item_groups,
        'teachers': teachers,
        'teachers_data': teachers_data,
        'user_department': user_department,
    }
    context = add_role_context(context, request)
    return render(request, 'core/distribution.html', context)


# ============ НАГРУЗКА ПРЕПОДАВАТЕЛЯ ============

@login_required
def workload_view(request):
    """
    Страница собственной нагрузки преподавателя.
    Показывает таблицу: строки = дисциплины, столбцы = виды работ,
    итоги за осенний семестр, весенний семестр и год в целом.
    Доступ: преподаватель.
    """
    if not has_role(request.user, 'teacher'):
        return error_page(request, 'Доступ запрещён. Это страница только для преподавателей.', 403)

    try:
        teacher = request.user.teacher
    except Exception:
        teacher = None

    if not teacher:
        return error_page(request, 'Вы не зарегистрированы как преподаватель.', 404)

    workload = teacher_workload(teacher)

    context = {
        'teacher': teacher,
        'workload': workload,
        'total_hours': workload['grand_totals_total'],
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
        return error_page(request, 'Доступ запрещён. Это страница только для преподавателей.', 403)

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

    # Записи в журнале
    journal_entries = JournalEntry.objects.filter(
        teacher=teacher
    ).select_related('load_item', 'load_item__discipline', 'load_item__group').order_by('-date', '-time')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_entry':
            try:
                load_item_id = request.POST.get('load_item_id')
                raw_date = (request.POST.get('date') or '').strip()
                raw_time = (request.POST.get('time') or '').strip()
                raw_hours = (request.POST.get('hours') or '').strip()
                topic = (request.POST.get('topic') or '').strip()

                if not raw_date:
                    raise ValueError('Дата не указана.')
                entry_date = date.fromisoformat(raw_date)

                if not raw_hours:
                    raise ValueError('Часы не указаны.')
                hours_decimal = Decimal(raw_hours)
                if hours_decimal <= Decimal('0') or not hours_decimal.is_finite() or hours_decimal > Decimal('999.99'):
                    raise ValueError('Некорректное значение часов.')

                if not topic:
                    raise ValueError('Тема не указана.')

                load_item = get_object_or_404(LoadItem, pk=load_item_id, teacher=teacher)

                JournalEntry.objects.create(
                    teacher=teacher,
                    load_item=load_item,
                    date=entry_date,
                    time=raw_time if raw_time else None,
                    hours=hours_decimal,
                    topic=topic
                )

                messages.success(request, 'Запись в журнал добавлена.')
            except (ValueError, ValidationError, InvalidOperation, Http404):
                messages.error(request, 'Не удалось добавить запись: проверьте дату и часы.')

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
    Заместитель декана может выбирать любую кафедру и преподавателя.
    """
    if has_role(request.user, 'deputy_dean') or request.user.is_staff:
        raw_dept_id = request.POST.get('department_id') if request.method == 'POST' else request.GET.get('department_id')
        dept_queryset = Department.objects.all().order_by('name')
        department = None
        if raw_dept_id:
            try:
                department = dept_queryset.filter(pk=int(raw_dept_id)).first()
            except (TypeError, ValueError):
                department = None
        if not department:
            department = dept_queryset.first()

        teachers = Teacher.objects.filter(department=department) if department else Teacher.objects.none()
        if not raw_teacher_id:
            return department, teachers, None, None
        try:
            teacher = Teacher.objects.filter(pk=int(raw_teacher_id)).first()
        except (TypeError, ValueError):
            teacher = None
        if teacher is None:
            return None, None, None, error_page(
                request, 'Преподаватель не найден.', 404
            )
        return teacher.department, teachers, teacher, None

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
    is_deputy_dean = has_role(request.user, 'deputy_dean')
    if not (is_head or is_teacher or is_deputy_dean or request.user.is_staff):
        return error_page(request, 'Доступ запрещён. Отчёт доступен преподавателям, заведующим кафедрой и деканату.', 403)

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
                    or hours_delta <= Decimal('0')
                    or hours_delta > Decimal('999.99')
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
        'all_departments': Department.objects.order_by('name') if (is_deputy_dean or request.user.is_staff) else None,
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
            wl = teacher_workload(teacher)
            kind_labels = [label for _, label in wl['kinds']]
            cent = Decimal('0.01')
            headers = ['Дисциплина', 'Семестр', 'Группы'] + kind_labels + ['Всего']
            rows = []
            for row in wl['rows']:
                rows.append(
                    [row['discipline'].name if row['discipline'] else '', row['semester'], row['groups']]
                    + [h.quantize(cent) for h in row['hours_list']]
                    + [row['total'].quantize(cent)]
                )
            # Итоговые строки
            rows.append(
                ['Всего за осенний семестр', '', '']
                + [h.quantize(cent) for h in wl['fall_totals_list']]
                + [wl['fall_totals_total'].quantize(cent)]
            )
            rows.append(
                ['Всего за весенний семестр', '', '']
                + [h.quantize(cent) for h in wl['spring_totals_list']]
                + [wl['spring_totals_total'].quantize(cent)]
            )
            rows.append(
                ['ИТОГО ЗА ГОД', '', '']
                + [h.quantize(cent) for h in wl['grand_totals_list']]
                + [wl['grand_totals_total'].quantize(cent)]
            )
            return xlsx_response('my_workload.xlsx', 'Моя нагрузка', headers, rows)

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

    if not (is_head or is_teacher or has_role(request.user, 'deputy_dean') or request.user.is_staff):
        return error_page(request, 'Отчёт доступен преподавателям, заведующим кафедрой и деканату.', 403)

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


# ============ ДЕКАНАТ (интерфейс зам. декана) ============

def _require_deputy_dean(request):
    """Проверка прав: только зам. декана или администратор."""
    if not (has_role(request.user, 'deputy_dean') or request.user.is_staff):
        return error_page(request, 'Доступ запрещён. Этот раздел предназначен для заместителя декана.', 403)
    return None


@login_required
def dean_dashboard_view(request):
    """Главная панель деканата со статистикой и быстрыми ссылками."""
    access_error = _require_deputy_dean(request)
    if access_error:
        return access_error

    stats = {
        'specialties_count': Specialty.objects.count(),
        'departments_count': Department.objects.count(),
        'groups_count': AcademicGroup.objects.count(),
        'curricula_count': Curriculum.objects.count(),
        'disciplines_count': Discipline.objects.count(),
    }
    context = add_role_context({'stats': stats}, request)
    return render(request, 'core/dean/dashboard.html', context)


@login_required
def specialties_view(request):
    """Справочник специальностей: список, добавление, редактирование, удаление."""
    access_error = _require_deputy_dean(request)
    if access_error:
        return access_error

    editing = None
    edit_id = request.GET.get('edit')
    if edit_id:
        editing = get_object_or_404(Specialty, pk=edit_id)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete':
            spec_id = request.POST.get('specialty_id')
            spec = get_object_or_404(Specialty, pk=spec_id)
            name = spec.name
            spec.delete()
            messages.success(request, f'Специальность «{name}» успешно удалена.')
            return redirect('core:specialties')

        if action == 'save':
            spec_id = request.POST.get('specialty_id')
            instance = get_object_or_404(Specialty, pk=spec_id) if spec_id else None
            form = SpecialtyForm(request.POST, instance=instance)
            if form.is_valid():
                form.save()
                messages.success(request, 'Специальность сохранена.')
                return redirect('core:specialties')
    else:
        form = SpecialtyForm(instance=editing)

    context = add_role_context({
        'specialties': Specialty.objects.all().order_by('code'),
        'form': form,
        'editing': editing,
    }, request)
    return render(request, 'core/dean/specialties.html', context)


@login_required
def departments_view(request):
    """Справочник кафедр: список, добавление, редактирование, удаление."""
    access_error = _require_deputy_dean(request)
    if access_error:
        return access_error

    editing = None
    edit_id = request.GET.get('edit')
    if edit_id:
        editing = get_object_or_404(Department, pk=edit_id)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete':
            dept_id = request.POST.get('department_id')
            dept = get_object_or_404(Department, pk=dept_id)
            name = dept.name
            dept.delete()
            messages.success(request, f'Кафедра «{name}» успешно удалена.')
            return redirect('core:departments')

        if action == 'save':
            dept_id = request.POST.get('department_id')
            instance = get_object_or_404(Department, pk=dept_id) if dept_id else None
            form = DepartmentForm(request.POST, instance=instance)
            if form.is_valid():
                form.save()
                messages.success(request, 'Кафедра сохранена.')
                return redirect('core:departments')
    else:
        form = DepartmentForm(instance=editing)

    context = add_role_context({
        'departments': Department.objects.select_related('head').order_by('name'),
        'form': form,
        'editing': editing,
    }, request)
    return render(request, 'core/dean/departments.html', context)


@login_required
def groups_view(request):
    """Справочник групп: список с фильтром по специальности, добавление, редактирование, удаление."""
    access_error = _require_deputy_dean(request)
    if access_error:
        return access_error

    selected_specialty = request.GET.get('specialty', '').strip()
    groups_qs = AcademicGroup.objects.select_related('specialty').order_by('course', 'name')
    if selected_specialty:
        groups_qs = groups_qs.filter(specialty_id=selected_specialty)

    editing = None
    edit_id = request.GET.get('edit')
    if edit_id:
        editing = get_object_or_404(AcademicGroup, pk=edit_id)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete':
            group_id = request.POST.get('group_id')
            grp = get_object_or_404(AcademicGroup, pk=group_id)
            name = grp.name
            grp.delete()
            messages.success(request, f'Группа «{name}» успешно удалена.')
            return redirect('core:groups')

        if action == 'save':
            group_id = request.POST.get('group_id')
            instance = get_object_or_404(AcademicGroup, pk=group_id) if group_id else None
            form = GroupForm(request.POST, instance=instance)
            if form.is_valid():
                form.save()
                messages.success(request, 'Группа сохранена.')
                return redirect('core:groups')
    else:
        form = GroupForm(instance=editing)

    context = add_role_context({
        'groups': groups_qs,
        'specialties': Specialty.objects.order_by('name'),
        'selected_specialty': selected_specialty,
        'form': form,
        'editing': editing,
    }, request)
    return render(request, 'core/dean/groups.html', context)


@login_required
def curricula_view(request):
    """Учебные планы: список, добавление, редактирование, удаление."""
    access_error = _require_deputy_dean(request)
    if access_error:
        return access_error

    selected_specialty = request.GET.get('specialty', '').strip()
    curricula_qs = Curriculum.objects.select_related('specialty').prefetch_related('disciplines').order_by('-admission_year', 'specialty__code')
    if selected_specialty:
        curricula_qs = curricula_qs.filter(specialty_id=selected_specialty)

    editing = None
    edit_id = request.GET.get('edit')
    if edit_id:
        editing = get_object_or_404(Curriculum, pk=edit_id)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete':
            cur_id = request.POST.get('curriculum_id')
            cur = get_object_or_404(Curriculum, pk=cur_id)
            str_repr = str(cur)
            cur.delete()
            messages.success(request, f'Учебный план «{str_repr}» успешно удалён.')
            return redirect('core:curricula')

        if action == 'save':
            cur_id = request.POST.get('curriculum_id')
            instance = get_object_or_404(Curriculum, pk=cur_id) if cur_id else None
            form = CurriculumForm(request.POST, instance=instance)
            if form.is_valid():
                form.save()
                messages.success(request, 'Учебный план сохранён.')
                return redirect('core:curricula')
    else:
        form = CurriculumForm(instance=editing)

    context = add_role_context({
        'curricula': curricula_qs,
        'specialties': Specialty.objects.order_by('name'),
        'selected_specialty': selected_specialty,
        'form': form,
        'editing': editing,
    }, request)
    return render(request, 'core/dean/curricula.html', context)


@login_required
def disciplines_view(request):
    """Справочник дисциплин: список с поиском и фильтрацией, добавление, редактирование, удаление."""
    access_error = _require_deputy_dean(request)
    if access_error:
        return access_error

    disciplines_qs = Discipline.objects.select_related(
        'curriculum__specialty', 'department'
    ).prefetch_related('groups').order_by('name', 'semester')

    search_query = request.GET.get('q', '').strip()
    if search_query:
        disciplines_qs = disciplines_qs.filter(name__icontains=search_query)

    selected_department = request.GET.get('department', '').strip()
    if selected_department:
        disciplines_qs = disciplines_qs.filter(department_id=selected_department)

    selected_curriculum = request.GET.get('curriculum', '').strip()
    if selected_curriculum:
        disciplines_qs = disciplines_qs.filter(curriculum_id=selected_curriculum)

    editing = None
    edit_id = request.GET.get('edit')
    if edit_id:
        editing = get_object_or_404(Discipline, pk=edit_id)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete':
            disc_id = request.POST.get('discipline_id')
            disc = get_object_or_404(Discipline, pk=disc_id)
            name = disc.name
            disc.delete()
            messages.success(request, f'Дисциплина «{name}» успешно удалена.')
            return redirect('core:disciplines')

        if action == 'save':
            disc_id = request.POST.get('discipline_id')
            instance = get_object_or_404(Discipline, pk=disc_id) if disc_id else None
            form = DisciplineForm(request.POST, instance=instance)
            if form.is_valid():
                form.save()
                messages.success(request, 'Дисциплина сохранена.')
                return redirect('core:disciplines')
    else:
        form = DisciplineForm(instance=editing)

    context = add_role_context({
        'disciplines': disciplines_qs,
        'departments': Department.objects.order_by('name'),
        'curricula': Curriculum.objects.select_related('specialty').order_by('specialty__code', 'admission_year'),
        'selected_department': selected_department,
        'selected_curriculum': selected_curriculum,
        'search_query': search_query,
        'form': form,
        'editing': editing,
    }, request)
    return render(request, 'core/dean/disciplines.html', context)


# ============ TELEGRAM MINI APP (WEBAPP) ============

def telegram_webapp_view(request):
    """
    Точка входа для Telegram Mini App (WebApp), открываемого прямо внутри чата.
    Проверяет криптографическую подпись initData от Telegram и автоматически
    авторизует привязанного пользователя или позволяет привязать аккаунт.
    """
    error_msg = None
    tg_user_info = None

    if request.method == 'POST':
        init_data = request.POST.get('init_data', '').strip()
        action = request.POST.get('action', 'auth')
        verified = verify_telegram_webapp_data(init_data)

        if not verified or not isinstance(verified.get('user'), dict):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'ok': False, 'error': 'Неверная или устаревшая подпись Telegram initData.'}, status=403)
            error_msg = 'Не удалось подтвердить подпись Telegram Mini App. Откройте приложение напрямую через кнопку в чате бота.'
        else:
            tg_user = verified['user']
            tg_id = tg_user.get('id')
            tg_username = tg_user.get('username', '') or ''
            tg_first_name = tg_user.get('first_name', '') or ''
            tg_user_info = tg_user

            if action == 'link':
                username = request.POST.get('username', '').strip()
                password = request.POST.get('password', '')
                user = authenticate(request, username=username, password=password)
                if user is not None:
                    TelegramProfile.objects.update_or_create(
                        user=user,
                        defaults={
                            'telegram_id': tg_id,
                            'telegram_username': tg_username,
                            'first_name': tg_first_name,
                        }
                    )
                    login(request, user)
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({'ok': True, 'redirect': '/'})
                    return redirect('/')
                else:
                    error_msg = 'Неверный логин или пароль учётной записи S.C.A.L.E.'
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({'ok': False, 'need_link': True, 'error': error_msg}, status=401)
            else:
                profile = TelegramProfile.objects.filter(telegram_id=tg_id).select_related('user').first()
                if profile:
                    if profile.telegram_username != tg_username or profile.first_name != tg_first_name:
                        profile.telegram_username = tg_username
                        profile.first_name = tg_first_name
                        profile.save(update_fields=['telegram_username', 'first_name'])
                    login(request, profile.user)
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({'ok': True, 'redirect': '/'})
                    return redirect('/')
                else:
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({
                            'ok': False,
                            'need_link': True,
                            'tg_user': {
                                'id': tg_id,
                                'username': tg_username,
                                'first_name': tg_first_name,
                            }
                        })

    context = add_role_context({
        'error_msg': error_msg,
        'tg_user_info': tg_user_info,
    }, request)
    return render(request, 'core/telegram_webapp.html', context)





