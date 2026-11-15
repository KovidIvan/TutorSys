"""
Главные URL-маршруты проекта nagruzka.
"""

from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('core.urls')),  # Подключить маршруты приложения core
    path('', RedirectView.as_view(url='/admin/', permanent=False)),  # Главная → админка
]
