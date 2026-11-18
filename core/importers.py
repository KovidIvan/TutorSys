"""Импорт учебных планов и групп из файла кафедральной нагрузки."""

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from zipfile import BadZipFile

from django.db import transaction
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .calculations import recalculate_department
from .models import (
    CalcSettings,
    Curriculum,
    Department,
    Discipline,
    Group,
    Specialty,
)

MAX_WORKBOOK_SIZE = 2 * 1024 * 1024
REQUIRED_HEADERS = {
    'discipline': 'дисциплины (виды работ)',
    'specialty': 'спец-ть',
    'course': 'курс',
    'semester': 'семестр',
    'study_form': 'форма обучения',
    'students': 'студентов',
    'groups': 'групп',
    'subgroups': 'подгрупп',
    'lectures': 'лекции (по плану)',
    'practices': 'практические (по плану)',
    'labs': 'лабораторные (по плану)',
    'control_works': 'кол-во кр',
    'usr': 'уср',
    'exams': 'экзамен',
    'credits': 'зачёт',
}
STUDY_FORMS = {
    'д': 'full_time',
    'd': 'full_time',
    'з': 'correspondence',
    'z': 'correspondence',
    'о': 'part_time',
    'o': 'part_time',
}
STUDY_FORM_CODES = {
    'full_time': 'д',
    'correspondence': 'з',
    'part_time': 'о',
}


class WorkloadImportError(Exception):
    """Ошибка проверки или загрузки книги, которую можно показать пользователю."""


@dataclass(frozen=True)
class WorkloadRow:
    row_number: int
    name: str
    specialties: tuple
    course: int
    semester: int
    study_form: str
    students: int
    groups: int
    subgroups: int | None
    hours_lec: Decimal
    hours_pract: Decimal
    hours_lab: Decimal
    hours_usr: Decimal
    control_works: int
    control_form: str


def _normalize_header(value):
    return re.sub(r'\s+', ' ', str(value or '').strip()).casefold()


def _integer(value, field, row_number, *, required=False, minimum=0, maximum=100000):
    if value in (None, ''):
        if required:
            raise WorkloadImportError(f'Строка {row_number}: не заполнено поле «{field}».')
        return None
    try:
        number = Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, ValueError):
        raise WorkloadImportError(f'Строка {row_number}: поле «{field}» должно быть целым числом.')
    if not number.is_finite() or number != number.to_integral_value():
        raise WorkloadImportError(f'Строка {row_number}: поле «{field}» должно быть целым числом.')
    result = int(number)
    if result < minimum or result > maximum:
        raise WorkloadImportError(
            f'Строка {row_number}: поле «{field}» должно быть от {minimum} до {maximum}.'
        )
    return result


def _hours(value, field, row_number):
    if value in (None, ''):
        return Decimal('0')
    try:
        number = Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, ValueError):
        raise WorkloadImportError(f'Строка {row_number}: поле «{field}» должно быть числом часов.')
    if not number.is_finite() or number < 0:
        raise WorkloadImportError(f'Строка {row_number}: поле «{field}» не может быть отрицательным.')
    if number.as_tuple().exponent < -1 or number >= Decimal('10000'):
        raise WorkloadImportError(
            f'Строка {row_number}: «{field}» должно содержать не более одного знака после запятой '
            'и быть меньше 10000.'
        )
    return number


def _cell(row, columns, name):
    index = columns[name]
    return row[index] if index < len(row) else None


def _read_workbook(uploaded_file):
    if uploaded_file.size > MAX_WORKBOOK_SIZE:
        raise WorkloadImportError('Размер файла превышает 2 МБ.')
    try:
        uploaded_file.seek(0)
        workbook = load_workbook(uploaded_file, read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, OSError, ValueError) as exc:
        raise WorkloadImportError('Не удалось прочитать файл. Загрузите корректный файл Excel формата .xlsx.') from exc

    try:
        worksheet = workbook.active
        rows = worksheet.iter_rows(values_only=True)
        headers = next(rows, None)
        if not headers:
            raise WorkloadImportError('Файл Excel пуст.')

        header_indexes = {_normalize_header(value): index for index, value in enumerate(headers)}
        columns = {}
        missing = []
        for name, title in REQUIRED_HEADERS.items():
            index = header_indexes.get(title)
            if index is None:
                missing.append(title)
            else:
                columns[name] = index
        if missing:
            raise WorkloadImportError(
                'Не найдены обязательные столбцы: ' + ', '.join(missing) + '.'
            )

        parsed_rows = []
        warnings = 0
        for row_number, row in enumerate(rows, start=2):
            if not any(value not in (None, '') for value in row):
                continue
            name = str(_cell(row, columns, 'discipline') or '').strip()
            if not name:
                raise WorkloadImportError(f'Строка {row_number}: не указано название дисциплины.')
            if len(name) > 255:
                raise WorkloadImportError(f'Строка {row_number}: название дисциплины длиннее 255 символов.')

            specialties = tuple(dict.fromkeys(
                value.strip() for value in str(_cell(row, columns, 'specialty') or '').split(',')
                if value.strip()
            ))
            if not specialties:
                raise WorkloadImportError(f'Строка {row_number}: не указана специальность.')
            if any(len(value) > 50 for value in specialties):
                raise WorkloadImportError(f'Строка {row_number}: код специальности длиннее 50 символов.')

            course = _integer(_cell(row, columns, 'course'), 'Курс', row_number,
                              required=True, minimum=1, maximum=8)
            semester_value = _integer(_cell(row, columns, 'semester'), 'Семестр', row_number,
                                      minimum=0, maximum=8)
            if semester_value is None:
                semester_value = 0
            if semester_value and semester_value not in (course * 2 - 1, course * 2):
                raise WorkloadImportError(
                    f'Строка {row_number}: семестр {semester_value} не соответствует курсу {course}.'
                )
            if semester_value == 0:
                semester_value = course * 2 - 1
                warnings += 1

            form_code = str(_cell(row, columns, 'study_form') or '').strip().casefold()
            study_form = STUDY_FORMS.get(form_code)
            if not study_form:
                raise WorkloadImportError(
                    f'Строка {row_number}: неизвестная форма обучения «{form_code}».'
                )

            students = _integer(_cell(row, columns, 'students'), 'Студентов', row_number,
                                required=True, minimum=1)
            group_count = _integer(_cell(row, columns, 'groups'), 'Групп', row_number,
                                   required=True, minimum=1)
            if group_count < len(specialties):
                specialties = (str(_cell(row, columns, 'specialty')).strip(),)
                if len(specialties[0]) > 50:
                    raise WorkloadImportError(
                        f'Строка {row_number}: код совместной специальности длиннее 50 символов.'
                    )
            if group_count > students:
                raise WorkloadImportError(
                    f'Строка {row_number}: число групп не может быть больше числа студентов.'
                )
            subgroups = _integer(_cell(row, columns, 'subgroups'), 'Подгрупп', row_number,
                                 minimum=1)
            control_works = _integer(_cell(row, columns, 'control_works'), 'Кол-во КР', row_number,
                                     minimum=0)
            exam_marker = _cell(row, columns, 'exams') not in (None, '')
            credit_marker = _cell(row, columns, 'credits') not in (None, '')
            if exam_marker and credit_marker:
                raise WorkloadImportError(
                    f'Строка {row_number}: одновременно указаны экзамен и зачёт.'
                )

            parsed_rows.append(WorkloadRow(
                row_number=row_number,
                name=name,
                specialties=specialties,
                course=course,
                semester=semester_value,
                study_form=study_form,
                students=students,
                groups=group_count,
                subgroups=subgroups,
                hours_lec=_hours(_cell(row, columns, 'lectures'), 'Лекции (по плану)', row_number),
                hours_pract=_hours(_cell(row, columns, 'practices'), 'Практические (по плану)', row_number),
                hours_lab=_hours(_cell(row, columns, 'labs'), 'Лабораторные (по плану)', row_number),
                hours_usr=_hours(_cell(row, columns, 'usr'), 'УСР', row_number),
                control_works=control_works or 0,
                control_form='exam' if exam_marker else 'credit' if credit_marker else 'none',
            ))
        if not parsed_rows:
            raise WorkloadImportError('В книге не найдено ни одной строки с данными.')
        return parsed_rows, warnings
    finally:
        workbook.close()


def _cohort_candidates(rows):
    single_specialty = defaultdict(list)
    shared_specialty = defaultdict(list)
    for row in rows:
        groups_left = row.groups
        students_left = row.students
        subgroups_left = row.subgroups
        specialty_count = len(row.specialties)
        for offset, abbreviation in enumerate(row.specialties):
            remaining = specialty_count - offset
            groups = groups_left // remaining
            students = students_left // remaining
            subgroups = subgroups_left // remaining if subgroups_left is not None else None
            groups_left -= groups
            students_left -= students
            if subgroups_left is not None:
                subgroups_left -= subgroups
            key = (abbreviation.casefold(), row.course, row.study_form)
            candidate = (groups, students, subgroups)
            target = single_specialty if specialty_count == 1 else shared_specialty
            target[key].append(candidate)

    result = {}
    for key in set(single_specialty) | set(shared_specialty):
        candidates = single_specialty.get(key) or shared_specialty[key]
        result[key] = (
            max(item[0] for item in candidates),
            max(item[1] for item in candidates),
            max((item[2] for item in candidates if item[2] is not None), default=None),
        )
    return result


def _make_group_name(abbreviation, academic_year, course, study_form, number):
    suffix = hashlib.sha256(abbreviation.casefold().encode('utf-8')).hexdigest()[:6]
    safe_abbreviation = ''.join(char for char in abbreviation if char.isalnum())[:18] or 'group'
    form_code = STUDY_FORM_CODES[study_form]
    return f'{safe_abbreviation}-{suffix}-{academic_year}-{course}-{form_code}-{number}'[:50]


def import_workload_xlsx(
    uploaded_file, department: Department | None, academic_year: int, *, department_name=None
):
    """Проверить и обновить учебные данные, затем пересчитать нагрузку кафедры."""
    rows, warning_count = _read_workbook(uploaded_file)
    if department is None and not department_name:
        raise WorkloadImportError('Выберите существующую кафедру или укажите название новой.')
    cohorts = _cohort_candidates(rows)
    specialty_cache = {}
    curriculum_cache = {}
    group_cache = {}
    imported_specialties = set()
    imported_groups = set()
    imported_disciplines = set()

    with transaction.atomic():
        if department is None:
            department, _ = Department.objects.get_or_create(name=department_name)

        for row in rows:
            admission_year = academic_year - row.course + 1
            for abbreviation in row.specialties:
                specialty_key = abbreviation.casefold()
                specialty = specialty_cache.get(specialty_key)
                if specialty is None:
                    specialty = Specialty.objects.filter(abbr__iexact=abbreviation).order_by('pk').first()
                    if specialty is None:
                        code = 'imp-' + hashlib.sha256(specialty_key.encode('utf-8')).hexdigest()[:16]
                        specialty, _ = Specialty.objects.get_or_create(
                            code=code,
                            defaults={'name': abbreviation, 'abbr': abbreviation},
                        )
                    specialty_cache[specialty_key] = specialty
                    imported_specialties.add(specialty.pk)

                curriculum_key = (specialty.pk, admission_year, row.study_form)
                curriculum = curriculum_cache.get(curriculum_key)
                if curriculum is None:
                    curriculum, _ = Curriculum.objects.get_or_create(
                        specialty=specialty,
                        admission_year=admission_year,
                        study_form=row.study_form,
                    )
                    curriculum_cache[curriculum_key] = curriculum

                cohort_key = (specialty_key, row.course, row.study_form)
                group_count, student_count, subgroup_count = cohorts[cohort_key]
                base_students, extra_students = divmod(student_count, group_count)
                base_subgroups, extra_subgroups = (
                    divmod(subgroup_count, group_count) if subgroup_count is not None else (None, 0)
                )
                for number in range(1, group_count + 1):
                    group_name = _make_group_name(
                        abbreviation, academic_year, row.course, row.study_form, number
                    )
                    defaults = {
                        'specialty': specialty,
                        'course': row.course,
                        'study_form': row.study_form,
                        'students_count': base_students + (number <= extra_students),
                        'subgroups_count': (
                            max(1, base_subgroups + (number <= extra_subgroups))
                            if base_subgroups is not None else None
                        ),
                    }
                    group, _ = Group.objects.update_or_create(name=group_name, defaults=defaults)
                    group_cache[(specialty_key, row.course, row.study_form, number)] = group
                    imported_groups.add(group.pk)

            specialty_codes = {value.casefold() for value in row.specialties}
            groups_for_discipline = [
                group for key, group in group_cache.items() if key[0] in specialty_codes
                and key[1] == row.course and key[2] == row.study_form
            ]
            curriculum = curriculum_cache[(specialty_cache[row.specialties[0].casefold()].pk,
                                           admission_year, row.study_form)]
            group_ids = sorted(group.pk for group in groups_for_discipline)
            discipline = None
            for candidate in Discipline.objects.filter(
                curriculum=curriculum, name=row.name, semester=row.semester
            ):
                if sorted(candidate.groups.values_list('pk', flat=True)) == group_ids:
                    discipline = candidate
                    break
            if discipline is None:
                discipline = Discipline(curriculum=curriculum, name=row.name, semester=row.semester)
            discipline.department = department
            discipline.hours_lec = row.hours_lec
            discipline.hours_pract = row.hours_pract
            discipline.hours_lab = row.hours_lab
            discipline.hours_usr = row.hours_usr
            discipline.control_works = row.control_works
            discipline.control_form = row.control_form
            discipline.save()
            discipline.groups.set(groups_for_discipline)
            imported_disciplines.add(discipline.pk)

        recalculate_department(department, CalcSettings.get())

    return {
        'rows': len(rows),
        'specialties': len(imported_specialties),
        'groups': len(imported_groups),
        'disciplines': len(imported_disciplines),
        'warnings': warning_count,
    }
