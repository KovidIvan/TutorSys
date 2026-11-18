"""
Тесты расчётов и формул нагрузки.
"""

from datetime import date
from django.test import TestCase
from django.contrib.auth.models import User, Group as AuthGroup
from decimal import Decimal
from .models import (
    Specialty, Group, Curriculum, Discipline, Department, CalcSettings,
    PositionRule, Teacher, LoadItem, Adjustment, JournalEntry
)
from .calculations import (
    calc_department_load, recalculate_department, teacher_total, check_teacher,
    report_for_teacher, report_matrix, teacher_workload
)
from .views import check_teacher_status


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

        # Лекции + экзамен + консультация: 15 + (30 × 0.35) + 2 = 27.5
        total_hours = sum(Decimal(str(item['hours'])) for item in load_items)
        expected = Decimal('27.5')
        self.assertAlmostEqual(float(total_hours), float(expected), places=2)

    def test_recalculation_preserves_exam_hours_and_assignment(self):
        discipline = Discipline.objects.create(
            curriculum=self.curriculum,
            department=self.department,
            name='Экзаменационная дисциплина',
            semester=1,
            control_form='exam'
        )
        discipline.groups.add(self.group1, self.group2)
        user = User.objects.create_user(username='assigned_teacher', password='pass')
        teacher = Teacher.objects.create(
            user=user,
            full_name='Назначенный преподаватель',
            department=self.department
        )
        existing = LoadItem.objects.create(
            discipline=discipline,
            kind='exam',
            group=self.group1,
            hours=Decimal('1'),
            teacher=teacher
        )

        recalculate_department(self.department, self.settings)

        existing.refresh_from_db()
        self.assertEqual(existing.hours, Decimal('10.40'))
        self.assertEqual(existing.teacher, teacher)
        self.assertEqual(
            LoadItem.objects.filter(discipline=discipline, kind='exam', group=self.group1).count(),
            1
        )
        self.assertEqual(
            LoadItem.objects.get(discipline=discipline, kind='exam', group=self.group2).hours,
            Decimal('10.05')
        )


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

    def test_check_teacher_with_lecture_capability(self):
        warnings = check_teacher(self.teacher)

        self.assertTrue(any('Лекций меньше нормы' in warning for warning in warnings))

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

    def test_multiple_warnings_are_warning_not_critical(self):
        status, warnings = check_teacher_status(self.teacher)

        self.assertEqual(status, 'warning')
        self.assertGreater(len(warnings), 1)

    def test_above_maximum_is_critical(self):
        LoadItem.objects.create(
            discipline=self.discipline,
            kind='pract',
            hours=Decimal('950'),
            teacher=self.teacher
        )

        status, _ = check_teacher_status(self.teacher)

        self.assertEqual(status, 'critical')


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

    def test_home_redirects_teacher_to_workload_without_admin_link(self):
        self.client.force_login(self.user)

        response = self.client.get('/')

        self.assertRedirects(response, '/workload/', fetch_redirect_response=False)
        response = self.client.get('/workload/')
        self.assertNotContains(response, 'href="/admin/"')

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

    def test_report_plan_is_allocated_to_semester_months(self):
        report = report_for_teacher(self.teacher, 2026)

        self.assertEqual(report['months'][9]['lec']['plan'], Decimal('18'))
        self.assertEqual(report['months'][1]['lec']['plan'], Decimal('18'))
        self.assertEqual(report['months'][2]['lec']['plan'], Decimal('0'))
        self.assertEqual(report['months'][7]['lec']['plan'], Decimal('0'))

    def test_journal_accepts_only_load_assigned_to_teacher(self):
        from datetime import date

        unassigned_item = LoadItem.objects.create(
            discipline=self.discipline,
            kind='pract',
            hours=Decimal('10')
        )
        self.client.force_login(self.user)
        payload = {
            'action': 'add_entry',
            'date': date.today().isoformat(),
            'hours': '2',
            'topic': 'Проверка назначения',
        }

        response = self.client.post('/journal/', {
            **payload,
            'load_item_id': unassigned_item.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(JournalEntry.objects.filter(load_item=unassigned_item).exists())

        response = self.client.post('/journal/', {
            **payload,
            'load_item_id': self.load_item.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(JournalEntry.objects.filter(load_item=self.load_item).exists())


class ReportMatrixTestCase(TestCase):
    """Матрица отчёта «Выполнение нагрузки»: месяц × виды работ."""

    def setUp(self):
        self.teacher_group, _ = AuthGroup.objects.get_or_create(name='teacher')
        self.head_group, _ = AuthGroup.objects.get_or_create(name='head')
        self.department = Department.objects.create(name='Кафедра матрицы')

        self.head_user = User.objects.create_user(username='matrix_head', password='pass')
        self.head_user.groups.add(self.head_group)
        self.head = Teacher.objects.create(
            user=self.head_user, full_name='Заведующий матрицы', department=self.department
        )
        self.user = User.objects.create_user(username='matrix_teacher', password='pass')
        self.user.groups.add(self.teacher_group)
        self.teacher = Teacher.objects.create(
            user=self.user, full_name='Иванов Иван', department=self.department
        )

        specialty = Specialty.objects.create(code='99.02.02', name='Матрица', abbr='М')
        curriculum = Curriculum.objects.create(
            specialty=specialty, admission_year=2025, study_form='full_time'
        )
        self.discipline = Discipline.objects.create(
            curriculum=curriculum, department=self.department,
            name='Алгебра', semester=1, control_form='exam'
        )
        self.other_discipline = Discipline.objects.create(
            curriculum=curriculum, department=self.department,
            name='Геометрия', semester=1, control_form='exam'
        )
        # Осенний семестр: план делится на 5 месяцев (сентябрь–январь).
        self.lec = LoadItem.objects.create(
            discipline=self.discipline, kind='lec', hours=Decimal('90'), teacher=self.teacher
        )
        self.head_lec = LoadItem.objects.create(
            discipline=self.other_discipline, kind='lec', hours=Decimal('45'), teacher=self.head
        )

    def add_fact(self, year=2026):
        """Сентябрь: проведено 10 ч из 18. Октябрь: замена коллеги на 30 ч сверх плана."""
        JournalEntry.objects.create(
            teacher=self.teacher, load_item=self.lec,
            date=date(year, 9, 10), hours=Decimal('10'), topic='Тема'
        )
        Adjustment.objects.create(
            teacher=self.teacher, discipline=self.discipline, kind='lec',
            month=10, year=year, hours_delta=Decimal('30'),
            reason='Замена коллеги', planned=False
        )

    def test_matrix_sums_plan_and_fact_and_marks_totals(self):
        self.add_fact()

        matrix = report_matrix([self.teacher], 2026)

        self.assertEqual(matrix['kinds'][0], ('lec', 'Лекция'))
        september, october = matrix['rows'][8], matrix['rows'][9]
        self.assertEqual(september['cells'][0]['plan'], Decimal('18'))
        self.assertEqual(september['cells'][0]['fact'], Decimal('10'))
        self.assertEqual(september['total']['css'], 'table-danger')
        self.assertEqual(october['total']['fact'], Decimal('30'))
        self.assertEqual(october['total']['css'], 'table-success')
        self.assertEqual(matrix['rows'][6]['total']['css'], '')  # июль: плана нет
        self.assertFalse(matrix['rows'][6]['total']['has_data'])
        self.assertEqual(matrix['totals']['cells'][0]['plan'], Decimal('90'))
        self.assertEqual(matrix['totals']['total']['plan'], Decimal('90'))
        self.assertEqual(matrix['totals']['total']['fact'], Decimal('40'))
        self.assertTrue(matrix['has_data'])

    def test_cells_by_kind_are_not_colored(self):
        self.add_fact()

        matrix = report_matrix([self.teacher], 2026)

        self.assertTrue(all(cell['css'] == '' for row in matrix['rows'] for cell in row['cells']))

    def test_matrix_ignores_other_years(self):
        self.add_fact(year=2025)

        matrix = report_matrix([self.teacher], 2026)

        self.assertEqual(matrix['totals']['total']['fact'], Decimal('0'))
        self.assertEqual(matrix['adjustments'], [])

    def test_department_matrix_sums_all_teachers(self):
        matrix = report_matrix([self.teacher, self.head], 2026)

        self.assertEqual(matrix['totals']['total']['plan'], Decimal('135'))
        self.assertEqual(matrix['rows'][8]['cells'][0]['plan'], Decimal('27'))
        names = {row['tn'] for row in matrix['details']}
        self.assertEqual(names, {'Иванов Иван', 'Заведующий матрицы'})

    def test_details_contain_discipline_teacher_and_reason(self):
        self.add_fact()

        matrix = report_matrix([self.teacher], 2026)

        october = [
            row for row in matrix['details']
            if row['m'] == 10 and row['k'] == 'lec' and row['d'] == self.discipline.pk
        ]
        self.assertEqual(len(october), 1)
        self.assertEqual(october[0]['tn'], 'Иванов Иван')
        self.assertEqual(october[0]['dn'], 'Алгебра')
        self.assertEqual(october[0]['r'], [{'h': '30.00', 'text': 'Замена коллеги', 'planned': False}])
        self.assertEqual(matrix['adjustments'][0]['reason'], 'Замена коллеги')

    def test_head_sees_whole_department_by_default(self):
        self.client.force_login(self.head_user)

        response = self.client.get('/report/', {'year': 2026})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Вся кафедра')
        self.assertEqual(response.context['matrix']['totals']['total']['plan'], Decimal('135'))
        self.assertTrue(response.context['details']['showTeacher'])

    def test_head_can_pick_single_teacher(self):
        self.client.force_login(self.head_user)

        response = self.client.get('/report/', {'year': 2026, 'teacher_id': self.teacher.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Иванов Иван')
        self.assertEqual(response.context['matrix']['totals']['total']['plan'], Decimal('90'))
        self.assertFalse(response.context['details']['showTeacher'])

    def test_teacher_sees_only_own_matrix(self):
        self.client.force_login(self.user)

        response = self.client.get('/report/', {'year': 2026, 'teacher_id': self.head.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['matrix']['totals']['total']['plan'], Decimal('90'))

    def test_head_cannot_add_adjustment_for_whole_department(self):
        self.client.force_login(self.head_user)

        response = self.client.post('/report/', {
            'action': 'add_adjustment', 'year': 2026,
            'discipline_id': self.discipline.pk, 'kind': 'lec', 'month': 9,
            'hours_delta': '2', 'reason': 'Причина',
        })

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Adjustment.objects.exists())

    def test_report_export_contains_matrix_sheets(self):
        from io import BytesIO
        from openpyxl import load_workbook

        self.add_fact()
        self.client.force_login(self.user)

        response = self.client.get('/export/report/', {'year': 2026})

        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(BytesIO(response.content))
        self.assertEqual(workbook.sheetnames, ['Факт', 'План', 'Отклонение', 'Корректировки'])
        plan = workbook['План']
        self.assertEqual(plan['A1'].value, 'Месяц')
        self.assertEqual(plan['B1'].value, 'Лекция')
        self.assertEqual(plan.cell(row=10, column=1).value, 'Сентябрь')
        self.assertEqual(plan.cell(row=10, column=2).value, 18)
        self.assertEqual(plan.cell(row=14, column=1).value, 'Итого за год')
        self.assertEqual(plan.cell(row=14, column=2).value, 90)
        self.assertEqual(workbook['Факт'].cell(row=14, column=2).value, 40)
        self.assertEqual(workbook['Корректировки']['G2'].value, 'Замена коллеги')

    def test_head_export_covers_whole_department(self):
        from io import BytesIO
        from openpyxl import load_workbook

        self.client.force_login(self.head_user)

        response = self.client.get('/export/report/', {'year': 2026})

        self.assertEqual(response.status_code, 200)
        self.assertIn('department_report_2026.xlsx', response['Content-Disposition'])
        workbook = load_workbook(BytesIO(response.content))
        self.assertEqual(workbook['План'].cell(row=14, column=2).value, 135)


class DistributionViewTestCase(TestCase):
    def setUp(self):
        head_group, _ = AuthGroup.objects.get_or_create(name='head')
        self.user = User.objects.create_user(username='department_head', password='pass')
        self.user.groups.add(head_group)
        self.department = Department.objects.create(name='Кафедра распределения')
        Teacher.objects.create(
            user=self.user,
            full_name='Заведующий кафедрой',
            department=self.department
        )
        self.target_user = User.objects.create_user(username='target_teacher', password='pass')
        self.target_teacher = Teacher.objects.create(
            user=self.target_user,
            full_name='Преподаватель кафедры',
            department=self.department
        )
        specialty = Specialty.objects.create(code='99.02.01', name='Тест', abbr='Т')
        curriculum = Curriculum.objects.create(
            specialty=specialty,
            admission_year=2025,
            study_form='full_time'
        )
        self.discipline = Discipline.objects.create(
            curriculum=curriculum,
            department=self.department,
            name='Дисциплина кафедры',
            semester=1
        )
        self.load_item = LoadItem.objects.create(
            discipline=self.discipline,
            kind='pract',
            hours=Decimal('12')
        )
        self.client.force_login(self.user)

    def test_head_cannot_assign_load_from_another_department(self):
        other_department = Department.objects.create(name='Чужая кафедра')
        other_discipline = Discipline.objects.create(
            curriculum=self.discipline.curriculum,
            department=other_department,
            name='Чужая дисциплина',
            semester=1
        )
        foreign_item = LoadItem.objects.create(
            discipline=other_discipline,
            kind='pract',
            hours=Decimal('8')
        )

        response = self.client.post('/distribution/', {
            'load_item_id': foreign_item.pk,
            'teacher_id': self.target_teacher.pk,
        })

        self.assertEqual(response.status_code, 403)
        foreign_item.refresh_from_db()
        self.assertIsNone(foreign_item.teacher)

    def test_head_cannot_assign_teacher_from_another_department(self):
        other_department = Department.objects.create(name='Другая кафедра')
        other_user = User.objects.create_user(username='foreign_teacher', password='pass')
        foreign_teacher = Teacher.objects.create(
            user=other_user,
            full_name='Чужой преподаватель',
            department=other_department
        )

        response = self.client.post('/distribution/', {
            'load_item_id': self.load_item.pk,
            'teacher_id': foreign_teacher.pk,
        })

        self.assertEqual(response.status_code, 403)
        self.load_item.refresh_from_db()
        self.assertIsNone(self.load_item.teacher)

    def test_htmx_assignment_returns_updated_teacher_panel(self):
        LoadItem.objects.create(
            discipline=self.discipline,
            kind='lec',
            hours=Decimal('5'),
            teacher=self.target_teacher
        )

        response = self.client.post(
            '/distribution/',
            {'load_item_id': self.load_item.pk, 'teacher_id': self.target_teacher.pk},
            HTTP_HX_REQUEST='true'
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'core/_teacher_panel.html')
        self.assertContains(response, '17')
        self.assertNotIn('_messages', self.client.session)

    def test_distribution_groups_by_specialty_and_marks_specialization(self):
        # Добавляем специализацию целевому преподавателю
        self.target_teacher.specialization.add(self.discipline)

        # Создаем вторую специальность и дисциплину на той же кафедре
        spec2 = Specialty.objects.create(code='88.01.01', name='Вторая Спец', abbr='ВС')
        cur2 = Curriculum.objects.create(specialty=spec2, admission_year=2025, study_form='full_time')
        disc2 = Discipline.objects.create(curriculum=cur2, department=self.department, name='Вторая дисциплина', semester=2)
        LoadItem.objects.create(discipline=disc2, kind='lec', hours=Decimal('20'))

        response = self.client.get('/distribution/')
        self.assertEqual(response.status_code, 200)

        # Проверяем наличие группировки по обеим специальностям
        self.assertContains(response, '99.02.01 — Тест (Т)')
        self.assertContains(response, '88.01.01 — Вторая Спец (ВС)')

        # Проверяем наличие галочки специализации для подходящей дисциплины
        self.assertContains(response, f'✓ {self.target_teacher.full_name}')


class TeachersPageTestCase(TestCase):
    """Страница «Преподаватели» зав. кафедрой: права и CRUD."""

    def setUp(self):
        self.teacher_group, _ = AuthGroup.objects.get_or_create(name='teacher')
        self.head_group, _ = AuthGroup.objects.get_or_create(name='head')
        self.dean_group, _ = AuthGroup.objects.get_or_create(name='deputy_dean')
        self.department = Department.objects.create(name='Кафедра А')
        self.other_department = Department.objects.create(name='Кафедра Б')
        self.position = PositionRule.objects.create(
            position='Доцент', min_hours=840, max_hours=900, min_lecture_hours=100, can_lecture=True
        )

        self.head_user = User.objects.create_user(username='t2_head', password='pass')
        self.head_user.groups.add(self.head_group)
        self.head = Teacher.objects.create(
            user=self.head_user, full_name='Зав А', department=self.department, position=self.position
        )
        self.department.head = self.head
        self.department.save()

        self.own_user = User.objects.create_user(username='t2_own', password='pass')
        self.own_user.groups.add(self.teacher_group)
        self.own = Teacher.objects.create(
            user=self.own_user, full_name='Свой', department=self.department, position=self.position
        )
        self.foreign_user = User.objects.create_user(username='t2_foreign', password='pass')
        self.foreign_user.groups.add(self.teacher_group)
        self.foreign = Teacher.objects.create(
            user=self.foreign_user, full_name='Чужой', department=self.other_department, position=self.position
        )
        self.url = '/teachers/'

    def payload(self, **extra):
        data = {
            'action': 'save', 'full_name': 'Новый Преподаватель', 'position': self.position.pk,
            'degree': 'none', 'rank': '', 'rate': '1.00',
            'username': 't2_new', 'password': 'Str0ng-pass-91',
        }
        data.update(extra)
        return data

    def test_only_head_has_access(self):
        self.client.login(username='t2_own', password='pass')
        self.assertEqual(self.client.get(self.url).status_code, 403)
        dean = User.objects.create_user(username='t2_dean', password='pass')
        dean.groups.add(self.dean_group)
        self.client.login(username='t2_dean', password='pass')
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_list_shows_only_own_department(self):
        self.client.login(username='t2_head', password='pass')
        response = self.client.get(self.url)
        self.assertContains(response, 'Свой')
        self.assertNotContains(response, 'Чужой')

    def test_create_makes_user_group_and_teacher_in_own_department(self):
        self.client.login(username='t2_head', password='pass')
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 302)
        teacher = Teacher.objects.get(full_name='Новый Преподаватель')
        self.assertEqual(teacher.department, self.department)
        self.assertTrue(teacher.user.groups.filter(name='teacher').exists())
        self.assertTrue(teacher.user.check_password('Str0ng-pass-91'))

    def test_create_requires_unique_login_and_password(self):
        self.client.login(username='t2_head', password='pass')
        self.client.post(self.url, self.payload(username='t2_own'))
        self.assertFalse(Teacher.objects.filter(full_name='Новый Преподаватель').exists())
        self.client.post(self.url, self.payload(password=''))
        self.assertFalse(Teacher.objects.filter(full_name='Новый Преподаватель').exists())

    def test_edit_updates_fields_and_keeps_password_when_blank(self):
        self.client.login(username='t2_head', password='pass')
        self.client.post(self.url, self.payload(
            teacher_id=self.own.pk, full_name='Свой Изменённый', rate='0.50', password='', username='ignored'
        ))
        self.own.refresh_from_db()
        self.own_user.refresh_from_db()
        self.assertEqual(self.own.full_name, 'Свой Изменённый')
        self.assertEqual(self.own.rate, Decimal('0.50'))
        self.assertEqual(self.own_user.username, 't2_own')
        self.assertTrue(self.own_user.check_password('pass'))

    def test_cannot_edit_or_delete_foreign_teacher(self):
        self.client.login(username='t2_head', password='pass')
        self.assertEqual(self.client.get(self.url, {'edit': self.foreign.pk}).status_code, 404)
        self.assertEqual(self.client.post(self.url, self.payload(teacher_id=self.foreign.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url, {'action': 'delete', 'teacher_id': self.foreign.pk}).status_code, 404)
        self.assertTrue(Teacher.objects.filter(pk=self.foreign.pk).exists())

    def test_delete_removes_teacher_and_user_but_not_head(self):
        self.client.login(username='t2_head', password='pass')
        self.client.post(self.url, {'action': 'delete', 'teacher_id': self.own.pk})
        self.assertFalse(Teacher.objects.filter(pk=self.own.pk).exists())
        self.assertFalse(User.objects.filter(username='t2_own').exists())
        self.client.post(self.url, {'action': 'delete', 'teacher_id': self.head.pk})
        self.assertTrue(Teacher.objects.filter(pk=self.head.pk).exists())

    def test_delete_via_get_is_not_performed(self):
        self.client.login(username='t2_head', password='pass')
        self.client.get(self.url, {'action': 'delete', 'teacher_id': self.own.pk})
        self.assertTrue(Teacher.objects.filter(pk=self.own.pk).exists())


class DepartmentRecalculateViewTestCase(TestCase):
    """Тесты пересчёта нагрузки: зав. кафедрой пересчитывает только свою кафедру."""

    def setUp(self):
        self.settings = CalcSettings.get()
        self.group_head, _ = AuthGroup.objects.get_or_create(name='head')
        self.group_deputy, _ = AuthGroup.objects.get_or_create(name='deputy_dean')

        self.dept_a = Department.objects.create(name='Кафедра А')
        self.dept_b = Department.objects.create(name='Кафедра Б')

        self.spec = Specialty.objects.create(code='01.01.01', name='Спец', abbr='СП')
        self.curriculum = Curriculum.objects.create(specialty=self.spec, admission_year=2026, study_form='full_time')
        self.group = Group.objects.create(name='Гр1', specialty=self.spec, course=1, students_count=20, subgroups_count=2)

        self.disc_a = Discipline.objects.create(
            curriculum=self.curriculum, department=self.dept_a, name='Дисциплина А',
            semester=1, control_form='exam', hours_lec=Decimal('30')
        )
        self.disc_a.groups.add(self.group)

        self.disc_b = Discipline.objects.create(
            curriculum=self.curriculum, department=self.dept_b, name='Дисциплина Б',
            semester=1, control_form='exam', hours_lec=Decimal('40')
        )
        self.disc_b.groups.add(self.group)

        self.user_head_a = User.objects.create_user('head_a', password='pass')
        self.user_head_a.groups.add(self.group_head)
        self.teacher_head_a = Teacher.objects.create(
            user=self.user_head_a, full_name='Зав А', department=self.dept_a, rate=Decimal('1.0')
        )

        self.user_deputy = User.objects.create_user('deputy', password='pass')
        self.user_deputy.groups.add(self.group_deputy)

    def test_head_recalculates_only_own_department_in_settings(self):
        self.client.login(username='head_a', password='pass')
        # Запрос пересчёта (даже если отправлено recalculate_all)
        response = self.client.post('/settings/', {'action': 'recalculate_all'})
        self.assertEqual(response.status_code, 302)
        # Нагрузка кафедры А создана
        self.assertTrue(LoadItem.objects.filter(discipline__department=self.dept_a).exists())
        # Нагрузка кафедры Б НЕ создана
        self.assertFalse(LoadItem.objects.filter(discipline__department=self.dept_b).exists())

    def test_head_recalculates_on_distribution_page(self):
        self.client.login(username='head_a', password='pass')
        response = self.client.post('/distribution/', {'action': 'recalculate'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(LoadItem.objects.filter(discipline__department=self.dept_a).exists())
        self.assertFalse(LoadItem.objects.filter(discipline__department=self.dept_b).exists())

    def test_deputy_dean_can_recalculate_specific_department(self):
        self.client.login(username='deputy', password='pass')
        response = self.client.post('/settings/', {'action': 'recalculate_department', 'department_id': self.dept_b.id})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LoadItem.objects.filter(discipline__department=self.dept_a).exists())
        self.assertTrue(LoadItem.objects.filter(discipline__department=self.dept_b).exists())


class JournalValidationTestCase(TestCase):
    """Тесты валидации записей журнала (задача T6)."""

    def setUp(self):
        self.group_teacher, _ = AuthGroup.objects.get_or_create(name='teacher')
        self.dept = Department.objects.create(name='Кафедра Журнала')
        self.spec = Specialty.objects.create(code='02.02.02', name='СпецЖурнал', abbr='СЖ')
        self.curriculum = Curriculum.objects.create(specialty=self.spec, admission_year=2026, study_form='full_time')
        self.group = Group.objects.create(name='Гр-Ж1', specialty=self.spec, course=1, students_count=20, subgroups_count=2)
        self.discipline = Discipline.objects.create(
            curriculum=self.curriculum, department=self.dept, name='Дисциплина Журнала',
            semester=1, control_form='exam', hours_lec=Decimal('30')
        )
        self.discipline.groups.add(self.group)

        self.user = User.objects.create_user('teacher_j', password='pass')
        self.user.groups.add(self.group_teacher)
        self.teacher = Teacher.objects.create(
            user=self.user, full_name='Преподаватель Журнала', department=self.dept, rate=Decimal('1.0')
        )
        self.load_item = LoadItem.objects.create(
            discipline=self.discipline, kind='lab', group=self.group, subgroup_no=1,
            hours=Decimal('20'), teacher=self.teacher
        )

    def test_journal_rejects_negative_or_zero_hours(self):
        self.client.login(username='teacher_j', password='pass')
        payload = {
            'action': 'add_entry',
            'load_item_id': self.load_item.id,
            'date': '2026-10-15',
            'topic': 'Тема 1',
        }
        # Ноль часов
        resp = self.client.post('/journal/', {**payload, 'hours': '0'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(JournalEntry.objects.filter(load_item=self.load_item).exists())

        # Отрицательные часы
        resp = self.client.post('/journal/', {**payload, 'hours': '-2.5'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(JournalEntry.objects.filter(load_item=self.load_item).exists())

    def test_journal_rejects_invalid_date(self):
        self.client.login(username='teacher_j', password='pass')
        resp = self.client.post('/journal/', {
            'action': 'add_entry',
            'load_item_id': self.load_item.id,
            'date': 'not-a-date',
            'hours': '2',
            'topic': 'Тема 2',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(JournalEntry.objects.filter(load_item=self.load_item).exists())

    def test_journal_page_renders_group_column(self):
        self.client.login(username='teacher_j', password='pass')
        JournalEntry.objects.create(
            teacher=self.teacher, load_item=self.load_item, date=date(2026, 10, 15),
            hours=Decimal('2'), topic='Тестовое занятие'
        )
        resp = self.client.get('/journal/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Группа / подгруппа')
        self.assertContains(resp, 'Гр-Ж1, п/г 1')


class DeputyDeanAdminAccessTestCase(TestCase):
    """Тесты доступа зам. декана к справочникам в админке (задача T3 Вариант A)."""

    def setUp(self):
        from django.contrib.contenttypes.models import ContentType
        from django.contrib.auth.models import Permission

        self.group_deputy, _ = AuthGroup.objects.get_or_create(name='deputy_dean')
        ref_models = [Specialty, Group, Curriculum, Discipline, Department]
        for model in ref_models:
            ct = ContentType.objects.get_for_model(model)
            perms = Permission.objects.filter(content_type=ct)
            self.group_deputy.permissions.add(*perms)

        self.user_deputy = User.objects.create_user('deputy_test', password='pass', is_staff=True)
        self.user_deputy.groups.add(self.group_deputy)

    def test_deputy_dean_has_access_to_directory_admins(self):
        self.client.login(username='deputy_test', password='pass')
        urls = [
            '/admin/core/specialty/',
            '/admin/core/group/',
            '/admin/core/curriculum/',
            '/admin/core/discipline/',
            '/admin/core/department/',
        ]
        for url in urls:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, f'Ожидался доступ 200 к {url}')


class WorkloadPageTestCase(TestCase):
    """T5: страница нагрузки преподавателя с матрицей и итогами по семестрам."""

    def setUp(self):
        self.teacher_group, _ = AuthGroup.objects.get_or_create(name='teacher')
        self.head_group, _ = AuthGroup.objects.get_or_create(name='head')
        self.specialty = Specialty.objects.create(code='05.01.01', name='ТестСпец', abbr='ТС')
        self.curriculum = Curriculum.objects.create(
            specialty=self.specialty, admission_year=2026, study_form='full_time'
        )
        self.department = Department.objects.create(name='Кафедра Т5')
        self.position = PositionRule.objects.create(
            position='Доцент', min_hours=840, max_hours=900, min_lecture_hours=100, can_lecture=True
        )
        self.group1 = Group.objects.create(
            name='Гр-Т5', specialty=self.specialty, course=1, students_count=20, subgroups_count=2
        )

        self.user = User.objects.create_user(username='t5_teacher', password='pass')
        self.user.groups.add(self.teacher_group)
        self.teacher = Teacher.objects.create(
            user=self.user, full_name='Тест Т5', department=self.department, position=self.position
        )

        # Дисциплина осеннего семестра (нечётный)
        self.disc_fall = Discipline.objects.create(
            curriculum=self.curriculum, department=self.department,
            name='Дисц Осень', semester=1, hours_lec=30, control_form='exam'
        )
        self.disc_fall.groups.add(self.group1)
        # Дисциплина весеннего семестра (чётный)
        self.disc_spring = Discipline.objects.create(
            curriculum=self.curriculum, department=self.department,
            name='Дисц Весна', semester=2, hours_pract=20, control_form='credit'
        )
        self.disc_spring.groups.add(self.group1)

        # Создаём нагрузку вручную
        self.item_lec = LoadItem.objects.create(
            discipline=self.disc_fall, kind='lec', hours=Decimal('30'), teacher=self.teacher
        )
        self.item_pract = LoadItem.objects.create(
            discipline=self.disc_spring, kind='pract', group=self.group1,
            hours=Decimal('20'), teacher=self.teacher
        )

    def test_workload_view_returns_200(self):
        self.client.login(username='t5_teacher', password='pass')
        response = self.client.get('/workload/')
        self.assertEqual(response.status_code, 200)

    def test_workload_view_forbidden_for_non_teacher(self):
        head_user = User.objects.create_user(username='t5_head', password='pass')
        head_user.groups.add(self.head_group)
        self.client.login(username='t5_head', password='pass')
        response = self.client.get('/workload/')
        self.assertEqual(response.status_code, 403)

    def test_workload_contains_semester_totals(self):
        self.client.login(username='t5_teacher', password='pass')
        response = self.client.get('/workload/')
        self.assertContains(response, 'осенний семестр')
        self.assertContains(response, 'весенний семестр')
        self.assertContains(response, 'ИТОГО ЗА ГОД')

    def test_teacher_workload_function_totals(self):
        """teacher_workload: grand_totals_total совпадает с teacher_total."""
        wl = teacher_workload(self.teacher)
        total_from_wl = wl['grand_totals_total']
        total_from_fn = teacher_total(self.teacher)
        self.assertEqual(total_from_wl, total_from_fn)

    def test_teacher_workload_fall_spring_split(self):
        """teacher_workload: осенний итог = 30 ч, весенний = 20 ч."""
        wl = teacher_workload(self.teacher)
        self.assertEqual(wl['fall_totals_total'], Decimal('30'))
        self.assertEqual(wl['spring_totals_total'], Decimal('20'))
        self.assertEqual(wl['grand_totals_total'], Decimal('50'))

    def test_teacher_workload_rows_count(self):
        """teacher_workload: по одной строке на дисциплину."""
        wl = teacher_workload(self.teacher)
        self.assertEqual(len(wl['rows']), 2)

    def test_workload_contains_discipline_names(self):
        self.client.login(username='t5_teacher', password='pass')
        response = self.client.get('/workload/')
        self.assertContains(response, 'Дисц Осень')
        self.assertContains(response, 'Дисц Весна')
