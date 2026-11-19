"""
Модуль создания демонстрационных данных TutorSys.
Генерирует кафедры, учебные планы, группы, дисциплины, вымышленных преподавателей
и сразу рассчитывает учебную нагрузку для всех кафедр.
"""

from django.contrib.auth.models import User, Group as UserGroup
from decimal import Decimal
from datetime import date
from .models import (
    Specialty, Department, Group, Curriculum, Discipline,
    CalcSettings, PositionRule, Teacher, LoadItem, JournalEntry, Adjustment
)
from .calculations import recalculate_department


def run():
    """Загрузить демо-данные с автоматическим расчётом нагрузки."""

    print("Очищаю базу данных...")
    # Очистить в правильном порядке зависимостей
    JournalEntry.objects.all().delete()
    Adjustment.objects.all().delete()
    LoadItem.objects.all().delete()
    Discipline.objects.all().delete()
    Curriculum.objects.all().delete()
    Teacher.objects.all().delete()
    Department.objects.all().delete()
    Group.objects.all().delete()
    Specialty.objects.all().delete()
    PositionRule.objects.all().delete()

    # Удалить старых пользователей демо (включая старые реальные имена для приватности)
    demo_usernames = [
        'deputy_dean', 'head', 'head_it', 'head_math', 'head_prog', 'head_pisp',
        'teacher', 'teacher_it', 'teacher_math', 'teacher_prog',
        'sokolov', 'morozov', 'fedorova', 'novikova', 'lebedev',
        'pavlysh', 'bulgakova', 'prohozhiy', 'vorobyev', 'podoksyonov', 'karaulova', 'korchevskaya'
    ]
    User.objects.filter(username__in=demo_usernames).delete()

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
    spec_po = Specialty.objects.create(
        code="6-05-0611-01",
        name="Программная инженерия",
        abbr="ПО"
    )
    print(f"  ✓ {spec_pi}")
    print(f"  ✓ {spec_pm}")
    print(f"  ✓ {spec_po}\n")

    # ============ КАФЕДРЫ ============
    print("🏛️  Создаю кафедры...")
    dept_it = Department.objects.create(
        name="Кафедра информационных технологий и управления бизнесом"
    )
    dept_math = Department.objects.create(
        name="Кафедра математики"
    )
    dept_pisp = Department.objects.create(
        name="Кафедра прикладного и системного программирования"
    )
    print(f"  ✓ {dept_it}")
    print(f"  ✓ {dept_math}")
    print(f"  ✓ {dept_pisp}\n")

    # ============ ГРУППЫ ============
    print("👥 Создаю студенческие группы...")
    # Группы 24 и 23 студента формируют контрольный расчёт нагрузки 277.95 ч
    group_pi1 = Group.objects.create(
        name="26ПИ1д",
        specialty=spec_pi,
        course=1,
        study_form="full_time",
        students_count=24,
        subgroups_count=2
    )
    group_pi2 = Group.objects.create(
        name="26ПИ2д",
        specialty=spec_pi,
        course=1,
        study_form="full_time",
        students_count=23,
        subgroups_count=2
    )
    group_pm1 = Group.objects.create(
        name="26ПМ1д",
        specialty=spec_pm,
        course=1,
        study_form="full_time",
        students_count=25,
        subgroups_count=2
    )
    group_po1 = Group.objects.create(
        name="26ПО1д",
        specialty=spec_po,
        course=1,
        study_form="full_time",
        students_count=22,
        subgroups_count=2
    )
    print(f"  ✓ {group_pi1} ({group_pi1.students_count} студ.)")
    print(f"  ✓ {group_pi2} ({group_pi2.students_count} студ.)")
    print(f"  ✓ {group_pm1} ({group_pm1.students_count} студ.)")
    print(f"  ✓ {group_po1} ({group_po1.students_count} студ.)\n")

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
    cur_po_2026 = Curriculum.objects.create(
        specialty=spec_po,
        admission_year=2026,
        study_form="full_time"
    )
    print(f"  ✓ {cur_pi_2026}")
    print(f"  ✓ {cur_pm_2026}")
    print(f"  ✓ {cur_po_2026}\n")

    # ============ ДИСЦИПЛИНЫ (по 4 на каждую кафедру) ============
    print("📖 Создаю дисциплины кафедр...")

    # --- 1. Кафедра информационных технологий (dept_it) ---
    disc_db = Discipline.objects.create(
        curriculum=cur_pi_2026,
        department=dept_it,
        name="Базы данных",
        semester=1,
        control_form="exam",
        hours_lec=Decimal('30'),
        hours_pract=Decimal('20'),
        hours_lab=Decimal('40'),
        hours_usr=Decimal('18'),
        control_works=2,
        credits=Decimal('5')
    )
    disc_db.groups.set([group_pi1, group_pi2])  # 24 + 23 = 47 студентов -> 277.95 ч

    disc_net = Discipline.objects.create(
        curriculum=cur_pi_2026,
        department=dept_it,
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
    disc_net.groups.set([group_pi1, group_pi2])

    disc_web = Discipline.objects.create(
        curriculum=cur_pi_2026,
        department=dept_it,
        name="Веб-технологии и разработка интерфейсов",
        semester=2,
        control_form="exam",
        hours_lec=Decimal('30'),
        hours_pract=Decimal('20'),
        hours_lab=Decimal('30'),
        hours_usr=Decimal('10'),
        control_works=1,
        credits=Decimal('4')
    )
    disc_web.groups.set([group_pi1, group_pi2])

    disc_is = Discipline.objects.create(
        curriculum=cur_pi_2026,
        department=dept_it,
        name="Информационные системы и базы знаний",
        semester=1,
        control_form="credit",
        hours_lec=Decimal('24'),
        hours_pract=Decimal('16'),
        hours_lab=Decimal('20'),
        hours_usr=Decimal('0'),
        control_works=1,
        credits=Decimal('3')
    )
    disc_is.groups.set([group_pi1, group_pi2])

    # --- 2. Кафедра математики (dept_math) ---
    disc_matan = Discipline.objects.create(
        curriculum=cur_pi_2026,
        department=dept_math,
        name="Математический анализ",
        semester=1,
        control_form="exam",
        hours_lec=Decimal('40'),
        hours_pract=Decimal('30'),
        hours_lab=Decimal('0'),
        hours_usr=Decimal('10'),
        control_works=2,
        credits=Decimal('5')
    )
    disc_matan.groups.set([group_pi1, group_pi2, group_pm1])

    disc_geom = Discipline.objects.create(
        curriculum=cur_pi_2026,
        department=dept_math,
        name="Линейная алгебра и аналитическая геометрия",
        semester=1,
        control_form="exam",
        hours_lec=Decimal('30'),
        hours_pract=Decimal('20'),
        hours_lab=Decimal('0'),
        hours_usr=Decimal('0'),
        control_works=1,
        credits=Decimal('4')
    )
    disc_geom.groups.set([group_pi1, group_pi2, group_pm1])

    disc_discrete = Discipline.objects.create(
        curriculum=cur_pi_2026,
        department=dept_math,
        name="Дискретная математика и теория графов",
        semester=2,
        control_form="exam",
        hours_lec=Decimal('30'),
        hours_pract=Decimal('20'),
        hours_lab=Decimal('0'),
        hours_usr=Decimal('10'),
        control_works=1,
        credits=Decimal('4')
    )
    disc_discrete.groups.set([group_pi1, group_pi2, group_pm1])

    disc_prob = Discipline.objects.create(
        curriculum=cur_pi_2026,
        department=dept_math,
        name="Теория вероятностей и математическая статистика",
        semester=2,
        control_form="credit",
        hours_lec=Decimal('24'),
        hours_pract=Decimal('24'),
        hours_lab=Decimal('0'),
        hours_usr=Decimal('0'),
        control_works=1,
        credits=Decimal('3')
    )
    disc_prob.groups.set([group_pi1, group_pi2, group_pm1])

    # --- 3. Кафедра прикладного и системного программирования (dept_pisp) ---
    disc_prog_intro = Discipline.objects.create(
        curriculum=cur_po_2026,
        department=dept_pisp,
        name="Основы и методологии программирования",
        semester=1,
        control_form="exam",
        hours_lec=Decimal('30'),
        hours_pract=Decimal('0'),
        hours_lab=Decimal('40'),
        hours_usr=Decimal('10'),
        control_works=1,
        credits=Decimal('5')
    )
    disc_prog_intro.groups.set([group_pi1, group_pi2, group_po1])

    disc_alg = Discipline.objects.create(
        curriculum=cur_po_2026,
        department=dept_pisp,
        name="Алгоритмы и структуры данных",
        semester=2,
        control_form="exam",
        hours_lec=Decimal('30'),
        hours_pract=Decimal('10'),
        hours_lab=Decimal('30'),
        hours_usr=Decimal('10'),
        control_works=1,
        credits=Decimal('4')
    )
    disc_alg.groups.set([group_pi1, group_pi2, group_po1])

    disc_oop = Discipline.objects.create(
        curriculum=cur_po_2026,
        department=dept_pisp,
        name="Объектно-ориентированное программирование",
        semester=2,
        control_form="exam",
        hours_lec=Decimal('30'),
        hours_pract=Decimal('0'),
        hours_lab=Decimal('40'),
        hours_usr=Decimal('10'),
        control_works=2,
        credits=Decimal('5')
    )
    disc_oop.groups.set([group_pi1, group_pi2, group_po1])

    disc_arch = Discipline.objects.create(
        curriculum=cur_po_2026,
        department=dept_pisp,
        name="Архитектура вычислительных систем",
        semester=1,
        control_form="credit",
        hours_lec=Decimal('24'),
        hours_pract=Decimal('0'),
        hours_lab=Decimal('24'),
        hours_usr=Decimal('0'),
        control_works=0,
        credits=Decimal('3')
    )
    disc_arch.groups.set([group_pi1, group_po1])

    print("  ✓ Дисциплины созданы (по 4 дисциплины на каждую из 3 кафедр)\n")

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
        min_lecture_hours=100,  # 100 ч согласно заданию
        can_lecture=True
    )
    pos_st_prep = PositionRule.objects.create(
        position="Старший преподаватель",
        min_hours=840,
        max_hours=900,
        min_lecture_hours=150,
        can_lecture=True
    )
    pos_assistant = PositionRule.objects.create(
        position="Ассистент",
        min_hours=840,
        max_hours=900,
        min_lecture_hours=0,
        can_lecture=False  # без права чтения лекций
    )
    print(f"  ✓ {pos_prof}")
    print(f"  ✓ {pos_docent}")
    print(f"  ✓ {pos_st_prep}")
    print(f"  ✓ {pos_assistant}\n")

    # ============ ГРУППЫ ПРАВ (РОЛИ) ============
    group_deputy_dean, _ = UserGroup.objects.get_or_create(name='deputy_dean')
    group_head, _ = UserGroup.objects.get_or_create(name='head')
    group_teacher, _ = UserGroup.objects.get_or_create(name='teacher')

    # ============ ВЫМЫШЛЕННЫЕ ПРЕПОДАВАТЕЛИ И ПОЛЬЗОВАТЕЛИ ============
    print("👨‍🏫 Создаю вымышленных преподавателей для каждой кафедры...\n")

    def create_user_and_teacher(username, full_name, department, position, degree, rank, rate, roles, email=None):
        parts = full_name.split()
        first_name = parts[1] if len(parts) > 1 else ''
        last_name = parts[0] if parts else ''
        user = User.objects.create_user(
            username=username,
            password='pass123',
            first_name=first_name,
            last_name=last_name,
            email=email or f"{username}@example.com"
        )
        for role in roles:
            user.groups.add(role)

        teacher = Teacher.objects.create(
            user=user,
            full_name=full_name,
            department=department,
            position=position,
            degree=degree,
            rank=rank,
            rate=rate
        )
        return user, teacher

    # --- 1. Преподаватели кафедры ИТ ---
    # Зав. кафедрой ИТ (логин head)
    user_head_it, teacher_head_it = create_user_and_teacher(
        username='head',
        full_name="Смирнов Алексей Иванович",
        department=dept_it,
        position=pos_docent,
        degree="candidate",
        rank="доцент",
        rate=Decimal('1.0'),
        roles=[group_head, group_teacher]
    )
    dept_it.head = teacher_head_it
    dept_it.save()
    teacher_head_it.specialization.set([disc_db, disc_web])

    # Основной демо-преподаватель ИТ (логин teacher)
    user_teacher_it, teacher_it = create_user_and_teacher(
        username='teacher',
        full_name="Кузнецова Ольга Дмитриевна",
        department=dept_it,
        position=pos_st_prep,
        degree="none",
        rank="",
        rate=Decimal('1.0'),
        roles=[group_teacher]
    )
    teacher_it.specialization.set([disc_db, disc_net])

    # Ассистент ИТ
    _, teacher_sokolov = create_user_and_teacher(
        username='sokolov',
        full_name="Соколов Роман Павлович",
        department=dept_it,
        position=pos_assistant,
        degree="none",
        rank="",
        rate=Decimal('0.5'),
        roles=[group_teacher]
    )
    teacher_sokolov.specialization.set([disc_net, disc_is])

    # --- 2. Преподаватели кафедры Математики ---
    # Зав. кафедрой математики (логин head_math)
    user_head_math, teacher_head_math = create_user_and_teacher(
        username='head_math',
        full_name="Васильев Дмитрий Сергеевич",
        department=dept_math,
        position=pos_prof,
        degree="doctor",
        rank="профессор",
        rate=Decimal('1.0'),
        roles=[group_head, group_teacher]
    )
    dept_math.head = teacher_head_math
    dept_math.save()
    teacher_head_math.specialization.set([disc_matan, disc_discrete])

    # Преподаватель математики (логин teacher_math)
    _, teacher_math = create_user_and_teacher(
        username='teacher_math',
        full_name="Морозов Андрей Николаевич",
        department=dept_math,
        position=pos_docent,
        degree="candidate",
        rank="доцент",
        rate=Decimal('1.0'),
        roles=[group_teacher]
    )
    teacher_math.specialization.set([disc_matan, disc_geom])

    # Второй преподаватель математики
    _, teacher_fedorova = create_user_and_teacher(
        username='fedorova',
        full_name="Федорова Екатерина Павловна",
        department=dept_math,
        position=pos_docent,
        degree="candidate",
        rank="доцент",
        rate=Decimal('0.75'),
        roles=[group_teacher]
    )
    teacher_fedorova.specialization.set([disc_geom, disc_prob])

    # --- 3. Преподаватели кафедры Программирования ---
    # Зав. кафедрой программирования (логин head_prog)
    user_head_prog, teacher_head_prog = create_user_and_teacher(
        username='head_prog',
        full_name="Ковалев Роман Михайлович",
        department=dept_pisp,
        position=pos_docent,
        degree="candidate",
        rank="доцент",
        rate=Decimal('1.0'),
        roles=[group_head, group_teacher]
    )
    dept_pisp.head = teacher_head_prog
    dept_pisp.save()
    teacher_head_prog.specialization.set([disc_prog_intro, disc_arch])

    # Преподаватель программирования (логин teacher_prog)
    _, teacher_prog = create_user_and_teacher(
        username='teacher_prog',
        full_name="Новикова Анна Александровна",
        department=dept_pisp,
        position=pos_docent,
        degree="candidate",
        rank="доцент",
        rate=Decimal('1.0'),
        roles=[group_teacher]
    )
    teacher_prog.specialization.set([disc_alg, disc_oop])

    # Старший преподаватель программирования
    _, teacher_lebedev = create_user_and_teacher(
        username='lebedev',
        full_name="Лебедев Максим Игоревич",
        department=dept_pisp,
        position=pos_st_prep,
        degree="none",
        rank="",
        rate=Decimal('1.0'),
        roles=[group_teacher]
    )
    teacher_lebedev.specialization.set([disc_prog_intro, disc_oop])

    # --- 4. Зам. декана ---
    from django.contrib.contenttypes.models import ContentType
    from django.contrib.auth.models import Permission

    ref_models = [Specialty, Group, Curriculum, Discipline, Department]
    for model in ref_models:
        ct = ContentType.objects.get_for_model(model)
        perms = Permission.objects.filter(content_type=ct)
        group_deputy_dean.permissions.add(*perms)

    user_deputy = User.objects.create_user(
        username='deputy_dean',
        password='pass123',
        first_name='Елена',
        last_name='Орлова',
        email='dekanat@example.com',
        is_staff=True
    )
    user_deputy.groups.add(group_deputy_dean)

    print("✓ Преподаватели и учетные записи созданы:")
    print("  • ИТ: Смирнов А.И. (head), Кузнецова О.Д. (teacher), Соколов Р.П. (sokolov)")
    print("  • Математика: Васильев Д.С. (head_math), Морозов А.Н. (teacher_math), Федорова Е.П. (fedorova)")
    print("  • Программирование: Ковалев Р.М. (head_prog), Новикова А.А. (teacher_prog), Лебедев М.И. (lebedev)")
    print("  • Деканат: Орлова Е.В. (deputy_dean)\n")

    # ============ ПАРАМЕТРЫ РАСЧЁТА И АВТОРАСЧЁТ НАГРУЗКИ ============
    print("⚙️  Инициализирую параметры расчёта...")
    settings = CalcSettings.get()

    print("⚡ АВТОМАТИЧЕСКИ РАССЧИТЫВАЮ НАГРУЗКУ ДЛЯ ВСЕХ КАФЕДР...")
    for dept in Department.objects.all():
        recalculate_department(dept, settings)
        count = LoadItem.objects.filter(discipline__department=dept).count()
        print(f"  ✓ {dept.name}: создано {count} элементов нагрузки")
    print()

    # ============ ПЕРВОНАЧАЛЬНОЕ РАСПРЕДЕЛЕНИЕ И ДЕМО-ЖУРНАЛ ============
    print("📋 Выполняю демонстрационное распределение нагрузки...")

    # --- Назначения для кафедры ИТ ---
    # "Базы данных" (277.95 ч): лекции и экзамен зав. кафедрой, практики и лабы Кузнецовой
    db_items = LoadItem.objects.filter(discipline=disc_db)
    for item in db_items:
        if item.kind in ('lec', 'exam'):
            item.teacher = teacher_head_it
            item.save()
        elif item.kind in ('pract', 'lab', 'usr', 'kr'):
            item.teacher = teacher_it
            item.save()

    # "Компьютерные сети": лабораторные отдаем ассистенту Соколову
    for item in LoadItem.objects.filter(discipline=disc_net, kind='lab'):
        item.teacher = teacher_sokolov
        item.save()

    # --- Назначения для кафедры Математики ---
    matan_items = LoadItem.objects.filter(discipline=disc_matan)
    for item in matan_items:
        if item.kind in ('lec', 'exam'):
            item.teacher = teacher_head_math
            item.save()
        elif item.kind in ('pract', 'usr', 'kr'):
            item.teacher = teacher_math
            item.save()

    for item in LoadItem.objects.filter(discipline=disc_geom, kind='pract'):
        item.teacher = teacher_fedorova
        item.save()

    # --- Назначения для кафедры Программирования ---
    prog_items = LoadItem.objects.filter(discipline=disc_prog_intro)
    for item in prog_items:
        if item.kind in ('lec', 'exam'):
            item.teacher = teacher_head_prog
            item.save()
        elif item.kind in ('lab', 'usr'):
            item.teacher = teacher_prog
            item.save()

    for item in LoadItem.objects.filter(discipline=disc_arch, kind='lab'):
        item.teacher = teacher_lebedev
        item.save()

    print("  ✓ Нагрузка частично распределена (остальные дисциплины готовы к живому назначению на демо)\n")

    # ============ ДЕМО-ЗАПИСИ ЖУРНАЛА И КОРРЕКТИРОВКИ ============
    print("📝 Создаю записи в журнале и корректировку плана/факта...")
    pract_item = LoadItem.objects.filter(teacher=teacher_it, kind='pract').first()
    lab_item = LoadItem.objects.filter(teacher=teacher_it, kind='lab').first()

    if pract_item:
        JournalEntry.objects.create(
            teacher=teacher_it,
            load_item=pract_item,
            date=date(2026, 9, 15),
            time='10:00',
            hours=Decimal('4'),
            topic='Проектирование реляционной схемы базы данных'
        )
        JournalEntry.objects.create(
            teacher=teacher_it,
            load_item=pract_item,
            date=date(2026, 10, 14),
            time='10:00',
            hours=Decimal('4'),
            topic='Нормализация таблиц до 3НФ и формы Бойса-Кодда'
        )

    if lab_item:
        JournalEntry.objects.create(
            teacher=teacher_it,
            load_item=lab_item,
            date=date(2026, 11, 10),
            time='12:00',
            hours=Decimal('4'),
            topic='Составление сложных аналитических запросов с CTE и окнами'
        )

    Adjustment.objects.create(
        teacher=teacher_it,
        discipline=disc_db,
        kind='pract',
        month=11,
        year=2026,
        hours_delta=Decimal('2'),
        reason='Дополнительная консультация перед рубежным контролем',
        planned=False
    )
    print("  ✓ Записи журнала и корректировка добавлены\n")

    # ============ ИТОГОВАЯ СТАТИСТИКА ============
    total_load_items = LoadItem.objects.count()
    print("=" * 65)
    print("✅ ВСЕ ДЕМО-ДАННЫЕ УСПЕШНО ЗАГРУЖЕНЫ И РАССЧИТАНЫ")
    print("=" * 65)
    print(f"""
📊 СТАТИСТИКА:
  • Кафедры: 3
  • Дисциплины: 12 (по 4 на каждую кафедру)
  • Элементов нагрузки: {total_load_items} (сразу рассчитаны и готовы)
  • Контрольная дисциплина "Базы данных": ровно 277.95 ч
  • Преподавателей: 9 (все имена вымышлены)

🔐 УЧЁТНЫЕ ЗАПИСИ ДЛЯ ДЕМОНСТРАЦИИ (пароль у всех: pass123):
  -------------------------------------------------------------
  🏛️  Кафедра информационных технологий:
     head        → Зав. кафедрой (Смирнов А.И.)
     teacher     → Преподаватель (Кузнецова О.Д.)
     sokolov     → Ассистент (Соколов Р.П.)

  🏛️  Кафедра математики:
     head_math   → Зав. кафедрой (Васильев Д.С.)
     teacher_math→ Преподаватель (Морозов А.Н.)
     fedorova    → Доцент (Федорова Е.П.)

  🏛️  Кафедра прикладного и системного программирования:
     head_prog   → Зав. кафедрой (Ковалев Р.М.)
     teacher_prog→ Преподаватель (Новикова А.А.)
     lebedev     → Старший преподаватель (Лебедев М.И.)

  🏛️  Деканат:
     deputy_dean → Зам. декана (Орлова Е.В.)
  -------------------------------------------------------------
""")


if __name__ == '__main__':
    run()
