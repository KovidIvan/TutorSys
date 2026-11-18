"""
Главные URL-маршруты проекта nagruzka.
"""

from django.contrib import admin
from django.urls import path, include
from core import views

urlpatterns = [
    path('', views.home_view, name='home'),
    path('admin/', admin.site.urls),
    path('accounts/login/', views.login_view, name='login'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', include('core.urls')),
]
