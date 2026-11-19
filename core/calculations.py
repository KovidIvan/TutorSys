"""
Модуль расчёта учебной нагрузки.
Все функции принимают параметры через аргументы (из CalcSettings или PositionRule).
Никаких захардкоженных норм.
"""

from decimal import Decimal
from typing import List, Dict
from django.db.models import Sum
from django.db.models.functions import ExtractMonth
from .models import (
    LoadItem, Teacher, CalcSettings, Discipline, JournalEntry, Adjustment
)

# Параметры отчёта план/факт.
MONTH_NAMES = (
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'
)
# Семестр, в который попадает месяц (июль и август — каникулы, плана нет).
MONTH_SEASONS = {
    1: 'fall', 2: 'spring', 3: 'spring', 4: 'spring', 5: 'spring',
    6: 'spring', 9: 'fall', 10: 'fall', 11: 'fall', 12: 'fall',
}
# План семестра равномерно делится на столько месяцев.
PLAN_MONTHS_PER_SEASON = Decimal('5')
# Допуск в часах: отклонение в пределах допуска считается выполнением.
REPORT_TOLERANCE = Decimal('1')
# Порядок столбцов отчёта, как в образце из задания.
REPORT_KIND_ORDER = ('lec', 'pract', 'lab', 'usr', 'kr', 'consult', 'exam', 'credit')


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


def auto_assign_department_load(department) -> int:
    """
    Автоматически подобрать и назначить преподавателей кафедры на элементы нагрузки (LoadItem)
    с учётом их специализации, должности (права вести лекции и минимума лекционных часов),
    индивидуальной нормы часов (effective_min_hours / effective_max_hours) и ставки.

    Правила:
    1. Если у преподавателя задана непустая специализация, но текущая дисциплина элемента нагрузки
       в неё больше не входит, либо преподавателю назначена лекция при can_lecture=False,
       такое некорректное назначение снимается для перераспределения.
    2. Сначала распределяются нераспределённые ЛЕКЦИИ ('lec') между преподавателями,
       у которых дисциплина входит в специализацию и должность разрешает лекции (can_lecture=True),
       с приоритетом тем, у кого ещё не выполнен минимум лекционных часов (min_lecture_hours * rate)
       и кто не превышает свой максимум часов (effective_max_hours).
    3. Затем распределяются остальные виды работ ('pract', 'lab', 'exam', 'credit', 'usr', 'kr')
       между профильными преподавателями (в рамках их специализации), у которых ещё не набран
       минимум (effective_min_hours) и не превышен максимум (effective_max_hours), выбирая
       наименее загруженного относительно его нормы.

    Возвращает количество автоматически назначенных элементов нагрузки.
    """
    teachers = list(
        Teacher.objects.filter(department=department)
        .select_related('position')
        .prefetch_related('specialization')
    )
    if not teachers:
        return 0

    spec_map = {
        t.id: {d.id for d in t.specialization.all()}
        for t in teachers
    }

    items = list(
        LoadItem.objects.filter(discipline__department=department)
        .select_related('discipline', 'teacher')
        .order_by('discipline__semester', 'discipline__name', 'kind', 'id')
    )

    # Шаг 1. Снять назначения, которые противоречат обновлённой специализации или запрету на лекции
    for item in items:
        if item.teacher_id is None:
            continue
        t_specs = spec_map.get(item.teacher_id)
        t_obj = next((t for t in teachers if t.id == item.teacher_id), None)
        if t_obj is None:
            continue
        invalid_lecture = (
            item.kind == 'lec'
            and (not t_obj.position or not t_obj.position.can_lecture)
        )
        # Если у преподавателя явно выбраны дисциплины специализации и данная дисциплина туда не входит
        invalid_spec = bool(t_specs) and (item.discipline_id not in t_specs)
        if invalid_lecture or invalid_spec:
            item.teacher = None
            item.save(update_fields=['teacher'])

    # Подсчёт текущих часов и лекционных часов каждого преподавателя
    total_hours = {t.id: Decimal('0') for t in teachers}
    lecture_hours = {t.id: Decimal('0') for t in teachers}
    for item in items:
        if item.teacher_id in total_hours:
            total_hours[item.teacher_id] += item.hours
            if item.kind == 'lec':
                lecture_hours[item.teacher_id] += item.hours

    def can_take(t: Teacher, item: LoadItem, allow_over_max: bool = False) -> bool:
        # Проверка специализации: назначаем только тем, у кого дисциплина входит в специализацию
        if item.discipline_id not in spec_map.get(t.id, set()):
            return False
        # Проверка права на чтение лекций
        if item.kind == 'lec' and (not t.position or not t.position.can_lecture):
            return False
        # Проверка лимита максимальных часов
        if not allow_over_max:
            max_h = Decimal(str(t.effective_max_hours))
            if total_hours[t.id] + item.hours > max_h:
                return False
        return True

    def min_lec_target(t: Teacher) -> Decimal:
        if t.position and t.position.can_lecture:
            return Decimal(str(t.position.min_lecture_hours)) * t.rate
        return Decimal('0')

    assigned_count = 0

    # Шаг 2. Распределяем нераспределённые ЛЕКЦИИ ('lec')
    unassigned_lectures = [i for i in items if i.teacher_id is None and i.kind == 'lec']
    for item in unassigned_lectures:
        candidates = [t for t in teachers if can_take(t, item, allow_over_max=False)]
        if not candidates:
            candidates = [t for t in teachers if can_take(t, item, allow_over_max=True)]
        if not candidates:
            continue

        # Приоритет:
        # 1) у кого ещё не выполнен норматив лекций (lecture_hours < min_lec_target)
        # 2) наибольший дефицит лекционных часов
        # 3) наименьшая общая загрузка относительно effective_min_hours
        def lecture_sort_key(t: Teacher):
            lec_deficit = max(Decimal('0'), min_lec_target(t) - lecture_hours[t.id])
            min_h = max(Decimal('1'), Decimal(str(t.effective_min_hours)))
            load_ratio = total_hours[t.id] / min_h
            return (
                0 if lec_deficit > 0 else 1,
                -lec_deficit,
                load_ratio,
                t.id,
            )

        chosen = min(candidates, key=lecture_sort_key)
        item.teacher = chosen
        item.save(update_fields=['teacher'])
        total_hours[chosen.id] += item.hours
        lecture_hours[chosen.id] += item.hours
        assigned_count += 1

    # Шаг 3. Распределяем остальные нераспределённые элементы нагрузки
    unassigned_other = [i for i in items if i.teacher_id is None and i.kind != 'lec']
    for item in unassigned_other:
        candidates = [t for t in teachers if can_take(t, item, allow_over_max=False)]
        if not candidates:
            candidates = [t for t in teachers if can_take(t, item, allow_over_max=True)]
        if not candidates:
            continue

        # Приоритет:
        # 1) кто ещё не набрал свой минимум часов (total_hours < effective_min_hours)
        # 2) наименьшая доля заполнения нормы (total_hours / effective_min_hours)
        # 3) наибольший запас до effective_max_hours
        def general_sort_key(t: Teacher):
            min_h = max(Decimal('1'), Decimal(str(t.effective_min_hours)))
            max_h = Decimal(str(t.effective_max_hours))
            below_min = 0 if total_hours[t.id] < min_h else 1
            load_ratio = total_hours[t.id] / min_h
            headroom = max_h - (total_hours[t.id] + item.hours)
            return (
                below_min,
                load_ratio,
                -headroom,
                t.id,
            )

        chosen = min(candidates, key=general_sort_key)
        item.teacher = chosen
        item.save(update_fields=['teacher'])
        total_hours[chosen.id] += item.hours
        assigned_count += 1

    return assigned_count


def recalculate_department(department, settings: CalcSettings, auto_assign: bool = True):
    """
    Пересчитать нагрузку всех дисциплин кафедры и автоматически подобрать преподавателей
    по их специализации, нормам лекций и лимитам часов по должности.

    Логика:
    - Для каждой дисциплины кафедры вычислить новые элементы нагрузки
    - Элементы с тем же (discipline, kind, group, subgroup_no) обновить только hours
    - Назначенный преподаватель сохраняется (если не противоречит специализации и правам на лекции)
    - Новые элементы создаются, лишние удаляются (кроме тех, с записями в журнале)
    - При auto_assign=True запускается автоподбор преподавателей по специализации и нормативам
    """
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

    if auto_assign:
        auto_assign_department_load(department)


# ============ ПРОВЕРКА НАГРУЗКИ ПРЕПОДАВАТЕЛЯ ============

def _format_hours(hours: Decimal) -> str:
    return format(hours.normalize(), 'f')


def teacher_total(teacher: Teacher) -> Decimal:
    """Сумма назначенных часов, округлённая до точности хранения часов."""
    total = LoadItem.objects.filter(teacher=teacher).aggregate(total=Sum('hours'))['total']
    return (total or Decimal('0')).quantize(Decimal('0.01'))


# Порядок видов работ для страницы нагрузки преподавателя (как в образце из задания).
WORKLOAD_KIND_ORDER = ('lec', 'pract', 'lab', 'usr', 'kr', 'consult', 'exam', 'credit')


def teacher_workload(teacher: Teacher) -> Dict:
    """
    Построить матрицу нагрузки преподавателя для страницы /workload/.

    Возвращает словарь:
    {
        'kinds': [(код, название), ...],          # столбцы таблицы
        'rows': [                                  # строки по дисциплинам
            {
                'discipline': Discipline,
                'semester': int,
                'season': 'fall' | 'spring',
                'hours': {код: Decimal, ...},      # часов по виду
                'total': Decimal,                  # Всего по дисциплине
                'groups': str,                     # группы/подгруппы (отображение)
            },
            ...
        ],
        'fall_totals': {код: Decimal, 'total': Decimal},
        'spring_totals': {код: Decimal, 'total': Decimal},
        'grand_totals': {код: Decimal, 'total': Decimal},
        'has_data': bool,
    }
    Строки отсортированы: осенние семестры (нечётные) → весенние (чётные),
    внутри сезона — по имени дисциплины.
    """
    from .models import LoadItem as _LoadItem

    labels = dict(_LoadItem.KIND_CHOICES)
    # Строим список кодов в нужном порядке — только те, что есть в KIND_CHOICES
    kind_codes = [k for k in WORKLOAD_KIND_ORDER if k in labels]
    # Добавляем виды, которых нет в WORKLOAD_KIND_ORDER (на будущее)
    kind_codes += [k for k in labels if k not in kind_codes]

    zero = Decimal('0')

    items = _LoadItem.objects.filter(teacher=teacher).select_related('discipline', 'group').order_by(
        'discipline__semester', 'discipline__name', 'kind'
    )

    # Агрегируем: (discipline, kind) -> sum(hours)
    from django.db.models import Sum as _Sum
    agg = (
        _LoadItem.objects.filter(teacher=teacher)
        .values('discipline', 'discipline__name', 'discipline__semester', 'kind')
        .annotate(total=_Sum('hours'))
        .order_by('discipline__semester', 'discipline__name', 'kind')
    )

    # Собираем по дисциплинам
    disc_data: Dict = {}
    cent = Decimal('0.01')
    for row in agg:
        disc_id = row['discipline']
        if disc_id not in disc_data:
            disc_data[disc_id] = {
                'discipline_id': disc_id,
                'name': row['discipline__name'],
                'semester': row['discipline__semester'],
                'hours': {k: zero for k in kind_codes},
                'total': zero,
            }
        kind = row['kind']
        if kind in kind_codes:
            val = Decimal(str(row['total'])).quantize(cent)
            disc_data[disc_id]['hours'][kind] = (disc_data[disc_id]['hours'].get(kind, zero) + val).quantize(cent)
            disc_data[disc_id]['total'] = (disc_data[disc_id]['total'] + val).quantize(cent)

    # Получаем объекты дисциплин для отображения
    from .models import Discipline as _Discipline
    discipline_objs = {d.pk: d for d in _Discipline.objects.filter(
        pk__in=list(disc_data.keys())
    ).select_related('curriculum__specialty')}

    # Собираем строки таблицы
    rows = []
    for disc_id, data in sorted(disc_data.items(), key=lambda x: (x[1]['semester'], x[1]['name'])):
        semester = data['semester']
        season = 'fall' if semester % 2 else 'spring'
        disc_obj = discipline_objs.get(disc_id)

        # Формируем строку отображения группы/подгрупп
        groups_set = set()
        for item in items.filter(discipline_id=disc_id):
            if item.group:
                label = item.group.name
                if item.subgroup_no:
                    label += f'-{item.subgroup_no}'
                groups_set.add(label)
        groups_str = ', '.join(sorted(groups_set)) if groups_set else 'Поток'

        rows.append({
            'discipline': disc_obj,
            'semester': semester,
            'season': season,
            'hours': data['hours'],
            'total': data['total'],
            'groups': groups_str,
        })

    # Итоги по сезонам
    fall_totals = {k: zero for k in kind_codes}
    fall_totals['total'] = zero
    spring_totals = {k: zero for k in kind_codes}
    spring_totals['total'] = zero
    grand_totals = {k: zero for k in kind_codes}
    grand_totals['total'] = zero

    for row in rows:
        totals = fall_totals if row['season'] == 'fall' else spring_totals
        for k in kind_codes:
            h = row['hours'].get(k, zero).quantize(cent)
            totals[k] = (totals[k] + h).quantize(cent)
            grand_totals[k] = (grand_totals[k] + h).quantize(cent)
        totals['total'] = (totals['total'] + row['total']).quantize(cent)
        grand_totals['total'] = (grand_totals['total'] + row['total']).quantize(cent)

    kinds = [(k, labels[k]) for k in kind_codes]

    # Преобразуем rows в список (с часами как упорядоченным списком, чтобы шаблон не нуждался в фильтрах)
    rows_list = []
    for row in rows:
        rows_list.append({
            'discipline': row['discipline'],
            'semester': row['semester'],
            'season': row['season'],
            'groups': row['groups'],
            'total': row['total'],
            'hours_list': [row['hours'].get(k, zero) for k in kind_codes],
        })

    def totals_list(t):
        return [t.get(k, zero) for k in kind_codes]

    return {
        'kinds': kinds,
        'rows': rows_list,
        'fall_totals_list': totals_list(fall_totals),
        'fall_totals_total': fall_totals['total'],
        'spring_totals_list': totals_list(spring_totals),
        'spring_totals_total': spring_totals['total'],
        'grand_totals_list': totals_list(grand_totals),
        'grand_totals_total': grand_totals['total'],
        # Также возвращаем raw-словари для Excel-экспорта
        '_kind_codes': kind_codes,
        '_fall_totals': fall_totals,
        '_spring_totals': spring_totals,
        '_grand_totals': grand_totals,
        'has_data': bool(rows_list),
    }


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
            f'Меньше минимума: {_format_hours(total)} ч из {teacher.effective_min_hours} ч'
        )

    # Проверка максимума
    if total > Decimal(str(teacher.effective_max_hours)):
        warnings.append(
            f'Больше максимума: {_format_hours(total)} ч из {teacher.effective_max_hours} ч'
        )

    # Проверка лекций
    if teacher.position and teacher.position.can_lecture:
        lecture_hours = LoadItem.objects.filter(
            teacher=teacher, kind='lec'
        ).aggregate(total=Sum('hours'))['total'] or Decimal('0')
        lecture_hours = lecture_hours.quantize(Decimal('0.01'))

        min_lecture = Decimal(str(teacher.position.min_lecture_hours * teacher.rate))
        if lecture_hours < min_lecture:
            warnings.append(
                f'Лекций меньше нормы: {_format_hours(lecture_hours)} ч из {_format_hours(min_lecture)} ч'
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
    report = {'teacher': teacher, 'months': {}}

    for month in range(1, 13):
        report['months'][month] = {}
        season = MONTH_SEASONS.get(month)
        for kind in kinds:
            plan_hours = (
                planned_by_kind_and_season.get((kind, season), Decimal('0')) / PLAN_MONTHS_PER_SEASON
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

            status = _delta_status(delta)

            report['months'][month][kind] = {
                'plan': plan_hours,
                'fact': fact_hours,
                'delta': delta,
                'status': status
            }

    return report


def _delta_status(delta: Decimal) -> str:
    """Статус отклонения факта от плана: 'under' / 'ok' / 'over'."""
    if delta < -REPORT_TOLERANCE:
        return 'under'
    if delta > REPORT_TOLERANCE:
        return 'over'
    return 'ok'


def report_kinds() -> List[tuple]:
    """Виды работ в порядке столбцов отчёта: [(код, название), ...]."""
    labels = dict(LoadItem.KIND_CHOICES)
    ordered = [kind for kind in REPORT_KIND_ORDER if kind in labels]
    ordered += [kind for kind in labels if kind not in REPORT_KIND_ORDER]
    return [(kind, labels[kind]) for kind in ordered]


def _report_cell(
    plan: Decimal, fact: Decimal, month: int, kind: str, colored: bool, title: str
) -> Dict:
    """Ячейка матрицы. Цвет (css) задаётся только для итоговых ячеек; title — для окна расшифровки."""
    delta = fact - plan
    status = _delta_status(delta)
    has_data = bool(plan or fact)
    css = ''
    if colored and has_data:
        css = 'table-danger' if status == 'under' else 'table-success'
    return {
        'month': month,
        'kind': kind,
        'plan': plan,
        'fact': fact,
        'delta': delta,
        'status': status,
        'css': css,
        'title': title,
        'has_data': has_data,
    }


def build_report_matrix(plan_rows, fact_rows, adjustment_rows, kinds) -> Dict:
    """
    Собрать матрицу отчёта «месяц × виды работ» из готовых строк (без обращения к БД).

    plan_rows: словари teacher_id, teacher, discipline_id, discipline, kind, semester, hours
    fact_rows: словари teacher_id, teacher, discipline_id, discipline, kind, month, hours
    adjustment_rows: как fact_rows, плюс planned (bool) и reason (str)
    kinds: [(код, название), ...] — столбцы отчёта

    Возвращает словарь:
    {
        'kinds': kinds,
        'rows': [{'month', 'name', 'cells': [ячейка по видам], 'total': ячейка «Всего»} × 12],
        'totals': {'cells': [...], 'total': ячейка «Итого за год»},
        'details': [строки для окна с расшифровкой по клику],
        'adjustments': [корректировки по месяцам],
        'has_data': bool,
    }
    Ячейка: month, kind, plan, fact, delta, status, css, title, has_data
    (month = 0 для итогов за год, kind = 'all' для итогов по всем видам).
    Итоговые ячейки («Всего» по месяцу, итоги по виду и за год) закрашиваются:
    css = 'table-danger' при недовыполнении, 'table-success' при перевыполнении.
    """
    zero = Decimal('0')
    labels = dict(kinds)
    kind_codes = [code for code, _ in kinds]
    items = {}  # (преподаватель, дисциплина, вид, месяц) -> план, факт и причины

    def item_for(row, month):
        key = (row['teacher_id'], row['discipline_id'], row['kind'], month)
        item = items.get(key)
        if item is None:
            item = items[key] = {
                'm': month, 'k': row['kind'], 'kn': labels[row['kind']],
                't': row['teacher_id'], 'tn': row['teacher'],
                'd': row['discipline_id'], 'dn': row['discipline'],
                'p': zero, 'f': zero, 'r': [],
            }
        return item

    for row in plan_rows:
        if row['kind'] not in labels:
            continue
        season = 'fall' if row['semester'] % 2 else 'spring'
        part = row['hours'] / PLAN_MONTHS_PER_SEASON
        for month, month_season in MONTH_SEASONS.items():
            if month_season == season:
                item_for(row, month)['p'] += part

    for row in fact_rows:
        if row['kind'] in labels:
            item_for(row, row['month'])['f'] += row['hours']

    adjustments = []
    for row in adjustment_rows:
        if row['kind'] not in labels:
            continue
        item = item_for(row, row['month'])
        item['f'] += row['hours']
        item['r'].append({'h': row['hours'], 'text': row['reason'], 'planned': row['planned']})
        adjustments.append({
            'month': row['month'],
            'teacher': row['teacher'],
            'discipline': row['discipline'],
            'kind': labels[row['kind']],
            'hours': row['hours'],
            'planned': row['planned'],
            'reason': row['reason'],
        })
    adjustments.sort(key=lambda a: (a['month'], a['teacher'], a['discipline'], a['kind']))

    plan_by_cell, fact_by_cell = {}, {}
    for item in items.values():
        key = (item['m'], item['k'])
        plan_by_cell[key] = plan_by_cell.get(key, zero) + item['p']
        fact_by_cell[key] = fact_by_cell.get(key, zero) + item['f']

    rows = []
    year_plan = {code: zero for code in kind_codes}
    year_fact = {code: zero for code in kind_codes}
    grand_plan = grand_fact = zero
    for month in range(1, 13):
        cells = []
        month_plan = month_fact = zero
        for code in kind_codes:
            plan = plan_by_cell.get((month, code), zero)
            fact = fact_by_cell.get((month, code), zero)
            cells.append(_report_cell(
                plan, fact, month, code, colored=False,
                title=f'{MONTH_NAMES[month - 1]} · {labels[code]}'
            ))
            month_plan += plan
            month_fact += fact
            year_plan[code] += plan
            year_fact[code] += fact
        rows.append({
            'month': month,
            'name': MONTH_NAMES[month - 1],
            'cells': cells,
            'total': _report_cell(
                month_plan, month_fact, month, 'all', colored=True,
                title=f'{MONTH_NAMES[month - 1]} · всего'
            ),
        })
        grand_plan += month_plan
        grand_fact += month_fact

    totals = {
        'cells': [
            _report_cell(
                year_plan[code], year_fact[code], 0, code, colored=True,
                title=f'Год · {labels[code]}'
            )
            for code in kind_codes
        ],
        'total': _report_cell(grand_plan, grand_fact, 0, 'all', colored=True, title='Итого за год'),
    }

    details = []
    for item in sorted(items.values(), key=lambda x: (x['m'], x['k'], x['tn'], x['dn'])):
        if not (item['p'] or item['f'] or item['r']):
            continue
        details.append({
            **item,
            'p': str(item['p']),
            'f': str(item['f']),
            'r': [
                {'h': str(reason['h']), 'text': reason['text'], 'planned': reason['planned']}
                for reason in item['r']
            ],
        })

    return {
        'kinds': kinds,
        'rows': rows,
        'totals': totals,
        'details': details,
        'adjustments': adjustments,
        'has_data': bool(grand_plan or grand_fact or adjustments),
    }


def report_matrix(teachers, year: int) -> Dict:
    """
    Матрица отчёта план/факт за год для одного преподавателя или для группы
    преподавателей (например, всей кафедры). Формат результата — build_report_matrix.
    """
    teacher_list = list(teachers)
    ids = [teacher.pk for teacher in teacher_list]
    names = {teacher.pk: teacher.full_name for teacher in teacher_list}

    plan_rows = [
        {
            'teacher_id': row['teacher'], 'teacher': names[row['teacher']],
            'discipline_id': row['discipline'], 'discipline': row['discipline__name'],
            'kind': row['kind'], 'semester': row['discipline__semester'],
            'hours': row['total'],
        }
        for row in LoadItem.objects.filter(teacher__in=ids)
        .values('teacher', 'discipline', 'discipline__name', 'discipline__semester', 'kind')
        .annotate(total=Sum('hours'))
        .order_by()
    ]

    fact_rows = [
        {
            'teacher_id': row['teacher'], 'teacher': names[row['teacher']],
            'discipline_id': row['load_item__discipline'],
            'discipline': row['load_item__discipline__name'],
            'kind': row['load_item__kind'], 'month': row['month'],
            'hours': row['total'],
        }
        for row in JournalEntry.objects.filter(teacher__in=ids, date__year=year)
        .annotate(month=ExtractMonth('date'))
        .values(
            'teacher', 'load_item__discipline', 'load_item__discipline__name',
            'load_item__kind', 'month'
        )
        .annotate(total=Sum('hours'))
        .order_by()
    ]

    adjustment_rows = [
        {
            'teacher_id': adjustment.teacher_id, 'teacher': names[adjustment.teacher_id],
            'discipline_id': adjustment.discipline_id,
            'discipline': adjustment.discipline.name,
            'kind': adjustment.kind, 'month': adjustment.month,
            'hours': adjustment.hours_delta, 'planned': adjustment.planned,
            'reason': adjustment.reason,
        }
        for adjustment in Adjustment.objects.filter(teacher__in=ids, year=year)
        .select_related('discipline')
    ]

    return build_report_matrix(plan_rows, fact_rows, adjustment_rows, report_kinds())
