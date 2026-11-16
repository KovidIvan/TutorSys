"""
Модуль расчёта учебной нагрузки.
Все функции принимают параметры через аргументы (из CalcSettings или PositionRule).
Никаких захардкоженных норм.
"""

from decimal import Decimal
from typing import List, Dict
from django.db.models import Sum
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
        # Экзамен и консультация — одна назначаемая единица нагрузки на группу.
        for group in groups:
            load_items.append({
                'kind': 'exam',
                'group': group,
                'subgroup_no': None,
                'hours': (
                    Decimal(str(group.students_count)) * settings.exam_hours_per_student
                    + settings.exam_consult_hours_per_group
                ),
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

    disciplines = Discipline.objects.filter(department=department)

    for discipline in disciplines:
        # Рассчитать эталонные элементы
        load_specs = calc_department_load(discipline, settings)

        # Для каждого эталонного элемента
        for spec in load_specs:
            # Построить уникальный ключ (без часов)
            key = (discipline.id, spec['kind'], 
                   spec['group'].id if spec['group'] else None,
                   spec['subgroup_no'])

            # Найти существующий LoadItem
            existing = LoadItem.objects.filter(
                discipline=discipline,
                kind=spec['kind'],
                group=spec['group'],
                subgroup_no=spec['subgroup_no']
            ).first()

            if existing:
                # Обновить только часы, преподаватель остаётся
                existing.hours = spec['hours']
                existing.save()
            else:
                # Создать новый
                LoadItem.objects.create(
                    discipline=discipline,
                    kind=spec['kind'],
                    group=spec['group'],
                    subgroup_no=spec['subgroup_no'],
                    hours=spec['hours'],
                    teacher=None
                )

        # Удалить лишние элементы (кроме тех, на которые есть записи журнала)
        existing_items = LoadItem.objects.filter(discipline=discipline)
        for item in existing_items:
            key = (item.discipline.id, item.kind,
                   item.group.id if item.group else None,
                   item.subgroup_no)

            # Проверить, есть ли такой в рассчитанных
            found = False
            for spec in load_specs:
                spec_key = (discipline.id, spec['kind'],
                           spec['group'].id if spec['group'] else None,
                           spec['subgroup_no'])
                if key == spec_key:
                    found = True
                    break

            if not found:
                # Проверить, есть ли записи журнала
                if not JournalEntry.objects.filter(load_item=item).exists():
                    item.delete()


# ============ ПРОВЕРКА НАГРУЗКИ ПРЕПОДАВАТЕЛЯ ============

def teacher_total(teacher: Teacher) -> Decimal:
    """Сумма всех назначенных часов преподавателя."""
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
    warnings = []
    total = teacher_total(teacher)

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
        lecture_hours = LoadItem.objects.filter(
            teacher=teacher, kind='lec'
        ).aggregate(total=Sum('hours'))['total'] or Decimal('0')

        min_lecture = Decimal(str(teacher.position.min_lecture_hours * teacher.rate))
        if lecture_hours < min_lecture:
            warnings.append(
                f'Лекций меньше нормы: {lecture_hours} ч из {min_lecture} ч'
            )

    # Проверка прав на лекции
    if not teacher.position or not teacher.position.can_lecture:
        lecture_count = LoadItem.objects.filter(
            teacher=teacher, kind='lec'
        ).count()
        if lecture_count > 0:
            warnings.append(
                f'Назначены лекции, но по должности не может читать'
            )

    # Проверка специализации
    assigned_disciplines = (
        LoadItem.objects.filter(teacher=teacher)
        .values_list('discipline', flat=True)
        .distinct()
    )
    for disc_id in assigned_disciplines:
        discipline = Discipline.objects.get(pk=disc_id)
        if not teacher.specialization.filter(pk=disc_id).exists():
            warnings.append(
                f'Дисциплина "{discipline.name}" вне специализации'
            )

    return warnings


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
    from datetime import datetime
    from .models import JournalEntry, Adjustment

    if year is None:
        year = datetime.now().year

    load_items = LoadItem.objects.filter(teacher=teacher).select_related('discipline')
    planned_by_kind_and_season = {}
    planned_items = load_items.values(
        'kind', 'discipline__semester'
    ).annotate(total=Sum('hours'))
    for item in planned_items:
        season = 'fall' if item['discipline__semester'] % 2 else 'spring'
        key = (item['kind'], season)
        planned_by_kind_and_season[key] = (
            planned_by_kind_and_season.get(key, Decimal('0')) + item['total']
        )

    kinds = {kind for kind, _ in planned_by_kind_and_season}
    month_seasons = {
        1: 'fall', 2: 'spring', 3: 'spring', 4: 'spring', 5: 'spring',
        6: 'spring', 9: 'fall', 10: 'fall', 11: 'fall', 12: 'fall',
    }
    report = {'teacher': teacher, 'months': {}}

    for month in range(1, 13):
        report['months'][month] = {}
        season = month_seasons.get(month)
        for kind in kinds:
            plan_hours = (
                planned_by_kind_and_season.get((kind, season), Decimal('0')) / Decimal('5')
                if season else Decimal('0')
            )

            # Факт: записи в журнале за месяц + Adjustment
            journal_hours = JournalEntry.objects.filter(
                teacher=teacher,
                load_item__kind=kind,
                date__month=month,
                date__year=year
            ).aggregate(total=Sum('hours'))['total'] or Decimal('0')

            adjustment_hours = Adjustment.objects.filter(
                teacher=teacher,
                kind=kind,
                month=month,
                year=year
            ).aggregate(total=Sum('hours_delta'))['total'] or Decimal('0')

            fact_hours = journal_hours + adjustment_hours
            delta = fact_hours - plan_hours

            # Статус
            if delta < -Decimal('1'):  # допуск 1 час
                status = 'under'
            elif delta > Decimal('1'):
                status = 'over'
            else:
                status = 'ok'

            report['months'][month][kind] = {
                'plan': plan_hours,
                'fact': fact_hours,
                'delta': delta,
                'status': status
            }

    return report
