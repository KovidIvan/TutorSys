"""Middleware для запрета кэширования защищённых страниц и работы за HTTPS-прокси / в Telegram Mini App."""


class DynamicOriginAndNoStoreMiddleware:
    """
    1. Разрешает CSRF-проверку Origin для запросов за обратным прокси (Nginx, Cloudflare, ngrok, sslip.io),
       когда домен совпадает с заголовком Host запроса.
    2. Запрещает кэширование страниц браузером (включая форму входа с CSRF-токеном).
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        origin = request.META.get('HTTP_ORIGIN')
        host = request.get_host()
        if origin and host:
            expected_https = f'https://{host}'
            expected_http = f'http://{host}'
            trusted = getattr(settings, 'CSRF_TRUSTED_ORIGINS', [])
            for candidate in (expected_https, expected_http):
                if origin == candidate and candidate not in trusted:
                    trusted.append(candidate)

        response = self.get_response(request)
        if not request.path.startswith('/static/'):
            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
        return response


NoStoreCacheMiddleware = DynamicOriginAndNoStoreMiddleware
