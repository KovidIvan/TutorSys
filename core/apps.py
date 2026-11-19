import os
import sys
import threading
from django.apps import AppConfig
from django.conf import settings


_bot_thread_started = False


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        global _bot_thread_started
        if _bot_thread_started:
            return

        # Не запускаем фонового бота при выполнении миграций, тестов, shell или отдельной команды runbot
        argv = sys.argv
        is_runserver = 'runserver' in argv
        is_wsgi_or_asgi = any(
             any(srv in arg for srv in ('gunicorn', 'uvicorn', 'daphne', 'uwsgi'))
            for arg in argv
        )
        if not (is_runserver or is_wsgi_or_asgi):
            return

        # При стандартном runserver с автоперезагрузкой запускаем бота только в рабочем дочернем процессе
        if is_runserver and '--noreload' not in argv and os.environ.get('RUN_MAIN') != 'true':
            return

        if os.environ.get('DISABLE_TELEGRAM_BOT', '').strip().lower() in {'1', 'true', 'yes'}:
            return

        token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '').strip()
        if not token:
            return

        _bot_thread_started = True

        def _run_telegram_bot():
            from django.core.management import call_command
            try:
                call_command('runbot')
            except Exception as exc:
                sys.stderr.write(f'[S.C.A.L.E Telegram Bot] Ошибка запуска фонового потока: {exc}\n')

        thread = threading.Thread(target=_run_telegram_bot, name='scale-telegram-bot', daemon=True)
        thread.start()
