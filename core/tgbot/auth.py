import hashlib
import secrets
import string
import threading
from collections import defaultdict, deque
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import TelegramLink, TelegramLinkToken, Teacher

CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
ATTEMPT_LIMIT = 5
ATTEMPT_WINDOW = timedelta(minutes=15)
_attempt_lock = threading.Lock()
_failed_attempts = defaultdict(deque)


def create_link_code(user):
    code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(9))
    now = timezone.now()
    with transaction.atomic():
        TelegramLinkToken.objects.filter(user=user, used_at__isnull=True).update(used_at=now)
        TelegramLinkToken.objects.create(
            user=user,
            token_hash=hashlib.sha256(code.encode('ascii')).hexdigest(),
            expires_at=now + timedelta(minutes=10),
        )
    return code


def _attempt_allowed(telegram_user_id):
    now = timezone.now()
    with _attempt_lock:
        attempts = _failed_attempts[telegram_user_id]
        while attempts and now - attempts[0] > ATTEMPT_WINDOW:
            attempts.popleft()
        return len(attempts) < ATTEMPT_LIMIT


def _record_failed_attempt(telegram_user_id):
    with _attempt_lock:
        attempts = _failed_attempts[telegram_user_id]
        now = timezone.now()
        while attempts and now - attempts[0] > ATTEMPT_WINDOW:
            attempts.popleft()
        attempts.append(now)


def link_account(code, telegram_user_id, chat_id, username='', consent_at=None):
    if not _attempt_allowed(telegram_user_id):
        return False, 'limit'
    token_hash = hashlib.sha256(code.encode('ascii', errors='ignore')).hexdigest()
    try:
        with transaction.atomic():
            token = TelegramLinkToken.objects.select_for_update().select_related('user').get(
                token_hash=token_hash, used_at__isnull=True, expires_at__gt=timezone.now(),
                user__is_active=True,
            )
            if TelegramLink.objects.filter(telegram_user_id=telegram_user_id).exclude(user=token.user).exists():
                return False, 'already_linked'
            if TelegramLink.objects.filter(user=token.user).exclude(telegram_user_id=telegram_user_id).exists():
                return False, 'user_has_link'
            token.used_at = timezone.now()
            token.save(update_fields=['used_at'])
            TelegramLink.objects.update_or_create(
                user=token.user,
                defaults={
                    'telegram_user_id': telegram_user_id,
                    'chat_id': chat_id,
                    'username': (username or '')[:64],
                    'consent_at': consent_at,
                    'notifications_enabled': True,
                },
            )
    except TelegramLinkToken.DoesNotExist:
        _record_failed_attempt(telegram_user_id)
        return False, 'invalid'
    except IntegrityError:
        return False, 'already_linked'
    with _attempt_lock:
        _failed_attempts.pop(telegram_user_id, None)
    return True, token.user_id


def get_link_for_telegram(telegram_user_id):
    return TelegramLink.objects.select_related('user').filter(
        telegram_user_id=telegram_user_id, user__is_active=True
    ).first()


def get_active_user(user_id):
    return User.objects.filter(pk=user_id, is_active=True).first()


def user_roles(user):
    if not user.is_active:
        return set()
    return set(user.groups.filter(name__in=('teacher', 'head', 'deputy_dean')).values_list('name', flat=True))


def unlink_telegram(telegram_user_id):
    return TelegramLink.objects.filter(telegram_user_id=telegram_user_id).delete()[0] > 0


def unlink_all():
    return TelegramLink.objects.all().delete()[0]


def own_teacher(user):
    try:
        return Teacher.objects.select_related('department', 'position').get(user=user)
    except Teacher.DoesNotExist:
        return None


def valid_head_department(user, department_id=None):
    if 'head' not in user_roles(user):
        return None
    try:
        department = user.teacher.department
    except (Teacher.DoesNotExist, AttributeError):
        return None
    if department_id is not None and department.pk != int(department_id):
        return None
    return department
