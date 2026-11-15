"""
Тесты расчётов и формул нагрузки.
"""

from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.contrib.auth.models import User, Group as AuthGroup
from decimal import Decimal
from .models import (
    Specialty, Group, Curriculum, Discipline, Department, CalcSettings,
    PositionRule, Teacher, LoadItem, Adjustment, JournalEntry
)
from .calculations import calc_department_load, teacher_total, check_teacher, report_for_teacher


class CalcDepartmentLoadTestCase(TestCase):
    """Тесты расчёта нагрузки по дисциплине."""

    def setUp(self):
        """Подготовить данные для тестов."""
        # Специальность
        self.specialty = Specialty.objects.create(
            code='09.03.03',
            name='Прикладная информатика',
            abbr='ПИ'
        )

        # Кафедра
        self.department = Department.objects.create(
            name='Кафедра ИС'
        )

        # Учебный план
        self.curriculum = Curriculum.objects.create(
            specialty=self.specialty,
            admission_year=2023,
            study_form='full_time'
        )

        # Группы: 2 группы по 24 и 23 студента
        self.group1 = Group.objects.create(
            name='ПИ-22-1',
            specialty=self.specialty,
            course=2,
            study_form='full_time',
            students_count=24,
            subgroups_count=2
        )

        self.group2 = Group.objects.create(
            name='ПИ-22-2',
            specialty=self.specialty,
            course=2,
            study_form='full_time',
            students_count=23,
            subgroups_count=2
        )

        # Параметры расчёта (значения по умолчанию из CalcSettings)
        self.settings = CalcSettings.get()

    def test_calc_discipline_with_exam_default_params(self):
        """
        Контрольный тест: дисциплина с экзаменом.
        30 ч лекций, 20 практических, 40 лабораторных, 2 контрольные, УСР 18
        Ожидаемый итог: 277.95 часов

        Расчёт:
        - Лекции: 30 × 1 = 30
        - Практики: 20 × 2 групп = 40
        - Лабораторные: 40 × 4 подгруппы (2 группы × 2 подгруппы) = 160
        - Экзамен: (24 + 23) × 0.35 = 16.45
        - Консультации к экзамену: 2 × 2 групп = 4
        - Контроль УСР: 2 × 2 групп = 4
        - КР: (24 + 23) × 0.25 × 2 = 23.5

        Итого: 30 + 40 + 160 + 16.45 + 4 + 4 + 23.5 = 277.95
        """
        discipline = Discipline.objects.create(
            curriculum=self.curriculum,
            department=self.department,
            name='Основы программирования',
            semester=1,
            control_form='exam',
            hours_lec=Decimal('30'),
            hours_pract=Decimal('20'),
            hours_lab=Decimal('40'),
            hours_usr=Decimal('18'),
            control_works=2,
            credits=Decimal('5')
        )
        discipline.groups.add(self.group1, self.group2)

        # Рассчитать нагрузку
        load_items = calc_department_load(discipline, self.settings)

        # Вычислить сумму часов
        total_hours = sum(Decimal(str(item['hours'])) for item in load_items)

        # Проверить результат (допуск 0.01)
        expected = Decimal('277.95')
        self.assertAlmostEqual(float(total_hours), float(expected), places=2)

    def test_calc_param_change_exam_hours_per_student(self):
        """
        Тест смены параметра: при изменении exam_hours_per_student на 0.5
        часы экзамена должны суммироваться иначе.

        С новым параметром:
        - Экзамен: (24 + 23) × 0.5 = 23.5 (было 16.45)
        - Остальное то же

        Итого: 30 + 40 + 160 + 23.5 + 4 + 4 + 23.5 = 285.0
        """
        # Изменить параметр
        self.settings.exam_hours_per_student = Decimal('0.5')
        self.settings.save()

        discipline = Discipline.objects.create(
            curriculum=self.curriculum,
            department=self.department,
            name='Основы программирования',
            semester=1,
            control_form='exam',
            hours_lec=Decimal('30'),
            hours_pract=Decimal('20'),
            hours_lab=Decimal('40'),
            hours_usr=Decimal('18'),
            control_works=2,
            credits=Decimal('5')
        )
        discipline.groups.add(self.group1, self.group2)

        load_items = calc_department_load(discipline, self.settings)
        total_hours = sum(Decimal(str(item['hours'])) for item in load_items)

        expected = Decimal('285.0')
        self.assertAlmostEqual(float(total_hours), float(expected), places=2)

    def test_exam_and_consultation_are_one_load_item_per_group(self):
        discipline = Discipline.objects.create(
            curriculum=self.curriculum,
            department=self.department,
            name='Экзамен с консультацией',
            semester=1,
            control_form='exam',
            hours_lec=Decimal('0'),
            hours_pract=Decimal('0'),
            hours_lab=Decimal('0'),
            hours_usr=Decimal('0'),
            control_works=0
        )
        discipline.groups.add(self.group1, self.group2)

        exam_items = [
            item for item in calc_department_load(discipline, self.settings)
            if item['kind'] == 'exam'
        ]

        self.assertEqual(len(exam_items), 2)
        self.assertEqual(len({item['group'].pk for item in exam_items}), 2)
        self.assertEqual(
            {item['group'].pk: item['hours'] for item in exam_items},
            {
                self.group1.pk: Decimal('10.40'),
                self.group2.pk: Decimal('10.05'),
            }
        )

    def test_calc_discipline_with_credit(self):
        """Тест дисциплины с зачётом вместо экзамена."""
        discipline = Discipline.objects.create(
            curriculum=self.curriculum,
            department=self.department,
            name='Математика',
            semester=1,
            control_form='credit',
            hours_lec=Decimal('20'),
            hours_pract=Decimal('10'),
            hours_lab=Decimal('0'),
            hours_usr=Decimal('0'),
            control_works=0,
            credits=Decimal('3')
        )
        discipline.groups.add(self.group1, self.group2)

        load_items = calc_department_load(discipline, self.settings)
        total_hours = sum(Decimal(str(item['hours'])) for item in load_items)

        # Лекции + практики + зачёты
        # 20 + (10 × 2) + (47 × 0.25) = 20 + 20 + 11.75 = 51.75
        expected = Decimal('51.75')
        self.assertAlmostEqual(float(total_hours), float(expected), places=2)

    def test_calc_discipline_without_usr_control(self):
        """УСР контроль не должен добавляться, если hours_usr = 0."""
        discipline = Discipline.objects.create(
            curriculum=self.curriculum,
            department=self.department,
            name='Логика',
            semester=1,
            control_form='credit',
            hours_lec=Decimal('15'),
            hours_pract=Decimal('0'),
            hours_lab=Decimal('0'),
            hours_usr=Decimal('0'),  # нет УСР
            control_works=0,
            credits=Decimal('2')
        )
        discipline.groups.add(self.group1, self.group2)

        load_items = calc_department_load(discipline, self.settings)

        # Проверить, что нет элементов kind='usr'
        usr_items = [item for item in load_items if item['kind'] == 'usr']
        self.assertEqual(len(usr_items), 0)

    def test_calc_single_group_discipline(self):
        """Тест дисциплины с одной группой."""
        single_group = Group.objects.create(
            name='МН-11',
            specialty=self.specialty,
            course=1,
            study_form='full_time',
            students_count=30,
            subgroups_count=2
        )

        discipline = Discipline.objects.create(
            curriculum=self.curriculum,
            department=self.department,
            name='Философия',
            semester=1,
            control_form='exam',
            hours_lec=Decimal('15'),
            hours_pract=Decimal('0'),
            hours_lab=Decimal('0'),
            hours_usr=Decimal('0'),
            control_works=0,
            credits=Decimal('2')
        )
        discipline.groups.add(single_group)

        load_items = calc_department_load(discipline, self.settings)

        # Лекции + экзамен + консультация
        # 15 + (30 × 0.35) + 2 = 27.5
        total_hours = sum(Decimal(str(item['hours'])) for item in load_items)
        expected = Decimal('27.5')
        self.assertAlmostEqual(float(total_hours), float(expected), places=2)

        exam_items = [item for item in load_items if item['kind'] == 'exam']
        self.assertEqual(len(exam_items), 1)
        self.assertEqual(exam_items[0]['hours'], Decimal('12.50'))


class TeacherCheckTestCase(TestCase):
    """Тесты проверки нагрузки преподавателя."""

    def setUp(self):
        """Подготовить учителя с нагрузкой."""
        # Пользователь Django
        self.user = User.objects.create_user(
            username='prof_ivanov',
            password='testpass123'
        )

        # Должность
        self.position_docent = PositionRule.objects.create(
            position='Доцент',
            min_hours=840,
            max_hours=900,
            min_lecture_hours=200,
            can_lecture=True
        )

        # Кафедра
        self.department = Department.objects.create(name='Кафедра ИС')

        # Преподаватель
        self.teacher = Teacher.objects.create(
            user=self.user,
            full_name='Иванов И.И.',
            department=self.department,
            position=self.position_docent,
            degree='candidate',
            rank='доцент',
            rate=Decimal('1.0')
        )

        # Специальность и программа
        self.specialty = Specialty.objects.create(
            code='09.03.03',
            name='Прикладная информатика',
            abbr='ПИ'
        )

        self.curriculum = Curriculum.objects.create(
            specialty=self.specialty,
            admission_year=2023,
            study_form='full_time'
        )

        # Дисциплина
        self.discipline = Discipline.objects.create(
            curriculum=self.curriculum,
            department=self.department,
            name='Программирование',
            semester=1,
            control_form='exam',
            hours_lec=Decimal('30'),
            hours_pract=Decimal('20'),
            hours_lab=Decimal('0'),
            hours_usr=Decimal('0'),
            control_works=0,
            credits=Decimal('4')
        )

    def test_teacher_total_no_load(self):
        """Преподаватель без нагрузки должен иметь 0 часов."""
        total = teacher_total(self.teacher)
        self.assertEqual(total, Decimal('0'))

    def test_teacher_total_with_load(self):
        """Сумма часов преподавателя с нагрузкой."""
        LoadItem.objects.create(
            discipline=self.discipline,
            kind='lec',
            group=None,
            subgroup_no=None,
            hours=Decimal('30'),
            teacher=self.teacher
        )

        LoadItem.objects.create(
            discipline=self.discipline,
            kind='pract',
            group=None,
            subgroup_no=None,
            hours=Decimal('20'),
            teacher=self.teacher
        )

        total = teacher_total(self.teacher)
        self.assertEqual(total, Decimal('50'))

    def test_check_teacher_below_minimum(self):
        """Тест предупреждения ниже минимума."""
        # Добавить только 100 часов (ниже минимума 840)
        LoadItem.objects.create(
            discipline=self.discipline,
            kind='lec',
            group=None,
            subgroup_no=None,
            hours=Decimal('100'),
            teacher=self.teacher
        )

        warnings = check_teacher(self.teacher)
        self.assertTrue(any('минимума' in w for w in warnings))

    def test_check_teacher_above_maximum(self):
        """Тест предупреждения выше максимума."""
        # Добавить 950 часов (выше максимума 900)
        LoadItem.objects.create(
            discipline=self.discipline,
            kind='lec',
            group=None,
            subgroup_no=None,
            hours=Decimal('950'),
            teacher=self.teacher
        )

        warnings = check_teacher(self.teacher)
        self.assertTrue(any('максимума' in w for w in warnings))

    def test_effective_min_max_with_rate_half(self):
        """При ставке 0.5 эффективные min/max должны быть вполовину."""
        self.teacher.rate = Decimal('0.5')
        self.teacher.save()

        self.assertEqual(self.teacher.effective_min_hours, 420)
        self.assertEqual(self.teacher.effective_max_hours, 450)

    def test_effective_min_max_override(self):
        """Переопределённые минимум/максимум должны приоритизироваться."""
        self.teacher.override_min_hours = 500
        self.teacher.override_max_hours = 600
        self.teacher.save()

        self.assertEqual(self.teacher.effective_min_hours, 500)
        self.assertEqual(self.teacher.effective_max_hours, 600)


class CalcSettingsTestCase(TestCase):
    """Тесты синглтона CalcSettings."""

    def test_calcsettings_get_creates_default(self):
        """CalcSettings.get() должен создать запись со значениями по умолчанию."""
        # Убрать все записи
        CalcSettings.objects.all().delete()

        # Вызвать get()
        settings = CalcSettings.get()

        # Проверить, что создана ровно одна запись
        self.assertEqual(CalcSettings.objects.count(), 1)
        self.assertEqual(settings.exam_hours_per_student, Decimal('0.35'))
        self.assertEqual(settings.subgroups_per_group, 2)

    def test_calcsettings_get_returns_existing(self):
        """CalcSettings.get() должен вернуть существующую запись."""
        CalcSettings.objects.all().delete()

        # Создать с нестандартным значением
        CalcSettings.objects.create(
            exam_hours_per_student=Decimal('0.5')
        )

        # Вызвать get()
        settings = CalcSettings.get()

        # Должна остаться одна запись с нашим значением
        self.assertEqual(CalcSettings.objects.count(), 1)
        self.assertEqual(settings.exam_hours_per_student, Decimal('0.5'))


class ReportTestCase(TestCase):
    def setUp(self):
        self.teacher_group, _ = AuthGroup.objects.get_or_create(name='teacher')
        self.user = User.objects.create_user(username='report_teacher', password='pass')
        self.user.groups.add(self.teacher_group)
        self.department = Department.objects.create(name='Кафедра отчётов')
        self.teacher = Teacher.objects.create(
            user=self.user,
            full_name='Преподаватель отчётов',
            department=self.department
        )
        specialty = Specialty.objects.create(code='99.01.01', name='Тест', abbr='Т')
        curriculum = Curriculum.objects.create(
            specialty=specialty,
            admission_year=2025,
            study_form='full_time'
        )
        self.discipline = Discipline.objects.create(
            curriculum=curriculum,
            department=self.department,
            name='Тестовая дисциплина',
            semester=1,
            control_form='exam'
        )
        self.load_item = LoadItem.objects.create(
            discipline=self.discipline,
            kind='lec',
            hours=Decimal('90'),
            teacher=self.teacher
        )

    def test_teacher_can_view_own_report(self):
        self.client.force_login(self.user)

        response = self.client.get('/report/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.teacher.full_name)

    def test_teacher_workload_export_is_valid_xlsx(self):
        from io import BytesIO
        from openpyxl import load_workbook

        self.client.force_login(self.user)

        response = self.client.get('/export/workload/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', response['Content-Type'])
        workbook = load_workbook(BytesIO(response.content), read_only=True)
        worksheet = workbook.active
        self.assertEqual(worksheet['A1'].value, 'Дисциплина')
        self.assertEqual(worksheet['A2'].value, self.discipline.name)
        workbook.close()

    def test_teacher_cannot_export_department_load(self):
        self.client.force_login(self.user)

        response = self.client.get('/export/department-load/')

        self.assertEqual(response.status_code, 403)

    def test_teacher_cannot_export_department_teachers_or_other_teacher_data(self):
        self.client.force_login(self.user)

        for export_type in ('teachers',):
            with self.subTest(export_type=export_type):
                response = self.client.get(f'/export/{export_type}/')
                self.assertEqual(response.status_code, 403)

    def test_head_can_export_department_load_and_teachers_only(self):
        from io import BytesIO
        from openpyxl import load_workbook

        head_user = User.objects.create_user(username='export_head', password='pass')
        head_user.groups.add(AuthGroup.objects.create(name='head'))
        Teacher.objects.create(
            user=head_user,
            full_name='Заведующий экспортом',
            department=self.department
        )
        self.client.force_login(head_user)

        for export_type in ('department-load', 'teachers', 'report'):
            with self.subTest(export_type=export_type):
                response = self.client.get(f'/export/{export_type}/')
                self.assertEqual(response.status_code, 200)
                self.assertIn('spreadsheetml', response['Content-Type'])

        department_load = self.client.get('/export/department-load/')
        workbook = load_workbook(BytesIO(department_load.content), read_only=True)
        self.assertEqual(workbook.active['A1'].value, 'Дисциплина')
        workbook.close()
        teachers_export = self.client.get('/export/teachers/')
        workbook = load_workbook(BytesIO(teachers_export.content), read_only=True)
        self.assertEqual(workbook.active['A1'].value, 'ФИО')
        workbook.close()

        for export_type in ('workload', 'journal'):
            with self.subTest(export_type=export_type):
                self.assertEqual(self.client.get(f'/export/{export_type}/').status_code, 403)

    def test_all_teacher_exports_are_valid_xlsx_and_scope_to_current_teacher(self):
        from datetime import date
        from io import BytesIO
        from openpyxl import load_workbook

        JournalEntry.objects.create(
            teacher=self.teacher,
            load_item=self.load_item,
            date=date(2025, 3, 10),
            hours=Decimal('2'),
            topic='Моя тема'
        )
        self.client.force_login(self.user)

        for export_type, expected_header in (
            ('workload', 'Дисциплина'),
            ('journal', 'Дата'),
            ('report', 'Месяц'),
        ):
            with self.subTest(export_type=export_type):
                response = self.client.get(f'/export/{export_type}/')
                self.assertEqual(response.status_code, 200)
                workbook = load_workbook(BytesIO(response.content), read_only=True)
                worksheet = workbook.active
                self.assertEqual(worksheet['A1'].value, expected_header)
                workbook.close()

    def test_user_without_report_role_cannot_export_report(self):
        user = User.objects.create_user(username='export_no_role', password='pass')
        self.client.force_login(user)

        self.assertEqual(self.client.get('/export/report/').status_code, 403)

    def test_user_without_report_role_is_forbidden(self):
        user = User.objects.create_user(username='no_report_role', password='pass')
        self.client.force_login(user)

        response = self.client.get('/report/')

        self.assertEqual(response.status_code, 403)

    def test_head_cannot_select_teacher_outside_department(self):
        head_user = User.objects.create_user(username='report_head', password='pass')
        head_user.groups.add(AuthGroup.objects.create(name='head'))
        Teacher.objects.create(
            user=head_user,
            full_name='Заведующий',
            department=self.department
        )
        other_department = Department.objects.create(name='Другая кафедра')
        outsider_user = User.objects.create_user(username='outsider', password='pass')
        outsider = Teacher.objects.create(
            user=outsider_user,
            full_name='Чужой преподаватель',
            department=other_department
        )
        self.client.force_login(head_user)

        response = self.client.get('/report/', {'teacher_id': outsider.pk})

        self.assertEqual(response.status_code, 404)
        response = self.client.get('/report/', {'teacher_id': 'invalid'})
        self.assertEqual(response.status_code, 404)

    def test_teacher_can_add_adjustment_for_assigned_load(self):
        from datetime import date

        self.client.force_login(self.user)
        year = date.today().year

        response = self.client.post('/report/', {
            'action': 'add_adjustment',
            'year': year,
            'discipline_id': self.discipline.pk,
            'kind': 'lec',
            'month': 1,
            'hours_delta': '2.5',
            'reason': 'Замена занятия'
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Adjustment.objects.filter(
            teacher=self.teacher,
            discipline=self.discipline,
            year=year,
            month=1,
            hours_delta=Decimal('2.5')
        ).exists())

    def test_teacher_cannot_adjust_unassigned_discipline(self):
        from datetime import date

        other_discipline = Discipline.objects.create(
            curriculum=self.discipline.curriculum,
            department=self.department,
            name='Не назначенная дисциплина',
            semester=1,
            control_form='exam'
        )
        self.client.force_login(self.user)

        response = self.client.post('/report/', {
            'action': 'add_adjustment',
            'year': date.today().year,
            'discipline_id': other_discipline.pk,
            'kind': 'lec',
            'month': 1,
            'hours_delta': '2',
            'reason': 'Недопустимая корректировка'
        })

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Adjustment.objects.filter(discipline=other_discipline).exists())

    def test_report_filters_adjustments_by_year(self):
        from datetime import date

        year = date.today().year
        JournalEntry.objects.create(
            teacher=self.teacher,
            load_item=self.load_item,
            date=date(year, 1, 15),
            hours=Decimal('2'),
            topic='Проведённое занятие'
        )
        for adjustment_year, hours in ((year - 1, '100'), (year, '3')):
            Adjustment.objects.create(
                teacher=self.teacher,
                discipline=self.discipline,
                kind='lec',
                month=1,
                year=adjustment_year,
                hours_delta=Decimal(hours),
                reason='Тестовая корректировка'
            )

        report = report_for_teacher(self.teacher, year)

        self.assertEqual(report['months'][1]['lec']['fact'], Decimal('5'))

    def test_report_distributes_plan_over_five_semester_months(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        second_semester = Discipline.objects.create(
            curriculum=self.discipline.curriculum,
            department=self.department,
            name='Дисциплина второго семестра',
            semester=2,
            control_form='credit'
        )
        LoadItem.objects.create(
            discipline=second_semester,
            kind='lec',
            hours=Decimal('50'),
            teacher=self.teacher
        )

        with CaptureQueriesContext(connection) as queries:
            report = report_for_teacher(self.teacher, 2025)

        self.assertEqual(report['months'][1]['lec']['plan'], Decimal('18'))
        self.assertEqual(report['months'][9]['lec']['plan'], Decimal('18'))
        self.assertEqual(report['months'][2]['lec']['plan'], Decimal('10'))
        self.assertEqual(report['months'][6]['lec']['plan'], Decimal('10'))
        self.assertEqual(report['months'][12]['lec']['plan'], Decimal('18'))
        self.assertEqual(report['months'][1]['lec']['plan'], Decimal('18'))
        self.assertNotIn('lec', report['months'][7])
        self.assertLessEqual(len(queries), 4)

    def test_check_teachers_uses_bounded_number_of_queries(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        from .calculations import check_teachers

        teachers = [self.teacher]
        for index in range(3):
            user = User.objects.create_user(username=f'bulk_report_teacher_{index}', password='pass')
            teachers.append(Teacher.objects.create(
                user=user,
                full_name=f'Преподаватель {index}',
                department=self.department
            ))
        for index, teacher in enumerate(teachers):
            LoadItem.objects.create(
                discipline=self.discipline,
                kind='pract',
                group=Group.objects.create(
                    name=f'Отчётная группа {index}',
                    specialty=self.discipline.curriculum.specialty,
                    course=1,
                    study_form='full_time',
                    students_count=20,
                    subgroups_count=2
                ),
                hours=Decimal('10'),
                teacher=teacher
            )

        with CaptureQueriesContext(connection) as queries:
            warnings = check_teachers(teachers)

        self.assertEqual(set(warnings), {teacher.pk for teacher in teachers})
        self.assertLessEqual(len(queries), 5)


class SettingsRecalculationPermissionTestCase(TestCase):
    """Проверки доступа к пересчёту нагрузки из настроек."""

    def setUp(self):
        head_group, _ = AuthGroup.objects.get_or_create(name='head')
        deputy_group, _ = AuthGroup.objects.get_or_create(name='deputy_dean')
        self.head_user = User.objects.create_user(username='settings_head', password='pass')
        self.head_user.groups.add(head_group)
        self.deputy_user = User.objects.create_user(username='settings_deputy', password='pass')
        self.deputy_user.groups.add(deputy_group)
        self.department = Department.objects.create(name='Кафедра настроек')
        self.settings = CalcSettings.get()

    def test_head_cannot_recalculate_any_department(self):
        from unittest.mock import patch

        self.client.force_login(self.head_user)
        with patch('core.views.recalculate_department') as recalculate:
            response = self.client.post('/settings/', {'action': 'recalculate_all'})

        self.assertEqual(response.status_code, 403)
        recalculate.assert_not_called()
        self.assertEqual(LoadItem.objects.count(), 0)

    def test_deputy_dean_can_recalculate_departments(self):
        from unittest.mock import patch

        self.client.force_login(self.deputy_user)
        with patch('core.views.recalculate_department') as recalculate:
            response = self.client.post('/settings/', {'action': 'recalculate_all'})

        self.assertEqual(response.status_code, 302)
        recalculate.assert_called_once_with(self.department, self.settings)

    def test_head_can_still_update_global_calculation_settings(self):
        self.client.force_login(self.head_user)

        response = self.client.post('/settings/', {
            'action': 'update_settings',
            'exam_hours_per_student': '0.5',
            'exam_consult_hours_per_group': '2',
            'credit_hours_per_student': '0.25',
            'usr_control_hours_per_group': '2',
            'kr_hours_per_student': '0.25',
            'subgroups_per_group': '2'
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(CalcSettings.get().exam_hours_per_student, Decimal('0.5'))

    def test_invalid_settings_are_not_saved_and_show_field_error(self):
        self.client.force_login(self.head_user)
        response = self.client.post('/settings/', {
            'action': 'update_settings',
            'exam_hours_per_student': '-0.1',
            'exam_consult_hours_per_group': '2',
            'credit_hours_per_student': '0.25',
            'usr_control_hours_per_group': '2',
            'kr_hours_per_student': '0.25',
            'subgroups_per_group': '2'
        }, follow=True)

        self.settings.refresh_from_db()
        self.assertEqual(self.settings.exam_hours_per_student, Decimal('0.35'))
        self.assertContains(response, 'Часы экзамена на одного студента')

    def test_recalculate_button_is_only_shown_to_deputy_dean(self):
        self.client.force_login(self.head_user)
        head_response = self.client.get('/settings/')
        self.assertNotContains(head_response, 'Пересчитать нагрузку всех кафедр')

        self.client.force_login(self.deputy_user)
        deputy_response = self.client.get('/settings/')
        self.assertContains(deputy_response, 'Пересчитать нагрузку всех кафедр')


class DistributionAssignmentPermissionTestCase(TestCase):
    def test_head_cannot_assign_department_load_to_another_department_teacher(self):
        head_role, _ = AuthGroup.objects.get_or_create(name='head')
        head_user = User.objects.create_user(username='assignment_head', password='pass')
        head_user.groups.add(head_role)
        department = Department.objects.create(name='Кафедра назначения')
        head = Teacher.objects.create(
            user=head_user,
            full_name='Заведующий назначением',
            department=department
        )
        outsider_department = Department.objects.create(name='Чужая кафедра')
        outsider_user = User.objects.create_user(username='assignment_outsider', password='pass')
        outsider = Teacher.objects.create(
            user=outsider_user,
            full_name='Чужой преподаватель',
            department=outsider_department
        )
        specialty = Specialty.objects.create(code='95.01.01', name='Назначение', abbr='Н')
        curriculum = Curriculum.objects.create(
            specialty=specialty,
            admission_year=2025,
            study_form='full_time'
        )
        discipline = Discipline.objects.create(
            curriculum=curriculum,
            department=department,
            name='Дисциплина назначения',
            semester=1,
            control_form='credit'
        )
        item = LoadItem.objects.create(
            discipline=discipline,
            kind='lec',
            hours=Decimal('10')
        )
        self.client.force_login(head_user)

        response = self.client.post('/distribution/', {
            'load_item_id': item.pk,
            'teacher_id': outsider.pk,
        })

        item.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(item.teacher_id)

class ValidationFormsTestCase(TestCase):
    """Проверки валидации форм настроек и журнала."""

    def setUp(self):
        self.department = Department.objects.create(name='Кафедра форм')
        self.user = User.objects.create_user(username='forms_teacher', password='pass')
        self.teacher = Teacher.objects.create(
            user=self.user,
            full_name='Преподаватель форм',
            department=self.department
        )
        specialty = Specialty.objects.create(code='98.01.01', name='Формы', abbr='Ф')
        curriculum = Curriculum.objects.create(
            specialty=specialty,
            admission_year=2025,
            study_form='full_time'
        )
        self.discipline = Discipline.objects.create(
            curriculum=curriculum,
            department=self.department,
            name='Дисциплина форм',
            semester=1,
            control_form='exam'
        )
        self.load_item = LoadItem.objects.create(
            discipline=self.discipline,
            kind='lec',
            hours=Decimal('90'),
            teacher=self.teacher
        )

    def test_calc_settings_form_rejects_negative_hours(self):
        from .forms import CalcSettingsForm

        fields = (
            'exam_hours_per_student',
            'exam_consult_hours_per_group',
            'credit_hours_per_student',
            'usr_control_hours_per_group',
            'kr_hours_per_student'
        )
        for field in fields:
            data = {
                'exam_hours_per_student': '0.35',
                'exam_consult_hours_per_group': '2',
                'credit_hours_per_student': '0.25',
                'usr_control_hours_per_group': '2',
                'kr_hours_per_student': '0.25',
                'subgroups_per_group': '2',
            }
            data[field] = '-0.01'
            with self.subTest(field=field):
                self.assertFalse(CalcSettingsForm(data).is_valid())

    def test_calc_settings_form_restricts_subgroup_count(self):
        from .forms import CalcSettingsForm

        for count in ('0', '11'):
            data = {
                'exam_hours_per_student': '0.35',
                'exam_consult_hours_per_group': '2',
                'credit_hours_per_student': '0.25',
                'usr_control_hours_per_group': '2',
                'kr_hours_per_student': '0.25',
                'subgroups_per_group': count,
            }
            with self.subTest(count=count):
                self.assertFalse(CalcSettingsForm(data).is_valid())

    def test_position_forms_validate_hour_ranges(self):
        from .forms import AddPositionForm, PositionRuleForm

        for form_class, position in (
            (PositionRuleForm, 'Доцент форм'),
            (AddPositionForm, 'Новая должность форм')
        ):
            with self.subTest(form=form_class.__name__, error='min_hours'):
                form = form_class(data={
                    'position': position,
                    'min_hours': '901',
                    'max_hours': '900',
                    'min_lecture_hours': '200',
                    'can_lecture': 'on',
                })
                self.assertFalse(form.is_valid())

            with self.subTest(form=form_class.__name__, error='min_lecture_hours'):
                form = form_class(data={
                    'position': position,
                    'min_hours': '800',
                    'max_hours': '900',
                    'min_lecture_hours': '901',
                    'can_lecture': 'on',
                })
                self.assertFalse(form.is_valid())

    def test_journal_form_rejects_invalid_hours_and_date(self):
        from .forms import JournalEntryForm

        base_data = {
            'load_item': str(self.load_item.pk),
            'date': '2025-03-10',
            'time': '',
            'hours': '2',
            'topic': 'Тема занятия',
        }
        for hours in ('0', '24.01'):
            data = {**base_data, 'hours': hours}
            with self.subTest(hours=hours):
                self.assertFalse(JournalEntryForm(data, teacher=self.teacher).is_valid())

        invalid_date = {**base_data, 'date': '2025-02-30'}
        self.assertFalse(JournalEntryForm(invalid_date, teacher=self.teacher).is_valid())

    def test_journal_form_only_offers_load_assigned_to_teacher(self):
        from .forms import JournalEntryForm

        other_user = User.objects.create_user(username='other_forms_teacher', password='pass')
        other_teacher = Teacher.objects.create(
            user=other_user,
            full_name='Другой преподаватель форм',
            department=self.department
        )
        other_item = LoadItem.objects.create(
            discipline=self.discipline,
            kind='pract',
            hours=Decimal('30'),
            teacher=other_teacher
        )
        form = JournalEntryForm(teacher=self.teacher)

        self.assertQuerySetEqual(form.fields['load_item'].queryset, [self.load_item])
        self.assertNotIn(other_item, form.fields['load_item'].queryset)

    def test_journal_view_rejects_invalid_hours(self):
        teacher_group, _ = AuthGroup.objects.get_or_create(name='teacher')
        self.user.groups.add(teacher_group)
        self.client.force_login(self.user)

        response = self.client.post('/journal/', {
            'action': 'add_entry',
            'load_item': self.load_item.pk,
            'date': '2025-03-10',
            'time': '',
            'hours': '24.01',
            'topic': 'Тема занятия'
        }, follow=True)

        self.assertEqual(JournalEntry.objects.filter(teacher=self.teacher).count(), 0)
        self.assertContains(response, 'Затрачено часов')


class RemovedEndpointTestCase(SimpleTestCase):
    def test_journal_kinds_route_is_not_registered(self):
        from django.urls import Resolver404, resolve

        with self.assertRaises(Resolver404):
            resolve('/journal/kinds/')


class RecalculateDepartmentTestCase(TestCase):
    """Проверки повторного и транзакционного пересчёта кафедры."""

    def setUp(self):
        self.settings = CalcSettings.get()
        self.department = Department.objects.create(name='Кафедра пересчёта')
        specialty = Specialty.objects.create(code='96.01.01', name='Пересчёт', abbr='П')
        self.specialty = specialty
        self.group = Group.objects.create(
            name='П-25-1',
            specialty=specialty,
            course=1,
            study_form='full_time',
            students_count=20,
            subgroups_count=2
        )
        curriculum = Curriculum.objects.create(
            specialty=specialty,
            admission_year=2025,
            study_form='full_time'
        )
        self.discipline = Discipline.objects.create(
            curriculum=curriculum,
            department=self.department,
            name='А дисциплина',
            semester=1,
            control_form='credit',
            hours_lec=Decimal('30')
        )
        self.teacher_user = User.objects.create_user(username='recalc_teacher', password='pass')
        self.teacher = Teacher.objects.create(
            user=self.teacher_user,
            full_name='Преподаватель пересчёта',
            department=self.department
        )

    def test_recalculation_is_idempotent_and_preserves_teacher(self):
        from .calculations import recalculate_department

        first_summary = recalculate_department(self.department, self.settings)
        item = LoadItem.objects.get(discipline=self.discipline, kind='lec')
        item.teacher = self.teacher
        item.save(update_fields=['teacher'])
        self.discipline.hours_lec = Decimal('42')
        self.discipline.save(update_fields=['hours_lec'])

        update_summary = recalculate_department(self.department, self.settings)
        unchanged_summary = recalculate_department(self.department, self.settings)

        item.refresh_from_db()
        self.assertEqual(first_summary['created'], 1)
        self.assertEqual(update_summary['updated'], 1)
        self.assertEqual(unchanged_summary['updated'], 0)
        self.assertEqual(item.hours, Decimal('42'))
        self.assertEqual(item.teacher, self.teacher)

    def test_recalculation_deletes_surplus_and_keeps_journal_items(self):
        from datetime import date
        from .calculations import recalculate_department

        assigned_surplus = LoadItem.objects.create(
            discipline=self.discipline,
            kind='lab',
            subgroup_no=1,
            hours=Decimal('5'),
            teacher=self.teacher
        )
        journal_surplus = LoadItem.objects.create(
            discipline=self.discipline,
            kind='exam',
            hours=Decimal('2'),
            teacher=self.teacher
        )
        JournalEntry.objects.create(
            teacher=self.teacher,
            load_item=journal_surplus,
            date=date(2025, 1, 10),
            hours=Decimal('2'),
            topic='Сохранить элемент'
        )

        summary = recalculate_department(self.department, self.settings)

        self.assertFalse(LoadItem.objects.filter(pk=assigned_surplus.pk).exists())
        self.assertTrue(LoadItem.objects.filter(pk=journal_surplus.pk).exists())
        self.assertEqual(summary['deleted'], 1)
        self.assertEqual(summary['deleted_with_teacher'], 1)

    def test_recalculation_rolls_back_all_changes_on_failure(self):
        from unittest.mock import patch
        from .calculations import calc_department_load, recalculate_department

        second_discipline = Discipline.objects.create(
            curriculum=self.discipline.curriculum,
            department=self.department,
            name='Б дисциплина',
            semester=1,
            control_form='credit',
            hours_lec=Decimal('20')
        )
        first_item = LoadItem.objects.create(
            discipline=self.discipline,
            kind='lec',
            hours=Decimal('10'),
            teacher=self.teacher
        )
        second_item = LoadItem.objects.create(
            discipline=second_discipline,
            kind='lec',
            hours=Decimal('10'),
            teacher=self.teacher
        )
        first_specs = calc_department_load(self.discipline, self.settings)
        with patch(
            'core.calculations.calc_department_load',
            side_effect=[first_specs, RuntimeError('test failure')]
        ):
            with self.assertRaises(RuntimeError):
                recalculate_department(self.department, self.settings)

        first_item.refresh_from_db()
        second_item.refresh_from_db()
        self.assertEqual(first_item.hours, Decimal('10'))
        self.assertEqual(second_item.hours, Decimal('10'))

    def test_load_item_unique_constraints_cover_nullable_key_shapes(self):
        from django.db import IntegrityError, transaction

        key_shapes = (
            {'group': None, 'subgroup_no': None},
            {'group': self.group, 'subgroup_no': None},
            {'group': None, 'subgroup_no': 1},
            {'group': self.group, 'subgroup_no': 1},
        )
        for key in key_shapes:
            with self.subTest(key=key):
                LoadItem.objects.create(
                    discipline=self.discipline,
                    kind='lab',
                    hours=Decimal('1'),
                    **key
                )
                with self.assertRaises(IntegrityError), transaction.atomic():
                    LoadItem.objects.create(
                        discipline=self.discipline,
                        kind='lab',
                        hours=Decimal('2'),
                        **key
                    )

    def test_exam_and_consultation_are_combined_per_group(self):
        from .calculations import calc_department_load

        self.discipline.control_form = 'exam'
        self.discipline.groups.add(self.group)

        exam_items = [
            item for item in calc_department_load(self.discipline, self.settings)
            if item['kind'] == 'exam'
        ]

        self.assertEqual(len(exam_items), 1)
        self.assertEqual(exam_items[0]['hours'], Decimal('9.00'))


class LoadItemDeduplicationMigrationTestCase(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        department = Department.objects.create(name='Кафедра миграции')
        specialty = Specialty.objects.create(code='97.01.01', name='Миграция', abbr='М')
        curriculum = Curriculum.objects.create(
            specialty=specialty,
            admission_year=2025,
            study_form='full_time'
        )
        self.discipline = Discipline.objects.create(
            curriculum=curriculum,
            department=department,
            name='Дисциплина миграции',
            semester=1,
            control_form='exam'
        )
        user = User.objects.create_user(username='migration_teacher', password='pass')
        self.teacher = Teacher.objects.create(
            user=user,
            full_name='Преподаватель миграции',
            department=department
        )

    def test_migration_keeps_assigned_duplicate_and_moves_journal(self):
        from datetime import date
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate([('core', '0001_initial')])
        old_apps = executor.loader.project_state([('core', '0001_initial')]).apps
        OldLoadItem = old_apps.get_model('core', 'LoadItem')
        OldJournalEntry = old_apps.get_model('core', 'JournalEntry')
        OldDiscipline = old_apps.get_model('core', 'Discipline')
        OldTeacher = old_apps.get_model('core', 'Teacher')
        old_discipline = OldDiscipline.objects.get(pk=self.discipline.pk)
        old_teacher = OldTeacher.objects.get(pk=self.teacher.pk)
        unassigned = OldLoadItem.objects.create(
            discipline=old_discipline,
            kind='exam',
            hours=Decimal('2')
        )
        OldLoadItem.objects.create(
            discipline=old_discipline,
            kind='exam',
            hours=Decimal('3'),
            teacher=old_teacher
        )
        OldJournalEntry.objects.create(
            teacher=old_teacher,
            load_item=unassigned,
            date=date(2025, 1, 10),
            hours=Decimal('1'),
            topic='Перенос записи'
        )

        executor = MigrationExecutor(connection)
        executor.migrate([('core', '0002_loaditem_uq_loaditem_null_stream_and_more')])
        migrated_apps = executor.loader.project_state(
            [('core', '0002_loaditem_uq_loaditem_null_stream_and_more')]
        ).apps
        MigratedLoadItem = migrated_apps.get_model('core', 'LoadItem')
        MigratedJournalEntry = migrated_apps.get_model('core', 'JournalEntry')

        self.assertEqual(
            MigratedLoadItem.objects.filter(discipline_id=self.discipline.pk, kind='exam').count(),
            1
        )
        keeper = MigratedLoadItem.objects.get(discipline_id=self.discipline.pk, kind='exam')
        self.assertEqual(keeper.teacher_id, self.teacher.pk)
        self.assertEqual(keeper.hours, Decimal('5'))
        self.assertEqual(
            MigratedJournalEntry.objects.get(topic='Перенос записи').load_item_id,
            keeper.pk
        )

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
