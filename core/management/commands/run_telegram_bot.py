import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor

from core.tgbot.runner import run_bot


class Command(BaseCommand):
    help = 'Запустить Telegram-бот TutorSys в режиме long polling.'

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError('Бот не настроен: задайте TELEGRAM_BOT_TOKEN в переменных окружения.')
        self._wait_for_migrations()
        try:
            run_bot(settings.TELEGRAM_BOT_TOKEN)
        except Exception as exc:
            from aiogram.exceptions import TelegramConflictError
            if isinstance(exc, TelegramConflictError):
                raise CommandError('Другой процесс уже использует этот токен Telegram; оставьте только один экземпляр бота.') from exc
            raise

    def _wait_for_migrations(self):
        while True:
            connection = connections['default']
            executor = MigrationExecutor(connection)
            pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
            if not pending:
                return
            self.stdout.write('Ожидание применения миграций Django...')
            time.sleep(5)
