from django.db import models
from django.contrib.auth.models import User
from decimal import Decimal
from django.db.models import Q
from datetime import date


def current_year():
    return date.today().year


# ============ СПРАВОЧНИКИ И УЧЕБНЫЙ ПЛАН ============

class Specialty(models.Model):
    """Специальность (направление подготовки)."""
    code = models.CharField(
        max_length=20,
        unique=True,
        verbose_name='Код специальности',
        help_text='Например: 09.03.03'
    )
    name = models.CharField(
        max_length=255,
        verbose_name='Название',
        help_text='Полное название специальности'
    )
    abbr = models.CharField(
        max_length=50,
        verbose_name='Сокращение',
        help_text='Например: ПИ'
    )

    class Meta:
        verbose_name = 'Специальность'
        verbose_name_plural = 'Специальности'
        ordering = ['code']

    def __str__(self):
        return f'{self.code} — {self.abbr}'


class Department(models.Model):
    """Кафедра."""
    name = models.CharField(
        max_length=255,
        unique=True,
        verbose_name='Название кафедры'
    )
    head = models.OneToOneField(
        'Teacher',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Заведующий кафедрой',
        related_name='head_of_department'
    )

    class Meta:
        verbose_name = 'Кафедра'
        verbose_name_plural = 'Кафедры'
        ordering = ['name']

    def __str__(self):
        return self.name


class Group(models.Model):
    """Группа студентов."""
    STUDY_FORM_CHOICES = [
        ('full_time', 'Очная'),
        ('part_time', 'Очно-заочная'),
        ('correspondence', 'Заочная'),
    ]

    name = models.CharField(
        max_length=50,
        unique=True,
        verbose_name='Номер группы'
    )
    specialty = models.ForeignKey(
        Specialty,
        on_delete=models.CASCADE,
        verbose_name='Специальность',
        related_name='groups'
    )
    course = models.IntegerField(
        verbose_name='Курс',
        help_text='1, 2, 3, 4'
    )
    study_form = models.CharField(
        max_length=20,
        choices=STUDY_FORM_CHOICES,
        default='full_time',
        verbose_name='Форма обучения'
    )
    students_count = models.IntegerField(
        verbose_name='Количество студентов'
    )
    subgroups_count = models.IntegerField(
        null=True,
        blank=True,
        verbose_name='Число подгрупп',
        help_text='Если NULL, берётся из CalcSettings'
    )

    class Meta:
        verbose_name = 'Группа'
        verbose_name_plural = 'Группы'
        ordering = ['name']

    def __str__(self):
        return self.name


class Curriculum(models.Model):
    """Учебный план (привязка специальности к году и форме обучения)."""
    specialty = models.ForeignKey(
        Specialty,
        on_delete=models.CASCADE,
        verbose_name='Специальность',
        related_name='curriculums'
    )
    admission_year = models.IntegerField(
        verbose_name='Год поступления',
        help_text='Например: 2023'
    )
    study_form = models.CharField(
        max_length=20,
        choices=Group.STUDY_FORM_CHOICES,
        default='full_time',
        verbose_name='Форма обучения'
    )

    class Meta:
        verbose_name = 'Учебный план'
        verbose_name_plural = 'Учебные планы'
        unique_together = ('specialty', 'admission_year', 'study_form')
        ordering = ['-admission_year', 'specialty']

    def __str__(self):
        return f'{self.specialty.abbr} {self.admission_year} ({self.get_study_form_display()})'


class Discipline(models.Model):
    """Дисциплина (предмет)."""
    CONTROL_FORM_CHOICES = [
        ('exam', 'Экзамен'),
        ('credit', 'Зачёт'),
    ]

    curriculum = models.ForeignKey(
        Curriculum,
        on_delete=models.CASCADE,
        verbose_name='Учебный план',
        related_name='disciplines'
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Ответственная кафедра',
        help_text='Если не указана, дисциплина не распределена'
    )
    name = models.CharField(
        max_length=255,
        verbose_name='Название дисциплины'
    )
    module = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Модуль',
        help_text='Например: Модуль 1'
    )
    semester = models.IntegerField(
        verbose_name='Семестр'
    )
    control_form = models.CharField(
        max_length=20,
        choices=CONTROL_FORM_CHOICES,
        default='exam',
        verbose_name='Форма контроля'
    )
    hours_lec = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        default=Decimal('0'),
        verbose_name='Часы лекций',
        help_text='На весь поток'
    )
    hours_pract = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        default=Decimal('0'),
        verbose_name='Часы практик',
        help_text='На группу'
    )
    hours_lab = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        default=Decimal('0'),
        verbose_name='Часы лабораторных',
        help_text='На подгруппу'
    )
    hours_usr = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        default=Decimal('0'),
        verbose_name='Часы УСР (самостоятельной работы)',
        help_text='Не входят в нагрузку, только используются для контроля'
    )
    control_works = models.IntegerField(
        default=0,
        verbose_name='Число контрольных работ'
    )
    credits = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0'),
        verbose_name='Зачётные единицы'
    )
    groups = models.ManyToManyField(
        Group,
        verbose_name='Группы',
        help_text='Группы, в которых читается дисциплина'
    )

    class Meta:
        verbose_name = 'Дисциплина'
        verbose_name_plural = 'Дисциплины'
        ordering = ['curriculum', 'semester', 'name']

    def __str__(self):
        return f'{self.name} ({self.curriculum.specialty.abbr})'


# ============ НАСТРАИВАЕМЫЕ ПАРАМЕТРЫ ============

class CalcSettings(models.Model):
    """Глобальные параметры расчёта нагрузки. Всегда одна запись."""

    exam_hours_per_student = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.35'),
        verbose_name='Часы экзамена на одного студента',
        help_text='На одного студента при сдаче экзамена. Единица: часы'
    )
    exam_consult_hours_per_group = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('2'),
        verbose_name='Часы консультаций к экзамену на группу',
        help_text='Одна консультация на всю группу. Единица: часы'
    )
    credit_hours_per_student = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.25'),
        verbose_name='Часы зачёта на одного студента',
        help_text='При сдаче зачёта без оценки. Единица: часы'
    )
    usr_control_hours_per_group = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('2'),
        verbose_name='Часы контроля УСР на группу',
        help_text='Если дисциплина предусматривает самостоятельную работу. Единица: часы'
    )
    kr_hours_per_student = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.25'),
        verbose_name='Часы на одну контрольную работу на студента',
        help_text='Проверка одной контрольной работы. Единица: часы'
    )
    subgroups_per_group = models.IntegerField(
        default=2,
        verbose_name='Число подгрупп на группу по умолчанию',
        help_text='Используется, если в группе не указано явно'
    )

    class Meta:
        verbose_name = 'Параметры расчёта'
        verbose_name_plural = 'Параметры расчёта'

    def __str__(self):
        return 'Глобальные параметры расчёта'

    @classmethod
    def get(cls):
        """Получить единственную запись настроек, создав её со значениями по умолчанию, если её нет."""
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        """Гарантировать, что сохраняется только при pk=1."""
        self.pk = 1
        super().save(*args, **kwargs)


class PositionRule(models.Model):
    """Норма часов по должности преподавателя на 1,0 ставки в год."""
    position = models.CharField(
        max_length=100,
        unique=True,
        verbose_name='Должность',
        help_text='Например: Профессор, Доцент, Ассистент'
    )
    min_hours = models.IntegerField(
        default=840,
        verbose_name='Минимум часов на 1,0 ставки в год',
        help_text='Общие часы преподавания на полную ставку'
    )
    max_hours = models.IntegerField(
        default=900,
        verbose_name='Максимум часов на 1,0 ставки в год',
        help_text='Не более этого на полную ставку'
    )
    min_lecture_hours = models.IntegerField(
        default=200,
        verbose_name='Минимум лекционных часов на 1,0 ставки',
        help_text='Для преподавателя, который может читать лекции'
    )
    can_lecture = models.BooleanField(
        default=True,
        verbose_name='Может ли читать лекции',
        help_text='False для должностей, не имеющих права на лекции'
    )

    class Meta:
        verbose_name = 'Нормы по должности'
        verbose_name_plural = 'Нормы по должностям'
        ordering = ['position']

    def __str__(self):
        return f'{self.position} ({self.min_hours}–{self.max_hours} ч/год)'


# ============ ЛЮДИ И НАГРУЗКА ============

class Teacher(models.Model):
    """Преподаватель."""
    DEGREE_CHOICES = [
        ('none', 'Нет'),
        ('candidate', 'Кандидат наук'),
        ('doctor', 'Доктор наук'),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        verbose_name='Учётная запись Django',
        related_name='teacher'
    )
    full_name = models.CharField(
        max_length=255,
        verbose_name='ФИО'
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        verbose_name='Кафедра',
        related_name='teachers'
    )
    position = models.ForeignKey(
        PositionRule,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name='Должность',
        help_text='От должности берутся нормы часов'
    )
    degree = models.CharField(
        max_length=20,
        choices=DEGREE_CHOICES,
        default='none',
        verbose_name='Учёная степень'
    )
    rank = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Учёное звание'
    )
    rate = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal('1.0'),
        verbose_name='Ставка',
        help_text='1.0 = полная ставка, 0.5 = половина ставки и т.д.'
    )
    override_min_hours = models.IntegerField(
        null=True,
        blank=True,
        verbose_name='Переопределённый минимум часов',
        help_text='Если указано, используется вместо (должность.min × ставка)'
    )
    override_max_hours = models.IntegerField(
        null=True,
        blank=True,
        verbose_name='Переопределённый максимум часов',
        help_text='Если указано, используется вместо (должность.max × ставка)'
    )
    specialization = models.ManyToManyField(
        Discipline,
        blank=True,
        verbose_name='Специализация (дисциплины)',
        help_text='Дисциплины, которые этот преподаватель может вести'
    )

    class Meta:
        verbose_name = 'Преподаватель'
        verbose_name_plural = 'Преподаватели'
        ordering = ['full_name']

    def __str__(self):
        return f'{self.full_name} ({self.department.name})'

    @property
    def effective_min_hours(self):
        """Эффективный минимум часов с учётом переопределения и ставки."""
        if self.override_min_hours is not None:
            return self.override_min_hours
        if self.position:
            return int(self.position.min_hours * self.rate)
        return 840  # default

    @property
    def effective_max_hours(self):
        """Эффективный максимум часов с учётом переопределения и ставки."""
        if self.override_max_hours is not None:
            return self.override_max_hours
        if self.position:
            return int(self.position.max_hours * self.rate)
        return 900  # default


class LoadItem(models.Model):
    """Единица нагрузки (то, что можно назначить преподавателю)."""
    KIND_CHOICES = [
        ('lec', 'Лекция'),
        ('pract', 'Практика'),
        ('lab', 'Лабораторная'),
        ('exam', 'Экзамен'),
        ('credit', 'Зачёт'),
        ('usr', 'Контроль УСР'),
        ('kr', 'Контрольная работа'),
    ]

    discipline = models.ForeignKey(
        Discipline,
        on_delete=models.CASCADE,
        verbose_name='Дисциплина',
        related_name='load_items'
    )
    kind = models.CharField(
        max_length=20,
        choices=KIND_CHOICES,
        verbose_name='Вид работы'
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name='Группа',
        help_text='NULL для лекций (поток)'
    )
    subgroup_no = models.IntegerField(
        null=True,
        blank=True,
        verbose_name='Номер подгруппы',
        help_text='Для лабораторных, иначе NULL'
    )
    hours = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        verbose_name='Часы',
        help_text='Объём этой единицы нагрузки'
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Назначенный преподаватель'
    )

    class Meta:
        verbose_name = 'Элемент нагрузки'
        verbose_name_plural = 'Элементы нагрузки'
        ordering = ['discipline', 'kind']

    def __str__(self):
        info = f'{self.discipline.name} — {self.get_kind_display()}'
        if self.group:
            info += f' ({self.group.name}'
            if self.subgroup_no:
                info += f', подгруппа {self.subgroup_no}'
            info += ')'
        return info


class JournalEntry(models.Model):
    """Запись в журнале: проведённое занятие."""
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        verbose_name='Преподаватель',
        related_name='journal_entries'
    )
    load_item = models.ForeignKey(
        LoadItem,
        on_delete=models.CASCADE,
        verbose_name='Элемент нагрузки'
    )
    date = models.DateField(
        verbose_name='Дата проведения'
    )
    time = models.TimeField(
        null=True,
        blank=True,
        verbose_name='Время начала'
    )
    hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        verbose_name='Затрачено часов'
    )
    topic = models.TextField(
        verbose_name='Тема занятия'
    )

    class Meta:
        verbose_name = 'Запись в журнале'
        verbose_name_plural = 'Записи в журнале'
        ordering = ['-date', 'teacher']

    def __str__(self):
        return f'{self.teacher} — {self.load_item} ({self.date})'


class Adjustment(models.Model):
    """Корректировка нагрузки (внеплановая работа или недовыполнение)."""
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        verbose_name='Преподаватель',
        related_name='adjustments'
    )
    discipline = models.ForeignKey(
        Discipline,
        on_delete=models.CASCADE,
        verbose_name='Дисциплина'
    )
    kind = models.CharField(
        max_length=20,
        choices=LoadItem.KIND_CHOICES,
        verbose_name='Вид работы'
    )
    month = models.IntegerField(
        choices=[(i, str(i)) for i in range(1, 13)],
        verbose_name='Месяц'
    )
    year = models.IntegerField(
        default=current_year,
        verbose_name='Год'
    )
    hours_delta = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        verbose_name='Часы (положительное или отрицательное значение)'
    )
    reason = models.TextField(
        verbose_name='Причина корректировки'
    )
    planned = models.BooleanField(
        default=False,
        verbose_name='Плановая работа',
        help_text='True = плановая, False = внеплановая (замены, пересдачи)'
    )

    class Meta:
        verbose_name = 'Корректировка нагрузки'
        verbose_name_plural = 'Корректировки нагрузки'
        ordering = ['teacher', 'month']

    def __str__(self):
        return f'{self.teacher} — {self.discipline.name} ({self.get_kind_display()}) на {self.month} мес.'
