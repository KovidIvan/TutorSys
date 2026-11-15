"""
Модуль расчёта учебной нагрузки.
Все функции принимают параметры через аргументы (из CalcSettings или PositionRule).
Никаких захардкоженных норм.
"""

from decimal import Decimal
from typing import List, Dict
from django.db import transaction
from django.db.models import Count, Q, Sum
from .models import LoadItem, Teacher, CalcSettings, Discipline


# ============ РАСЧЁТ НАГРУЗКИ ПО ДИСЦИПЛИНЕ ============

def calc_department_load(discipline: Discipline, settings: CalcSettings) -> List[Dict]:
    """
    Рассчитать элементы нагрузки для дисциплины.

    Возвращает список словарей с ключами:
    {
        'kind': str,           # 'lec' / 'pract' / 'lab' / 'exam' / 'credit' / 'usr' / 'kr'
        'group': Group или None,
        'subgroup_no': int или None,
        'hours': Decimal,
    }

    Правила:
    - Лекции: один раз на поток (все группы дисциплины)
    - Практические: на каждую группу
    - Лабораторные: на каждую подгруппу
    - Экзамен: exam_hours_per_student × студентов + exam_consult_hours_per_group на группу
    - Зачёт: credit_hours_per_student × студентов
    - Контроль УСР: usr_control_hours_per_group на группу, только если hours_usr > 0
    - КР: kr_hours_per_student × студентов × число контрольных
    """

    load_items = []
    groups = discipline.groups.all()

    # Лекции: один поток на все группы
    if discipline.hours_lec > 0:
        load_items.append({
            'kind': 'lec',
            'group': None,
            'subgroup_no': None,
            'hours': discipline.hours_lec,
        })

    # Практические: на каждую группу
    if discipline.hours_pract > 0:
        for group in groups:
            load_items.append({
                'kind': 'pract',
                'group': group,
                'subgroup_no': None,
                'hours': discipline.hours_pract,
            })

    # Лабораторные: на каждую подгруппу каждой группы
    if discipline.hours_lab > 0:
        for group in groups:
            subgroups_count = group.subgroups_count
            if subgroups_count is None:
                subgroups_count = settings.subgroups_per_group

            for subgroup_no in range(1, subgroups_count + 1):
                load_items.append({
                    'kind': 'lab',
                    'group': group,
                    'subgroup_no': subgroup_no,
                    'hours': discipline.hours_lab,
                })

    # Экзамен или Зачёт (в зависимости от control_form)
    if discipline.control_form == 'exam':
        for group in groups:
            exam_hours = (
                Decimal(str(group.students_count)) * settings.exam_hours_per_student
                + settings.exam_consult_hours_per_group
            )
            load_items.append({
                'kind': 'exam',
                'group': group,
                'subgroup_no': None,
                'hours': exam_hours,
            })

    elif discipline.control_form == 'credit':
        # Зачёт: часы на студента
        for group in groups:
            load_items.append({
                'kind': 'credit',
                'group': group,
                'subgroup_no': None,
                'hours': Decimal(str(group.students_count)) * settings.credit_hours_per_student,
            })

    # Контроль УСР: только если hours_usr > 0
    if discipline.hours_usr > 0:
        for group in groups:
            load_items.append({
                'kind': 'usr',
                'group': group,
                'subgroup_no': None,
                'hours': settings.usr_control_hours_per_group,
            })

    # Контрольные работы: на студента × число контрольных
    if discipline.control_works > 0:
        for group in groups:
            kr_hours = (Decimal(str(group.students_count)) *
                       settings.kr_hours_per_student *
                       Decimal(str(discipline.control_works)))
            load_items.append({
                'kind': 'kr',
                'group': group,
                'subgroup_no': None,
                'hours': kr_hours,
            })

    return load_items


@transaction.atomic
def recalculate_department(department, settings: CalcSettings):
    """
    Пересчитать нагрузку всех дисциплин кафедры.

    Логика:
    - Для каждой дисциплины кафедры вычислить новые элементы нагрузки
    - Элементы с тем же (discipline, kind, group, subgroup_no) обновить только hours
    - Назначенный преподаватель сохраняется
    - Новые элементы создаются, лишние удаляются (кроме тех, с записями в журнале)
    """
    from .models import JournalEntry

    summary = {
        'created': 0,
        'updated': 0,
        'deleted': 0,
        'deleted_with_teacher': 0,
    }
    disciplines = Discipline.objects.filter(department=department)
    desired_items = {}
    for discipline in disciplines:
        for spec in calc_department_load(discipline, settings):
            key = (
                discipline.pk,
                spec['kind'],
                spec['group'].pk if spec['group'] else None,
                spec['subgroup_no'],
            )
            desired_items[key] = (discipline, spec)

    existing_items = list(
        LoadItem.objects.filter(discipline__department=department)
        .select_related('discipline', 'group', 'teacher')
    )
    existing_by_key = {
        (item.discipline_id, item.kind, item.group_id, item.subgroup_no): item
        for item in existing_items
    }

    items_to_create = []
    items_to_update = []
    for key, (discipline, spec) in desired_items.items():
        item = existing_by_key.get(key)
        if item is None:
            items_to_create.append(LoadItem(
                discipline=discipline,
                kind=spec['kind'],
                group=spec['group'],
                subgroup_no=spec['subgroup_no'],
                hours=spec['hours'],
            ))
            continue
        if item.hours != spec['hours']:
            item.hours = spec['hours']
            items_to_update.append(item)

    if items_to_create:
        LoadItem.objects.bulk_create(items_to_create)
        summary['created'] = len(items_to_create)
    if items_to_update:
        LoadItem.objects.bulk_update(items_to_update, ['hours'])
        summary['updated'] = len(items_to_update)

    surplus_items = [item for key, item in existing_by_key.items() if key not in desired_items]
    surplus_ids = [item.pk for item in surplus_items]
    journal_item_ids = set(
        JournalEntry.objects.filter(load_item_id__in=surplus_ids)
        .values_list('load_item_id', flat=True)
    ) if surplus_ids else set()
    items_to_delete = [item for item in surplus_items if item.pk not in journal_item_ids]
    if items_to_delete:
        delete_ids = [item.pk for item in items_to_delete]
        LoadItem.objects.filter(pk__in=delete_ids).delete()
        summary['deleted'] = len(delete_ids)
        summary['deleted_with_teacher'] = sum(item.teacher_id is not None for item in items_to_delete)

    return summary


# ============ ПРОВЕРКА НАГРУЗКИ ПРЕПОДАВАТЕЛЯ ============

def teacher_total(teacher: Teacher) -> Decimal:
    """Сумма всех назначенных часов преподавателя."""
    from django.db.models import Sum
    total = LoadItem.objects.filter(teacher=teacher).aggregate(total=Sum('hours'))['total']
    return total or Decimal('0')


def check_teacher(teacher: Teacher) -> List[str]:
    """
    Проверить нагрузку преподавателя и вернуть список предупреждений.

    Проверяет:
    - Меньше эффективного минимума
    - Больше максимума
    - Лекций меньше, чем требуется (если может читать)
    - Назначены лекции, но position.can_lecture=False
    - Дисциплина вне специализации
    """
    load_items = LoadItem.objects.filter(teacher=teacher)
    totals = load_items.aggregate(
        total=Sum('hours'),
        lecture_hours=Sum('hours', filter=Q(kind='lec')),
        lecture_count=Count('pk', filter=Q(kind='lec')),
    )
    total = totals['total'] or Decimal('0')
    assigned_disciplines = list(
        Discipline.objects.filter(load_items__teacher=teacher).distinct()
    )
    specialized_discipline_ids = set(teacher.specialization.values_list('pk', flat=True))
    return _teacher_warnings(
        teacher,
        total,
        totals['lecture_hours'] or Decimal('0'),
        totals['lecture_count'] > 0,
        [discipline for discipline in assigned_disciplines if discipline.pk not in specialized_discipline_ids],
    )


def _teacher_warnings(teacher, total, lecture_hours, has_lectures, outside_specialization):
    warnings = []

    # Проверка минимума
    if total < Decimal(str(teacher.effective_min_hours)):
        warnings.append(
            f'Меньше минимума: {total} ч из {teacher.effective_min_hours} ч'
        )

    # Проверка максимума
    if total > Decimal(str(teacher.effective_max_hours)):
        warnings.append(
            f'Больше максимума: {total} ч из {teacher.effective_max_hours} ч'
        )

    # Проверка лекций
    if teacher.position and teacher.position.can_lecture:
        min_lecture = Decimal(str(teacher.position.min_lecture_hours * teacher.rate))
        if lecture_hours < min_lecture:
            warnings.append(
                f'Лекций меньше нормы: {lecture_hours} ч из {min_lecture} ч'
            )

    # Проверка прав на лекции
    if not teacher.position or not teacher.position.can_lecture:
        if has_lectures:
            warnings.append(
                f'Назначены лекции, но по должности не может читать'
            )

    for discipline in outside_specialization:
        warnings.append(f'Дисциплина "{discipline.name}" вне специализации')

    return warnings


def check_teachers(teachers) -> Dict[int, List[str]]:
    """Проверить несколько преподавателей общими запросами для панелей кафедры."""
    teachers = list(teachers)
    teacher_ids = [teacher.pk for teacher in teachers]
    if not teacher_ids:
        return {}
    totals_by_teacher = {
        row['teacher_id']: row
        for row in LoadItem.objects.filter(teacher_id__in=teacher_ids)
        .values('teacher_id')
        .annotate(
            total=Sum('hours'),
            lecture_hours=Sum('hours', filter=Q(kind='lec')),
            lecture_count=Count('pk', filter=Q(kind='lec')),
        )
    }
    assigned = {teacher_id: [] for teacher_id in teacher_ids}
    for teacher_id, discipline_id, discipline_name in LoadItem.objects.filter(
        teacher_id__in=teacher_ids
    ).values_list('teacher_id', 'discipline__id', 'discipline__name').distinct():
        assigned[teacher_id].append((discipline_id, discipline_name))
    result = {}
    specialization_by_teacher = {teacher.pk: set() for teacher in teachers}
    for teacher_id, discipline_id in Teacher.specialization.through.objects.filter(
        teacher_id__in=teacher_ids
    ).values_list('teacher_id', 'discipline_id'):
        specialization_by_teacher[teacher_id].add(discipline_id)
    for teacher in teachers:
        totals = totals_by_teacher.get(teacher.pk, {})
        specialized_ids = specialization_by_teacher[teacher.pk]
        warnings = _teacher_warnings(
            teacher,
            totals.get('total') or Decimal('0'),
            totals.get('lecture_hours') or Decimal('0'),
            totals.get('lecture_count', 0) > 0,
            [],
        )
        warnings.extend(
            f'Дисциплина "{name}" вне специализации'
            for discipline_id, name in assigned[teacher.pk]
            if discipline_id not in specialized_ids
        )
        result[teacher.pk] = warnings
    return result


# ============ ОТЧЁТЫ ============

def report_for_teacher(teacher: Teacher, year: int = None) -> Dict[str, any]:
    """
    Построить отчёт план/факт для преподавателя.

    Возвращает словарь:
    {
        'teacher': Teacher,
        'months': {
            'месяц': {
                'вид_работы': {
                    'plan': часы,
                    'fact': часы,
                    'delta': часы (факт - план),
                    'status': 'under' / 'ok' / 'over'
                }
            }
        }
    }
    """
    from django.db.models import Sum
    from datetime import datetime
    from .models import JournalEntry, Adjustment

    if year is None:
        year = datetime.now().year

    plan_totals = LoadItem.objects.filter(teacher=teacher).values(
        'kind', 'discipline__semester'
    ).annotate(total=Sum('hours'))
    plans = {}
    for row in plan_totals:
        semester = row['discipline__semester']
        semester_months = (9, 10, 11, 12, 1) if semester % 2 else range(2, 7)
        monthly_hours = (row['total'] or Decimal('0')) / Decimal('5')
        for month in semester_months:
            plans[(month, row['kind'])] = plans.get((month, row['kind']), Decimal('0')) + monthly_hours

    journal_totals = JournalEntry.objects.filter(
        teacher=teacher,
        date__year=year
    ).values('load_item__kind', 'date__month').annotate(total=Sum('hours'))
    journal_facts = {
        (row['date__month'], row['load_item__kind']): row['total'] or Decimal('0')
        for row in journal_totals
    }
    adjustment_totals = Adjustment.objects.filter(
        teacher=teacher,
        year=year
    ).values('kind', 'month').annotate(total=Sum('hours_delta'))
    adjustment_facts = {
        (row['month'], row['kind']): row['total'] or Decimal('0')
        for row in adjustment_totals
    }

    report = {'teacher': teacher, 'months': {month: {} for month in range(1, 13)}}
    all_month_kinds = set(plans) | set(journal_facts) | set(adjustment_facts)
    for month, kind in all_month_kinds:
        plan_hours = plans.get((month, kind), Decimal('0'))
        fact_hours = (
            journal_facts.get((month, kind), Decimal('0'))
            + adjustment_facts.get((month, kind), Decimal('0'))
        )
        delta = fact_hours - plan_hours
        if delta < -Decimal('1'):
            status = 'under'
        elif delta > Decimal('1'):
            status = 'over'
        else:
            status = 'ok'
        report['months'][month][kind] = {
            'plan': plan_hours,
            'fact': fact_hours,
            'delta': delta,
            'status': status,
        }

    return report


def report_for_department(department, year: int = None) -> Dict[str, any]:
    """
    Построить сводный отчёт по кафедре.

    Аналогично report_for_teacher, но по всем преподавателям кафедры.
    """
    teachers = Teacher.objects.filter(department=department).select_related('position').prefetch_related(
        'specialization'
    )

    return {
        'department': department,
        'teacher_reports': [report_for_teacher(t, year) for t in teachers]
    }
