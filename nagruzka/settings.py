"""Django settings for the TutorSys demo project."""
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

SECRET_KEY = os.getenv('SECRET_KEY') or secrets.token_urlsafe(50)
DEBUG = os.getenv('DEBUG', 'True').strip().lower() in {'true', '1', 'yes'}
USE_HTTPS = os.getenv('USE_HTTPS', 'False').strip().lower() in {'true', '1', 'yes'}
SITE_URL = os.getenv('SITE_URL', '').strip().rstrip('/')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
TELEGRAM_BOT_USERNAME = os.getenv('TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')
TELEGRAM_OUTBOX_POLL_SECONDS = max(1, int(os.getenv('TELEGRAM_OUTBOX_POLL_SECONDS', '5')))
TELEGRAM_PRIVACY_MODE = os.getenv('TELEGRAM_PRIVACY_MODE', 'full').strip().lower()
if TELEGRAM_PRIVACY_MODE not in {'full', 'masked', 'minimal'}:
    raise ValueError('TELEGRAM_PRIVACY_MODE must be full, masked, or minimal')
TELEGRAM_REQUIRE_CONSENT = os.getenv('TELEGRAM_REQUIRE_CONSENT', 'True').strip().lower() in {'true', '1', 'yes'}
TELEGRAM_OUTBOX_KEEP_DAYS = max(1, int(os.getenv('TELEGRAM_OUTBOX_KEEP_DAYS', '7')))
ALLOWED_HOSTS = [host.strip() for host in os.getenv(
    'ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver'
).split(',') if host.strip()]

CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.getenv(
    'CSRF_TRUSTED_ORIGINS', ''
).split(',') if origin.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_htmx',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
    'core.middleware.NoStoreCacheMiddleware',
]

ROOT_URLCONF = 'nagruzka.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'nagruzka.wsgi.application'
ASGI_APPLICATION = 'nagruzka.asgi.application'

DB_ENGINE = os.getenv('DB_ENGINE', '').strip()
DB_NAME = os.getenv('DB_NAME', '').strip()
if DB_ENGINE and DB_NAME:
    DATABASES = {
        'default': {
            'ENGINE': DB_ENGINE,
            'NAME': DB_NAME,
            'USER': os.getenv('DB_USER', ''),
            'PASSWORD': os.getenv('DB_PASSWORD', ''),
            'HOST': os.getenv('DB_HOST', ''),
            'PORT': os.getenv('DB_PORT', ''),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': Path(os.getenv('SQLITE_PATH', BASE_DIR / 'db.sqlite3')),
            'OPTIONS': {'timeout': 20},
        }
    }

if DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3':
    DATABASES['default'].setdefault('OPTIONS', {})['timeout'] = 20

if USE_HTTPS:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'ru'
TIME_ZONE = 'Europe/Minsk'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'
