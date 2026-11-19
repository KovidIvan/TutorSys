from django.core.management.base import BaseCommand

from core.tgbot.auth import unlink_all


class Command(BaseCommand):
    help = 'Отвязать все Telegram-аккаунты и удалить их очередь уведомлений.'

    def handle(self, *args, **options):
        count = unlink_all()
        self.stdout.write(self.style.SUCCESS(f'Удалено привязок Telegram: {count}.'))
