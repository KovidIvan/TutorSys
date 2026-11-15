"""
Демо-данные факультета математики и информационных технологий ВГУ имени П.М. Машерова.

Источник: официальный сайт vsu.by (факультет ФМИТ).
Использование:
    python manage.py shell
    from core.seed import run
    run()
"""

from django.contrib.auth.models import User, Group as UserGroup
from decimal import Decimal
from .models import (
    Specialty, Department, Group, Curriculum, Discipline,
    CalcSettings, PositionRule, Teacher
)


def run():
    """Загрузить демо-данные факультета ФМИТ ВГУ."""

    print("Очищаю базу данных (кроме пользователей)...")
    # Очистить в правильном порядке (чтобы не было конфликтов ForeignKey)
    Discipline.objects.all().delete()
    Curriculum.objects.all().delete()
    Teacher.objects.all().delete()
    Department.objects.all().delete()
    Group.objects.all().delete()
    Specialty.objects.all().delete()
    PositionRule.objects.all().delete()

    print("✓ База очищена\n")

    # ============ СПЕЦИАЛЬНОСТИ ============
    print("📚 Создаю специальности...")
    spec_pi = Specialty.objects.create(
        code="6-05-0533-11",
        name="Прикладная информатика",
        abbr="ПИ"
    )
    spec_pm = Specialty.objects.create(
        code="6-05-0533-09",
        name="Прикладная математика",
        abbr="ПМ"
    )
    print(f"  ✓ {spec_pi}")
    print(f"  ✓ {spec_pm}\n")

    # ============ КАФЕДРЫ ============
    print("🏛️  Создаю кафедры...")
    dept_math = Department.objects.create(
        name="Кафедра математики"
    )
    dept_itub = Department.objects.create(
        name="Кафедра информационных технологий и управления бизнесом"
    )
    dept_pisp = Department.objects.create(
        name="Кафедра прикладного и системного программирования"
    )
    print(f"  ✓ {dept_math}")
    print(f"  ✓ {dept_itub}")
    print(f"  ✓ {dept_pisp}\n")

    # ============ ГРУППЫ ============
    print("👥 Создаю студенческие группы...")
    group_pi1 = Group.objects.create(
        name="26ПИ1д",
        specialty=spec_pi,
        course=1,
        study_form="full_time",
        students_count=15,
        subgroups_count=2
    )
    group_pi2 = Group.objects.create(
        name="26ПИ2д",
        specialty=spec_pi,
        course=1,
        study_form="full_time",
        students_count=15,
        subgroups_count=2
    )
    group_pm1 = Group.objects.create(
        name="26ПМ1д",
        specialty=spec_pm,
        course=1,
        study_form="full_time",
        students_count=24,
        subgroups_count=2
    )
    print(f"  ✓ {group_pi1} ({group_pi1.students_count} студентов)")
    print(f"  ✓ {group_pi2} ({group_pi2.students_count} студентов)")
    print(f"  ✓ {group_pm1} ({group_pm1.students_count} студентов)\n")

    # ============ УЧЕБНЫЕ ПЛАНЫ ============
    print("📋 Создаю учебные планы...")
    cur_pi_2026 = Curriculum.objects.create(
        specialty=spec_pi,
        admission_year=2026,
        study_form="full_time"
    )
    cur_pm_2026 = Curriculum.objects.create(
        specialty=spec_pm,
        admission_year=2026,
        study_form="full_time"
    )
    print(f"  ✓ {cur_pi_2026}")
    print(f"  ✓ {cur_pm_2026}\n")

    # ============ ДИСЦИПЛИНЫ ============
    print("📖 Создаю дисциплины...")

    disc_1 = Discipline.objects.create(
        curriculum=cur_pi_2026,
        department=dept_pisp,
        name="Основы и методологии программирования",
        semester=1,
        control_form="exam",
        hours_lec=Decimal('30'),
        hours_pract=Decimal('0'),
        hours_lab=Decimal('40'),
        hours_usr=Decimal('0'),
        control_works=0,
        credits=Decimal('5')
    )
    disc_1.groups.set([group_pi1, group_pi2, group_pm1])

    disc_2 = Discipline.objects.create(
        curriculum=cur_pi_2026,
        department=dept_math,
        name="Математический анализ",
        semester=1,
        control_form="exam",
        hours_lec=Decimal('40'),
        hours_pract=Decimal('30'),
        hours_lab=Decimal('0'),
        hours_usr=Decimal('0'),
        control_works=0,
        credits=Decimal('4')
    )
    disc_2.groups.set([group_pi1, group_pi2, group_pm1])

    disc_3 = Discipline.objects.create(
        curriculum=cur_pi_2026,
        department=dept_math,
        name="Аналитическая геометрия",
        semester=1,
        control_form="exam",
        hours_lec=Decimal('20'),
        hours_pract=Decimal('20'),
        hours_lab=Decimal('0'),
        hours_usr=Decimal('0'),
        control_works=0,
        credits=Decimal('3')
    )
    disc_3.groups.set([group_pi1, group_pi2, group_pm1])

    disc_4 = Discipline.objects.create(
        curriculum=cur_pi_2026,
        department=dept_math,
        name="Алгебра и теория чисел",
        semester=1,
        control_form="credit",
        hours_lec=Decimal('20'),
        hours_pract=Decimal('20'),
        hours_lab=Decimal('0'),
        hours_usr=Decimal('0'),
        control_works=0,
        credits=Decimal('3')
    )
    disc_4.groups.set([group_pi1, group_pi2])

    disc_5 = Discipline.objects.create(
        curriculum=cur_pi_2026,
        department=dept_math,
        name="Дискретная математика и математическая логика",
        semester=2,
        control_form="exam",
        hours_lec=Decimal('30'),
        hours_pract=Decimal('20'),
        hours_lab=Decimal('0'),
        hours_usr=Decimal('0'),
        control_works=0,
        credits=Decimal('4')
    )
    disc_5.groups.set([group_pi1, group_pi2, group_pm1])

    disc_6 = Discipline.objects.create(
        curriculum=cur_pi_2026,
        department=dept_pisp,
        name="Алгоритмы и структуры данных",
        semester=2,
        control_form="exam",
        hours_lec=Decimal('30'),
        hours_pract=Decimal('10'),
        hours_lab=Decimal('30'),
        hours_usr=Decimal('0'),
        control_works=0,
        credits=Decimal('4')
    )
    disc_6.groups.set([group_pi1, group_pi2, group_pm1])

    disc_7 = Discipline.objects.create(
        curriculum=cur_pi_2026,
        department=dept_itub,
        name="Компьютерные сети",
        semester=2,
        control_form="credit",
        hours_lec=Decimal('20'),
        hours_pract=Decimal('0'),
        hours_lab=Decimal('30'),
        hours_usr=Decimal('0'),
        control_works=0,
        credits=Decimal('3')
    )
    disc_7.groups.set([group_pi1, group_pi2])

    print(f"  ✓ {disc_1.name}")
    print(f"  ✓ {disc_2.name}")
    print(f"  ✓ {disc_3.name}")
    print(f"  ✓ {disc_4.name}")
    print(f"  ✓ {disc_5.name}")
    print(f"  ✓ {disc_6.name}")
    print(f"  ✓ {disc_7.name}\n")

    # ============ ДОЛЖНОСТИ ============
    print("👔 Создаю должности...")
    pos_prof = PositionRule.objects.create(
        position="Профессор",
        min_hours=900,
        max_hours=1000,
        min_lecture_hours=300,
        can_lecture=True
    )
    pos_docent = PositionRule.objects.create(
        position="Доцент",
        min_hours=840,
        max_hours=900,
        min_lecture_hours=200,
        can_lecture=True
    )
    pos_st_prep = PositionRule.objects.create(
        position="Старший преподаватель",
        min_hours=840,
        max_hours=900,
        min_lecture_hours=150,
        can_lecture=True
    )
    print(f"  ✓ {pos_prof}")
    print(f"  ✓ {pos_docent}")
    print(f"  ✓ {pos_st_prep}\n")

    # ============ ПРЕПОДАВАТЕЛИ ============
    print("👨‍🏫 Создаю преподавателей...\n")

    # Преподавателей нужно привязать к User
    # Сначала убедиться, что пользователи существуют
    user_pavlysh, _ = User.objects.get_or_create(
        username='pavlysh',
        defaults={'first_name': 'Эдуард', 'last_name': 'Павлыш'}
    )
    user_bulgakova, _ = User.objects.get_or_create(
        username='bulgakova',
        defaults={'first_name': 'Наталья', 'last_name': 'Булгакова'}
    )
    user_prohozhiy, _ = User.objects.get_or_create(
        username='prohozhiy',
        defaults={'first_name': 'Сергей', 'last_name': 'Прохожий'}
    )
    user_vorobyev, _ = User.objects.get_or_create(
        username='vorobyev',
        defaults={'first_name': 'Николай', 'last_name': 'Воробьев'}
    )
    user_podoksyonov, _ = User.objects.get_or_create(
        username='podoksyonov',
        defaults={'first_name': 'Михаил', 'last_name': 'Подоксёнов'}
    )
    user_karaulova, _ = User.objects.get_or_create(
        username='karaulova',
        defaults={'first_name': 'Татьяна', 'last_name': 'Караулова'}
    )
    user_korchevskaya, _ = User.objects.get_or_create(
        username='korchevskaya',
        defaults={'first_name': 'Елена', 'last_name': 'Корчевская'}
    )

    # Создавать преподавателей
    teacher_1 = Teacher.objects.create(
        user=user_pavlysh,
        full_name="Павлыш Эдуард Владимирович",
        department=dept_itub,
        position=pos_docent,
        degree="candidate",
        rank="доцент",
        rate=Decimal('1.0')
    )
    teacher_1.specialization.set([disc_7])

    teacher_2 = Teacher.objects.create(
        user=user_bulgakova,
        full_name="Булгакова Наталья Валентиновна",
        department=dept_itub,
        position=pos_st_prep,
        degree="none",
        rank="",
        rate=Decimal('0.75')
    )
    teacher_2.specialization.set([disc_7])

    teacher_3 = Teacher.objects.create(
        user=user_prohozhiy,
        full_name="Прохожий Сергей Александрович",
        department=dept_itub,
        position=pos_docent,
        degree="candidate",
        rank="доцент",
        rate=Decimal('1.0')
    )
    teacher_3.specialization.set([disc_7])

    teacher_4 = Teacher.objects.create(
        user=user_vorobyev,
        full_name="Воробьев Николай Тимофеевич",
        department=dept_math,
        position=pos_prof,
        degree="doctor",
        rank="профессор",
        rate=Decimal('1.0')
    )
    teacher_4.specialization.set([disc_4, disc_5])

    teacher_5 = Teacher.objects.create(
        user=user_podoksyonov,
        full_name="Подоксёнов Михаил Николаевич",
        department=dept_math,
        position=pos_docent,
        degree="candidate",
        rank="доцент",
        rate=Decimal('1.0')
    )
    teacher_5.specialization.set([disc_2, disc_3])

    teacher_6 = Teacher.objects.create(
        user=user_karaulova,
        full_name="Караулова Татьяна Борисовна",
        department=dept_math,
        position=pos_docent,
        degree="candidate",
        rank="доцент",
        rate=Decimal('1.0')
    )
    teacher_6.specialization.set([disc_2])

    teacher_7 = Teacher.objects.create(
        user=user_korchevskaya,
        full_name="Корчевская Елена Алексеевна",
        department=dept_pisp,
        position=pos_docent,
        degree="none",
        rank="",
        rate=Decimal('1.0')
    )
    teacher_7.specialization.set([disc_1, disc_6])

    print(f"  ✓ {teacher_1.full_name} ({teacher_1.position.position}, {dept_itub.name})")
    print(f"  ✓ {teacher_2.full_name} ({teacher_2.position.position}, {dept_itub.name})")
    print(f"  ✓ {teacher_3.full_name} ({teacher_3.position.position}, {dept_itub.name})")
    print(f"  ✓ {teacher_4.full_name} ({teacher_4.position.position}, {dept_math.name})")
    print(f"  ✓ {teacher_5.full_name} ({teacher_5.position.position}, {dept_math.name})")
    print(f"  ✓ {teacher_6.full_name} ({teacher_6.position.position}, {dept_math.name})")
    print(f"  ✓ {teacher_7.full_name} ({teacher_7.position.position}, {dept_pisp.name})\n")

    # Установить зав. кафедрой
    dept_itub.head = teacher_1
    dept_itub.save()

    dept_math.head = teacher_6
    dept_math.save()

    dept_pisp.head = teacher_7
    dept_pisp.save()

    # ============ ПОЛЬЗОВАТЕЛИ И РОЛИ ============
    print("🔐 Создаю пользователей и роли...\n")

    # Создать группы ролей (если их нет)
    group_deputy_dean, _ = UserGroup.objects.get_or_create(name='deputy_dean')
    group_head, _ = UserGroup.objects.get_or_create(name='head')
    group_teacher, _ = UserGroup.objects.get_or_create(name='teacher')

    # Пользователь 1: Зам. декана
    user_deputy, _ = User.objects.get_or_create(
        username='deputy_dean',
        defaults={
            'first_name': 'Ольга',
            'last_name': 'Декабрева',
            'email': 'dekabr@vsu.by',
        }
    )
    user_deputy.is_staff = False
    user_deputy.set_password('pass123')
    user_deputy.groups.add(group_deputy_dean)
    user_deputy.save()

    # Пользователь 2: Заведующий кафедрой (через преподавателя)
    user_head, _ = User.objects.get_or_create(
        username='head',
        defaults={
            'first_name': 'Сергей',
            'last_name': 'Иванов',
            'email': 'ivanov@vsu.by',
        }
    )
    user_head.is_staff = False
    user_head.set_password('pass123')
    user_head.groups.add(group_head)
    user_head.save()

    # Привязать к преподавателю (Teacher #1 - Павлыш)
    # (Создать дополнительного пользователя для этого)
    teacher_head = Teacher.objects.create(
        user=user_head,
        full_name="Иванов Сергей Сергеевич",
        department=dept_itub,
        position=pos_docent,
        degree="candidate",
        rank="доцент",
        rate=Decimal('1.0')
    )

    # Пользователь 3: Преподаватель
    user_teacher, _ = User.objects.get_or_create(
        username='teacher',
        defaults={
            'first_name': 'Мария',
            'last_name': 'Петрова',
            'email': 'petrova@vsu.by',
        }
    )
    user_teacher.is_staff = False
    user_teacher.set_password('pass123')
    user_teacher.groups.add(group_teacher)
    user_teacher.save()

    demo_teacher = Teacher.objects.create(
        user=user_teacher,
        full_name='Петрова Мария',
        department=dept_itub,
        position=pos_st_prep,
        rate=Decimal('0.75')
    )
    demo_teacher.specialization.set([disc_7])

    print(f"  ✓ Пользователь deputy_dean (Ольга Декабрева)")
    print(f"  ✓ Пользователь head (Сергей Иванов) → преподаватель")
    print(f"  ✓ Пользователь teacher (Мария Петрова) → преподаватель\n")

    # ============ ПАРАМЕТРЫ РАСЧЁТА ============
    print("⚙️  Инициализирую параметры расчёта...")
    settings = CalcSettings.get()
    print(f"  ✓ CalcSettings (exam_hours_per_student: {settings.exam_hours_per_student})\n")

    # ============ ИТОГОВАЯ СТАТИСТИКА ============
    print("=" * 60)
    print("✅ ВСЕ ДЕМО-ДАННЫЕ ЗАГРУЖЕНЫ")
    print("=" * 60)
    print(f"""
📊 ЗАГРУЖЕНО:
  • Специальности: 2 (ПИ, ПМ)
  • Кафедры: 3
  • Группы: 3 (всего {group_pi1.students_count + group_pi2.students_count + group_pm1.students_count} студентов)
  • Учебные планы: 2
  • Дисциплины: 7
  • Должности: 3
  • Преподаватели: 9 (7 основных, head и teacher)
  • Пользователи демо: 3 (deputy_dean, head, teacher)

🔐 УЧЁТНЫЕ ДАННЫЕ:
  deputy_dean / pass123    → Зам. декана (Ольга Декабрева)
  head       / pass123      → Завед. кафедрой (Сергей Иванов)
  teacher    / pass123      → Преподаватель (Мария Петрова)

🌐 ОТКРЫТЬ В БРАУЗЕРЕ:
  http://127.0.0.1:8000/accounts/login/ → Вход в систему
  http://127.0.0.1:8000/settings/       → Параметры (требует логин)
  http://127.0.0.1:8000/distribution/   → Распределение (для head)
  http://127.0.0.1:8000/workload/       → Моя нагрузка (для teacher)

📝 ФАКУЛЬТЕТ: ВГУ имени П.М. Машерова, факультет ФМИТ
""")


def clear_demo_data():
    """
    Удалить все демо-данные (кроме суперпользователя).
    Использование:
        python manage.py shell
        from core.seed import clear_demo_data
        clear_demo_data()
    """
    print("Удаляю демо-данные...")
    Discipline.objects.all().delete()
    Curriculum.objects.all().delete()
    Teacher.objects.all().delete()
    Department.objects.all().delete()
    Group.objects.all().delete()
    Specialty.objects.all().delete()
    print("✓ Данные удалены")
