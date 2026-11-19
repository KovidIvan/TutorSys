from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.models import TelegramLink
from core.tgbot.auth import create_link_code


@login_required
@require_http_methods(['GET', 'POST'])
def telegram_account_view(request):
    link = TelegramLink.objects.filter(user=request.user).first()
    generated_code = None
    bot_url = None
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create_code':
            if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_BOT_USERNAME:
                messages.error(request, 'Telegram-бот не настроен.')
            elif settings.USE_HTTPS and not request.is_secure():
                messages.error(request, 'Код привязки можно создать только через защищённое HTTPS-соединение.')
            else:
                generated_code = create_link_code(request.user)
                bot_url = f'https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start=link_{generated_code}'
        elif action == 'unlink':
            TelegramLink.objects.filter(user=request.user).delete()
            messages.success(request, 'Telegram-аккаунт отвязан.')
            return redirect('core:telegram_account')
    return render(request, 'core/telegram.html', {
        'telegram_link': link,
        'telegram_bot_configured': bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_USERNAME),
        'generated_code': generated_code,
        'bot_url': bot_url,
    })
