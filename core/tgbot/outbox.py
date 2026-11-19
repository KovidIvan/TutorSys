import asyncio
import logging
import time
from collections import defaultdict
from datetime import timedelta

from asgiref.sync import sync_to_async
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.models import TelegramLink, TelegramOutbox

logger = logging.getLogger(__name__)


def _next_batch():
    now = timezone.now()
    cutoff = now - timedelta(seconds=60)
    with transaction.atomic():
        records = list(TelegramOutbox.objects.filter(
            sent_at__isnull=True, attempts__lt=3
        ).select_related('link').order_by('created_at')[:100])
    groups = defaultdict(list)
    for record in records:
        key = (record.link_id, record.dedupe_key) if record.dedupe_key else (record.link_id, f'row:{record.pk}')
        groups[key].append(record)
    batches = []
    for grouped in groups.values():
        if grouped[0].dedupe_key and max(item.created_at for item in grouped) > cutoff:
            continue
        first = grouped[0]
        if not first.link.notifications_enabled:
            batches.append(([item.pk for item in grouped], first.link_id, first.link.chat_id,
                            False, first.kind, '', max(item.attempts for item in grouped)))
            continue
        details = []
        for item in grouped:
            lines = item.text.splitlines()
            detail = lines[-1] if lines else ''
            if detail and detail not in details:
                details.append(detail)
        if len(details) > 1:
            header = f'🧩 <b>Обновлено назначений нагрузки ({len(details)}):</b>'
            shown_details = []
            current_length = len(header)
            for detail in details:
                if len(shown_details) >= 10 or current_length + len(detail) + 1 > 3800:
                    break
                shown_details.append(detail)
                current_length += len(detail) + 1
            remaining = len(details) - len(shown_details)
            if remaining:
                shown_details.append(f'… и ещё {remaining}')
            text = header + '\n' + '\n'.join(shown_details)
        else:
            text = first.text
        batches.append(([item.pk for item in grouped], first.link_id, first.link.chat_id,
                        True, first.kind, text, max(item.attempts for item in grouped)))
    return batches


def _mark_sent(ids):
    TelegramOutbox.objects.filter(pk__in=ids).update(sent_at=timezone.now(), last_error='')


def _mark_error(record_ids, message, *, disable=False, terminal=False):
    records = TelegramOutbox.objects.filter(pk__in=record_ids)
    records.update(
        attempts=3 if terminal else F('attempts') + 1,
        last_error=str(message)[:255],
    )
    if disable:
        TelegramLink.objects.filter(outbox__pk__in=record_ids).update(notifications_enabled=False)


def _cleanup_old():
    cutoff = timezone.now() - timedelta(days=settings.TELEGRAM_OUTBOX_KEEP_DAYS)
    TelegramOutbox.objects.filter(sent_at__lt=cutoff).delete()


async def outbox_loop(bot):
    last_sent_by_chat = {}
    last_global_send = 0.0
    while True:
        records = await sync_to_async(_next_batch, thread_sensitive=True)()
        for record_ids, link_id, chat_id, enabled, kind, text, attempts in records:
            if not enabled:
                await sync_to_async(_mark_error, thread_sensitive=True)(
                    record_ids, 'Уведомления отключены.', terminal=True
                )
                continue
            now = time.monotonic()
            wait_for_chat = max(0.0, 1.0 - (now - last_sent_by_chat.get(chat_id, 0.0)))
            wait_global = max(0.0, (1.0 / 25) - (now - last_global_send))
            if wait_for_chat or wait_global:
                await asyncio.sleep(max(wait_for_chat, wait_global))
            try:
                await bot.send_message(chat_id, text, parse_mode='HTML')
                await sync_to_async(_mark_sent, thread_sensitive=True)(record_ids)
                sent_at = time.monotonic()
                last_sent_by_chat[chat_id] = sent_at
                last_global_send = sent_at
            except TelegramForbiddenError:
                await sync_to_async(_mark_error, thread_sensitive=True)(
                    record_ids, 'Пользователь заблокировал бота.', disable=True, terminal=True
                )
            except TelegramRetryAfter as exc:
                retry_attempts = attempts + 1
                await sync_to_async(_mark_error, thread_sensitive=True)(
                    record_ids, 'Telegram ограничил частоту отправки.', terminal=retry_attempts >= 3
                )
                if retry_attempts < 3:
                    await asyncio.sleep(exc.retry_after)
            except Exception as exc:
                logger.warning('Telegram outbox send failed: record_ids=%s type=%s', record_ids, type(exc).__name__)
                retry_attempts = attempts + 1
                await sync_to_async(_mark_error, thread_sensitive=True)(
                    record_ids, type(exc).__name__, terminal=retry_attempts >= 3
                )
        await sync_to_async(_cleanup_old, thread_sensitive=True)()
        await asyncio.sleep(settings.TELEGRAM_OUTBOX_POLL_SECONDS)
