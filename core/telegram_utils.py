"""
Утилиты для интеграции S.C.A.L.E с Telegram Bot API и Telegram Mini App (WebApp).
Проверка криптографической подписи initData (HMAC-SHA256) без внешних зависимостей.
"""

import hashlib
import hmac
import io
import json
import time
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from django.conf import settings
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill


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


def telegram_send_document(
    chat_id: int,
    filename: str,
    file_bytes: bytes,
    caption: str = '',
    bot_token: Optional[str] = None,
    timeout: int = 45,
) -> Dict:
    """Отправить файл (например, Excel .xlsx) в чат Telegram методом sendDocument (multipart/form-data)."""
    token = (bot_token if bot_token is not None else getattr(settings, 'TELEGRAM_BOT_TOKEN', '')).strip()
    if not token:
        raise RuntimeError('TELEGRAM_BOT_TOKEN не задан в настройках (.env).')

    boundary = f'----ScaleBotBoundary{uuid.uuid4().hex}'
    body = io.BytesIO()

    def _write_field(name: str, value: str):
        body.write(f'--{boundary}\r\n'.encode('utf-8'))
        body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode('utf-8'))
        body.write(f'{value}\r\n'.encode('utf-8'))

    _write_field('chat_id', str(chat_id))
    if caption:
        _write_field('caption', caption)
        _write_field('parse_mode', 'HTML')

    body.write(f'--{boundary}\r\n'.encode('utf-8'))
    body.write(
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode('utf-8')
    )
    body.write(
        b'Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n'
    )
    body.write(file_bytes)
    body.write(b'\r\n')
    body.write(f'--{boundary}--\r\n'.encode('utf-8'))

    url = f'https://api.telegram.org/bot{token}/sendDocument'
    req = urllib.request.Request(
        url,
        data=body.getvalue(),
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def build_xlsx_bytes(sheet_name: str, headers: List[str], rows: List[Tuple], extra_sheets=()) -> bytes:
    """Сформировать байты XLSX-файла в памяти для отправки через Telegram или HTTP."""
    header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True)

    def _fill(ws, hdrs, rws):
        ws.append(list(hdrs))
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
        for row in rws:
            safe_row = []
            for value in row:
                if isinstance(value, str) and value.lstrip(' \t\r\n')[:1] in ('=', '+', '-', '@'):
                    value = "'" + value
                safe_row.append(value)
            ws.append(safe_row)
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = ws.dimensions
        for column in ws.columns:
            col_letter = column[0].column_letter
            width = max((len(str(c.value or '')) for c in column), default=0)
            ws.column_dimensions[col_letter].width = min(max(width + 2, 12), 60)

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]
    _fill(ws, headers, rows)
    for extra_name, extra_headers, extra_rows in extra_sheets:
        _fill(wb.create_sheet(extra_name[:31]), extra_headers, extra_rows)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
