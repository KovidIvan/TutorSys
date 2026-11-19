from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.http import Http404

from core.models import JournalEntry, LoadItem
from . import ServiceError


def add_journal_entry(teacher, load_item_id, raw_date, raw_time, raw_hours, topic):
    """Validate and save a journal entry for one of the teacher's load items."""
    try:
        if not raw_date:
            raise ValueError('Дата не указана.')
        entry_date = date.fromisoformat(raw_date)

        if not raw_hours:
            raise ValueError('Часы не указаны.')
        hours_decimal = Decimal(raw_hours)
        if (
            hours_decimal <= Decimal('0')
            or not hours_decimal.is_finite()
            or hours_decimal > Decimal('999.99')
        ):
            raise ValueError('Некорректное значение часов.')

        if not topic:
            raise ValueError('Тема не указана.')

        load_item = LoadItem.objects.get(pk=load_item_id, teacher=teacher)
        return JournalEntry.objects.create(
            teacher=teacher,
            load_item=load_item,
            date=entry_date,
            time=raw_time if raw_time else None,
            hours=hours_decimal,
            topic=topic,
        )
    except (ValueError, TypeError, ValidationError, InvalidOperation, LoadItem.DoesNotExist, Http404) as exc:
        raise ServiceError('Не удалось добавить запись: проверьте дату и часы.') from exc
