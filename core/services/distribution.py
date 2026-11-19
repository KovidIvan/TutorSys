from html import escape

from django.conf import settings
from django.db import connection, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.calculations import teacher_total
from core.models import LoadItem, Teacher, TelegramOutbox
from core.tgbot.texts import display_hours, display_name
from . import ServiceError


def department_of(user):
    """Return a user's teacher department, or None when no teacher profile exists."""
    try:
        return user.teacher.department
    except (AttributeError, Teacher.DoesNotExist):
        return None


def _queue_assignment_notification(teacher, load_item, assigned):
    if TelegramOutbox._meta.db_table not in connection.introspection.table_names():
        return

    try:
        link = teacher.user.telegram_link
    except AttributeError:
        return

    action = 'назначена' if assigned else 'снята'
    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        text = f'🧩 Нагрузка {action}.'
    else:
        group = load_item.group.name if load_item.group_id else 'Поток'
        if load_item.subgroup_no:
            group += f'-{load_item.subgroup_no}'
        item = ' · '.join((
            escape(load_item.discipline.name, quote=True),
            escape(load_item.get_kind_display(), quote=True),
            escape(group, quote=True),
        ))
        text = f'🧩 <b>Нагрузка {action}.</b>\n{item}'

    TelegramOutbox.objects.create(
        link=link,
        kind='assignment' if assigned else 'unassignment',
        text=text,
        dedupe_key=f'load-assignment:{teacher.pk}',
    )


def _queue_overload_notification(teacher, department):
    if TelegramOutbox._meta.db_table not in connection.introspection.table_names():
        return

    try:
        department_head = department.head
    except Teacher.DoesNotExist:
        return
    if not department_head:
        return

    try:
        link = department_head.user.telegram_link
    except AttributeError:
        return

    dedupe_key = f'teacher-overload:{teacher.pk}:{timezone.localdate():%Y-%m-%d}'
    if TelegramOutbox.objects.filter(link=link, dedupe_key=dedupe_key).exists():
        return

    if settings.TELEGRAM_PRIVACY_MODE == 'minimal':
        text = '🔴 Преподаватель превысил максимальную нагрузку.'
    else:
        total_hours = teacher_total(teacher)
        text = (
            f'🔴 <b>Превышен максимум нагрузки.</b> '
            f'{display_name(teacher, department_head.user)}: '
            f'{display_hours(total_hours, department_head.user, teacher)} из '
            f'{display_hours(teacher.effective_max_hours, department_head.user, teacher)} ч.'
        )
    TelegramOutbox.objects.create(
        link=link,
        kind='overload',
        text=text,
        dedupe_key=dedupe_key,
    )


def assign_load_item(actor_user, load_item_id, teacher_id_or_none):
    """Assign or unassign a load item within the head's own department."""
    if not actor_user.groups.filter(name='head').exists():
        raise ServiceError('Доступ запрещён. Только зав. кафедрой может распределять нагрузку.', 403)

    load_item = get_object_or_404(LoadItem.objects.select_related('discipline'), pk=load_item_id)
    department = department_of(actor_user)
    if not department or load_item.discipline.department_id != department.pk:
        raise ServiceError('Нельзя менять нагрузку другой кафедры.', 403)

    teacher = None
    if teacher_id_or_none:
        teacher = get_object_or_404(Teacher, pk=teacher_id_or_none)
        if teacher.department_id != department.pk:
            raise ServiceError('Нельзя назначить преподавателя другой кафедры.', 403)

    previous_teacher = load_item.teacher
    changed = (previous_teacher.pk if previous_teacher else None) != (teacher.pk if teacher else None)
    with transaction.atomic():
        previous_total = teacher_total(teacher) if changed and teacher else None
        load_item.teacher = teacher
        load_item.save()
        if changed and previous_teacher:
            _queue_assignment_notification(previous_teacher, load_item, assigned=False)
        if changed and teacher:
            _queue_assignment_notification(teacher, load_item, assigned=True)
            if (
                previous_total <= teacher.effective_max_hours
                and teacher_total(teacher) > teacher.effective_max_hours
            ):
                _queue_overload_notification(teacher, department)
    return load_item
