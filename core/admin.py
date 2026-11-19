from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Specialty, Department, Group, Curriculum, Discipline,
    CalcSettings, PositionRule, Teacher, LoadItem, JournalEntry, Adjustment,
    TelegramProfile
)


# ============ СПЕЦИАЛЬНОСТИ ============

@admin.register(Specialty)
class SpecialtyAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'abbr', 'groups_count']
    list_filter = ['code']
    search_fields = ['code', 'name', 'abbr']
    ordering = ['code']

    def groups_count(self, obj):
        """Количество групп по специальности."""
        return obj.groups.count()
    groups_count.short_description = 'Групп'


# ============ КАФЕДРЫ ============

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'head_display', 'teachers_count', 'disciplines_count']
    list_filter = ['name']
    search_fields = ['name']
    raw_id_fields = ['head']

    def head_display(self, obj):
        """Показать ФИО заведующего."""
        return obj.head.full_name if obj.head else '—'
    head_display.short_description = 'Заведующий'

    def teachers_count(self, obj):
        return obj.teachers.count()
    teachers_count.short_description = 'Преподавателей'

    def disciplines_count(self, obj):
        return obj.discipline_set.count()
    disciplines_count.short_description = 'Дисциплин'


# ============ ГРУППЫ ============

@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'specialty', 'course', 'study_form', 'students_count', 'subgroups_count']
    list_filter = ['specialty', 'course', 'study_form']
    search_fields = ['name', 'specialty__abbr']
    ordering = ['name']


# ============ УЧЕБНЫЕ ПЛАНЫ ============

class DisciplineInline(admin.TabularInline):
    """Встроенное редактирование дисциплин внутри плана."""
    model = Discipline
    extra = 0
    fields = ['name', 'semester', 'department', 'control_form', 'hours_lec', 'hours_pract', 'hours_lab']


@admin.register(Curriculum)
class CurriculumAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'specialty', 'admission_year', 'study_form', 'disciplines_count']
    list_filter = ['specialty', 'admission_year', 'study_form']
    search_fields = ['specialty__code', 'specialty__name']
    inlines = [DisciplineInline]

    def disciplines_count(self, obj):
        return obj.disciplines.count()
    disciplines_count.short_description = 'Дисциплин'


# ============ ДИСЦИПЛИНЫ ============

@admin.register(Discipline)
class DisciplineAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'curriculum', 'semester', 'department', 'control_form',
        'hours_lec', 'hours_pract', 'hours_lab', 'groups_count'
    ]
    list_filter = ['curriculum__specialty', 'semester', 'department', 'control_form']
    search_fields = ['name', 'curriculum__specialty__abbr']
    filter_horizontal = ['groups']

    fieldsets = (
        ('Основное', {
            'fields': ['name', 'curriculum', 'department', 'module']
        }),
        ('Семестр и формы контроля', {
            'fields': ['semester', 'control_form']
        }),
        ('Часы нагрузки', {
            'fields': ['hours_lec', 'hours_pract', 'hours_lab', 'hours_usr', 'control_works']
        }),
        ('Оценка', {
            'fields': ['credits', 'groups']
        }),
    )

    def groups_count(self, obj):
        return obj.groups.count()
    groups_count.short_description = 'Групп'


# ============ ПАРАМЕТРЫ РАСЧЁТА ============

@admin.register(CalcSettings)
class CalcSettingsAdmin(admin.ModelAdmin):
    """Админка параметров расчёта. Запрещено добавление второй записи и удаление."""
    list_display = ['settings_info']
    change_list_template = 'admin/calcsettings_changelist.html'

    fieldsets = (
        ('Параметры расчёта нагрузки', {
            'description': 'Все значения используются для автоматического расчёта элементов нагрузки.',
            'fields': [
                'exam_hours_per_student',
                'exam_consult_hours_per_group',
                'credit_hours_per_student',
                'usr_control_hours_per_group',
                'kr_hours_per_student',
                'subgroups_per_group'
            ]
        }),
    )

    def settings_info(self, obj):
        return 'Глобальные параметры'
    settings_info.short_description = 'Параметры'

    def has_add_permission(self, request):
        """Запретить добавление второй записи (максимум одна)."""
        return not CalcSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        """Запретить удаление единственной записи."""
        return False

    def get_urls(self):
        """Переопределить URL, чтобы редактировать всегда одну запись."""
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path('', self.admin_site.admin_view(self.changelist_view), {'extra_context': {'title': 'Параметры расчёта'}},
                 name='core_calcsettings_changelist'),
        ]
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        """Если есть запись, перенаправить на её редактирование."""
        obj = CalcSettings.objects.first()
        if obj:
            from django.shortcuts import redirect
            return redirect('admin:core_calcsettings_change', obj.pk)
        # Если записей нет, показать пустой список
        return super().changelist_view(request, extra_context)


# ============ НОРМЫ ПО ДОЛЖНОСТЯМ ============

@admin.register(PositionRule)
class PositionRuleAdmin(admin.ModelAdmin):
    list_display = [
        'position', 'min_hours', 'max_hours', 'min_lecture_hours', 'can_lecture_icon'
    ]
    list_filter = ['can_lecture', 'min_lecture_hours']
    search_fields = ['position']
    ordering = ['position']

    fieldsets = (
        ('Должность', {
            'fields': ['position']
        }),
        ('Нормы часов на 1,0 ставки в год', {
            'fields': ['min_hours', 'max_hours']
        }),
        ('Ограничения по лекциям', {
            'fields': ['min_lecture_hours', 'can_lecture']
        }),
    )

    def can_lecture_icon(self, obj):
        """Показать иконку для возможности читать лекции."""
        icon = '✓' if obj.can_lecture else '✗'
        return format_html(f'<span style="font-size: 1.5em;">{icon}</span>')
    can_lecture_icon.short_description = 'Может читать лекции'


# ============ ПРЕПОДАВАТЕЛИ ============

@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = [
        'full_name', 'department', 'position', 'rate', 'degree', 'rank',
        'effective_min_hours_display', 'effective_max_hours_display'
    ]
    list_filter = ['department', 'position', 'degree', 'rate']
    search_fields = ['full_name', 'user__username']
    raw_id_fields = ['user', 'department', 'position']
    filter_horizontal = ['specialization']

    fieldsets = (
        ('Учётная запись', {
            'fields': ['user', 'full_name']
        }),
        ('Должность и кафедра', {
            'fields': ['department', 'position', 'rate']
        }),
        ('Учёные степень и звание', {
            'fields': ['degree', 'rank']
        }),
        ('Переопределение лимитов часов', {
            'description': 'Если указано, используется вместо норм по должности × ставка',
            'fields': ['override_min_hours', 'override_max_hours']
        }),
        ('Специализация', {
            'description': 'Дисциплины, которые может вести',
            'fields': ['specialization'],
            'classes': ['collapse']
        }),
    )

    def effective_min_hours_display(self, obj):
        return f"{obj.effective_min_hours} ч"
    effective_min_hours_display.short_description = 'Эффективный минимум'

    def effective_max_hours_display(self, obj):
        return f"{obj.effective_max_hours} ч"
    effective_max_hours_display.short_description = 'Эффективный максимум'


# ============ ЭЛЕМЕНТЫ НАГРУЗКИ ============

@admin.register(LoadItem)
class LoadItemAdmin(admin.ModelAdmin):
    list_display = [
        'discipline', 'kind', 'group_display', 'hours', 'teacher_display'
    ]
    list_filter = ['discipline__department', 'kind']
    search_fields = ['discipline__name', 'teacher__full_name']
    raw_id_fields = ['discipline', 'group', 'teacher']

    def group_display(self, obj):
        """Показать информацию о группе и подгруппе."""
        if obj.group:
            info = obj.group.name
            if obj.subgroup_no:
                info += f'-{obj.subgroup_no}'
            return info
        return '—'
    group_display.short_description = 'Группа / подгруппа'

    def teacher_display(self, obj):
        """Показать преподавателя или '—' если не назначен."""
        return obj.teacher.full_name if obj.teacher else '—'
    teacher_display.short_description = 'Преподаватель'


# ============ ЖУРНАЛ ============

@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ['teacher', 'load_item', 'date', 'hours', 'topic_short']
    list_filter = ['teacher', 'date', 'load_item__kind']
    search_fields = ['teacher__full_name', 'topic', 'load_item__discipline__name']
    raw_id_fields = ['teacher', 'load_item']
    date_hierarchy = 'date'

    def topic_short(self, obj):
        """Показать первые 50 символов темы."""
        return obj.topic[:50] + '...' if len(obj.topic) > 50 else obj.topic
    topic_short.short_description = 'Тема'


# ============ КОРРЕКТИРОВКИ ============

@admin.register(Adjustment)
class AdjustmentAdmin(admin.ModelAdmin):
    list_display = ['teacher', 'discipline', 'kind', 'month', 'year', 'hours_delta', 'planned', 'reason_short']
    list_filter = ['year', 'month', 'planned', 'kind', 'teacher__department']
    search_fields = ['teacher__full_name', 'discipline__name', 'reason']
    raw_id_fields = ['teacher', 'discipline']

    def reason_short(self, obj):
        """Показать первые 40 символов причины."""
        return obj.reason[:40] + '...' if len(obj.reason) > 40 else obj.reason
    reason_short.short_description = 'Причина'


# ============ ПРОФИЛИ TELEGRAM ============

@admin.register(TelegramProfile)
class TelegramProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'telegram_id', 'telegram_username', 'first_name', 'linked_at']
    search_fields = ['user__username', 'user__first_name', 'telegram_username', 'telegram_id']
    raw_id_fields = ['user']
