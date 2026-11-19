def site_link(path):
    from django.conf import settings

    site_url = settings.SITE_URL.rstrip('/')
    if not site_url:
        return None
    return f'{site_url}/{path.lstrip("/")}'
