from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from core.calculations import MONTH_NAMES, report_matrix, teacher_total, teacher_workload
from core.models import JournalEntry, LoadItem, Teacher


def department_load_table(dept):
    items = LoadItem.objects.filter(
        discipline__department=dept
    ).select_related('discipline', 'group', 'teacher').order_by('discipline', 'kind')
    rows = [
        (
            item.discipline.name,
            item.get_kind_display(),
            item.group.name if item.group else 'Поток',
            item.subgroup_no or '',
            item.hours,
            item.teacher.full_name if item.teacher else '',
        )
        for item in items
    ]
    return (
        'department_load.xlsx', 'Нагрузка кафедры',
        ['Дисциплина', 'Вид работы', 'Группа', 'Подгруппа', 'Часы', 'Преподаватель'], rows, (),
    )


def teachers_table(dept):
    teachers = Teacher.objects.filter(department=dept).select_related('position')
    rows = [
        (
            teacher.full_name,
            teacher.position.position if teacher.position else '',
            teacher.get_degree_display(),
            teacher.rank,
            teacher.rate,
            teacher.effective_min_hours,
            teacher.effective_max_hours,
            teacher_total(teacher),
        )
        for teacher in teachers
    ]
    return (
        'department_teachers.xlsx', 'Преподаватели',
        ['ФИО', 'Должность', 'Учёная степень', 'Звание', 'Ставка', 'Мин. часы', 'Макс. часы', 'Назначено часов'],
        rows, (),
    )


def workload_table(teacher):
    workload = teacher_workload(teacher)
    kind_labels = [label for _, label in workload['kinds']]
    cent = Decimal('0.01')
    headers = ['Дисциплина', 'Семестр', 'Группы'] + kind_labels + ['Всего']
    rows = [
        [row['discipline'].name if row['discipline'] else '', row['semester'], row['groups']]
        + [hours.quantize(cent) for hours in row['hours_list']]
        + [row['total'].quantize(cent)]
        for row in workload['rows']
    ]
    rows.append(
        ['Всего за осенний семестр', '', '']
        + [hours.quantize(cent) for hours in workload['fall_totals_list']]
        + [workload['fall_totals_total'].quantize(cent)]
    )
    rows.append(
        ['Всего за весенний семестр', '', '']
        + [hours.quantize(cent) for hours in workload['spring_totals_list']]
        + [workload['spring_totals_total'].quantize(cent)]
    )
    rows.append(
        ['ИТОГО ЗА ГОД', '', '']
        + [hours.quantize(cent) for hours in workload['grand_totals_list']]
        + [workload['grand_totals_total'].quantize(cent)]
    )
    return 'my_workload.xlsx', 'Моя нагрузка', headers, rows, ()


def journal_table(teacher):
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
            entry.topic,
        )
        for entry in entries
    ]
    return (
        'my_journal.xlsx', 'Журнал',
        ['Дата', 'Время', 'Дисциплина', 'Вид работы', 'Группа', 'Часы', 'Тема'], rows, (),
    )


def report_tables(teachers, year, is_single_teacher):
    matrix = report_matrix(teachers, year)
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
            item['hours'], 'Плановая' if item['planned'] else 'Внеплановая', item['reason'],
        ]
        for item in matrix['adjustments']
    ]
    filename = f'workload_report_{year}.xlsx' if is_single_teacher else f'department_report_{year}.xlsx'
    extra_sheets = [
        ('План', headers, matrix_rows('plan')),
        ('Отклонение', headers, matrix_rows('delta')),
        (
            'Корректировки',
            ['Месяц', 'Преподаватель', 'Дисциплина', 'Вид работы', 'Часы', 'Тип', 'Причина'],
            adjustment_rows,
        ),
    ]
    return filename, 'Факт', headers, matrix_rows('fact'), extra_sheets


def _fill_sheet(worksheet, headers, rows):
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


def build_xlsx_bytes(sheet_name, headers, rows, extra_sheets=()):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name[:31]
    _fill_sheet(worksheet, headers, rows)

    for extra_name, extra_headers, extra_rows in extra_sheets:
        _fill_sheet(workbook.create_sheet(extra_name[:31]), extra_headers, extra_rows)

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
