"""
Утилиты для интеграции S.C.A.L.E с Telegram Bot API и Telegram Mini App (WebApp).
Проверка криптографической подписи initData (HMAC-SHA256) без внешних зависимостей.
"""

import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from typing import Dict, Optional
from django.conf import settings


def verify_telegram_webapp_data(init_data: str, bot_token: Optional[str] = None, max_age_seconds: int = 86400) -> Optional[Dict]:
    """
    Проверить криптографическую подпись initData от Telegram Mini App (WebApp).
    Возвращает словарь с данными (включая распарсенный 'user'), если подпись верна, иначе None.
    """
    token = (bot_token if bot_token is not None else getattr(settings, 'TELEGRAM_BOT_TOKEN', '')).strip()
    if not token or not init_data:
        return None

    parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop('hash', None)
    if not received_hash:
        return None

    # Проверка свежести auth_date
    auth_date_str = parsed.get('auth_date', '0')
    try:
        auth_date = int(auth_date_str)
        if max_age_seconds and abs(time.time() - auth_date) > max_age_seconds:
            return None
    except ValueError:
        return None

    data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b'WebAppData', token.encode('utf-8'), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode('utf-8'), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    user_raw = parsed.get('user')
    if user_raw:
        try:
            parsed['user'] = json.loads(user_raw)
        except json.JSONDecodeError:
            return None
    return parsed


def telegram_api_call(method: str, payload: Optional[Dict] = None, bot_token: Optional[str] = None, timeout: int = 35) -> Dict:
    """Выполнить HTTP-запрос к Telegram Bot API (urllib, без сторонних библиотек)."""
    token = (bot_token if bot_token is not None else getattr(settings, 'TELEGRAM_BOT_TOKEN', '')).strip()
    if not token:
        raise RuntimeError('TELEGRAM_BOT_TOKEN не задан в настройках (.env).')

    url = f'https://api.telegram.org/bot{token}/{method}'
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'

    req = urllib.request.Request(url, data=data, headers=headers, method='POST' if data else 'GET')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))
