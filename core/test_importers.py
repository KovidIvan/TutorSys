from datetime import date
from io import BytesIO

from django.contrib.auth.models import Group as AuthGroup, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import Workbook

from .calculations import teacher_workload
from .models import (
    Curriculum,
    Department,
    Discipline,
    Group,
    JournalEntry,
    LoadItem,
    Specialty,
    Teacher,
)


class WorkloadImportViewTestCase(TestCase):
    headers = [
        'Дисциплины (виды работ)', 'Спец-ть', 'Курс', 'Семестр(1,2)', 'Семестр',
        'Форма обучения', 'Студентов', 'Потоков', 'Групп', 'Подгрупп',
        'Лекции\n(по плану)', 'Лекции\n(всего)', 'Практические\n(по плану)',
        'Практические\n(всего)', 'Лабораторные\n(по плану)', 'Лабораторные\n(всего)',
        'Кол-во КР', 'УСР', 'КУСР', 'КР', 'Конс.', 'Экз. конс.', 'Экзамены', 'Зачеты',
        'Всего', 'Экзамен', 'Зачёт', 'Коэфф. модуля', 'Консультация',
    ]

    def setUp(self):
        deputy_group, _ = AuthGroup.objects.get_or_create(name='deputy_dean')
        self.user = User.objects.create_user(username='deputy_import', password='pass')
        self.user.groups.add(deputy_group)
        self.department = Department.objects.create(name='Кафедра импорта')

    def make_workbook(self, invalid_semester=False):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = 'Нагрузка'
        worksheet.append(self.headers)
        worksheet.append([
            'Алгоритмы', 'ПМ', 1, 1, 1, 'д', 40, 1, 2, 4,
            30, 30, 12, 24, 8, 32, 1, 5, 2, 6, None, 2, 9, None,
            100, 'у', None, None, None,
        ])
        worksheet.append([
            'Введение в курс', 'ПМ', 1, 0, 0 if not invalid_semester else 4,
            'д', 40, 1, 2, 4, 6, 6, None, None, None, None, None, None,
            None, None, None, None, None, None, 6, None, None, None, None,
        ])
        content = BytesIO()
        workbook.save(content)
        return SimpleUploadedFile(
            'workload.xlsx', content.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    def import_file(self, upload):
        self.client.force_login(self.user)
        return self.client.post('/data-import/', {
            'workload_file': upload,
            'department_id': self.department.pk,
            'academic_year': '2026',
        })

    def test_import_creates_base_data_and_reimport_updates_without_duplicates(self):
        response = self.import_file(self.make_workbook())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Specialty.objects.count(), 1)
        self.assertEqual(Curriculum.objects.count(), 1)
        self.assertEqual(Group.objects.count(), 2)
        self.assertEqual(set(Group.objects.values_list('name', flat=True)), {'26ПМ1д', '26ПМ2д'})
        self.assertEqual(Discipline.objects.count(), 2)
        self.assertEqual(LoadItem.objects.filter(discipline__department=self.department).count(), 14)
        self.assertTrue(Discipline.objects.get(name='Алгоритмы').groups.filter(students_count=20).exists())
        self.assertEqual(Discipline.objects.get(name='Введение в курс').semester, 1)
        self.assertEqual(
            Discipline.objects.get(name='Введение в курс').control_form,
            'none',
        )

        legacy_groups = list(Group.objects.order_by('name'))
        duplicate_specialty = Specialty.objects.create(
            code='legacy-specialty', name='Старая специальность', abbr='ПМ'
        )
        specialty_count = Specialty.objects.count()
        canonical_groups = []
        for number, group in enumerate(legacy_groups, start=1):
            group.name = f'ПМ-3ffde9-2026-1-д-{number}'
            if number == 1:
                group.specialty = duplicate_specialty
                group.save(update_fields=['name', 'specialty'])
            else:
                group.save(update_fields=['name'])
            if number == 1:
                canonical_groups.append(Group.objects.create(
                    name=f'26ПМ{number}д',
                    specialty=group.specialty,
                    course=group.course,
                    study_form=group.study_form,
                    students_count=group.students_count,
                    subgroups_count=group.subgroups_count,
                ))
            else:
                group.name = f'26ПМ{number}д'
                group.save(update_fields=['name'])
                canonical_groups.append(group)

        teacher_user = User.objects.create_user(username='assigned_teacher', password='pass')
        teacher = Teacher.objects.create(
            user=teacher_user, full_name='Преподаватель', department=self.department
        )
        legacy_item = LoadItem.objects.filter(group=legacy_groups[0], kind='exam').first()
        legacy_item.teacher = teacher
        legacy_item.save(update_fields=['teacher'])
        journal_entry = JournalEntry.objects.create(
            teacher=teacher,
            load_item=legacy_item,
            date=date(2026, 1, 1),
            hours=1,
            topic='Сохраненная запись',
        )

        second_response = self.import_file(self.make_workbook())

        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(set(Group.objects.values_list('pk', flat=True)),
                         {group.pk for group in canonical_groups})
        self.assertEqual(set(Group.objects.values_list('name', flat=True)), {'26ПМ1д', '26ПМ2д'})
        self.assertEqual(
            JournalEntry.objects.get(pk=journal_entry.pk).load_item.group_id,
            canonical_groups[0].pk,
        )
        self.assertEqual(
            LoadItem.objects.get(discipline__name='Алгоритмы', kind='exam', group=canonical_groups[0]).teacher,
            teacher,
        )
        workload_groups = [row['groups'] for row in teacher_workload(teacher)['rows']]
        self.assertIn('26ПМ1д', workload_groups)
        self.assertTrue(all('3ffde9' not in groups for groups in workload_groups))
        self.assertEqual(Specialty.objects.count(), specialty_count)
        self.assertEqual(Curriculum.objects.count(), 1)
        self.assertEqual(Group.objects.count(), 2)
        self.assertEqual(Discipline.objects.count(), 2)
        self.assertEqual(LoadItem.objects.filter(discipline__department=self.department).count(), 14)

    def test_import_can_create_department_without_manual_database_setup(self):
        self.client.force_login(self.user)

        response = self.client.post('/data-import/', {
            'workload_file': self.make_workbook(),
            'department_id': '',
            'new_department_name': 'Новая кафедра из файла',
            'academic_year': '2026',
        })

        self.assertEqual(response.status_code, 302)
        department = Department.objects.get(name='Новая кафедра из файла')
        self.assertEqual(Discipline.objects.filter(department=department).count(), 2)
        self.assertTrue(LoadItem.objects.filter(discipline__department=department).exists())

    def test_invalid_workbook_does_not_create_any_data(self):
        response = self.import_file(self.make_workbook(invalid_semester=True))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'не соответствует курсу')
        self.assertFalse(Specialty.objects.exists())
        self.assertFalse(Curriculum.objects.exists())
        self.assertFalse(Group.objects.exists())
        self.assertFalse(Discipline.objects.exists())

    def test_teacher_cannot_import_workload_data(self):
        teacher_group, _ = AuthGroup.objects.get_or_create(name='teacher')
        teacher = User.objects.create_user(username='teacher_import', password='pass')
        teacher.groups.add(teacher_group)
        self.client.force_login(teacher)

        response = self.client.post('/data-import/', {
            'workload_file': self.make_workbook(),
            'department_id': self.department.pk,
            'academic_year': '2026',
        })

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Specialty.objects.exists())

    def test_import_page_is_available_to_deputy_dean(self):
        self.client.force_login(self.user)

        response = self.client.get('/data-import/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Импорт учебных данных из Excel')

    def test_import_page_is_available_to_admin_without_demo_role(self):
        admin = User.objects.create_user(username='import_admin', password='pass', is_staff=True)
        self.client.force_login(admin)

        response = self.client.get('/data-import/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Импорт учебных данных из Excel')
