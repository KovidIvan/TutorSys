"""
URL-маршруты приложения core.
"""

from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    # Параметры расчёта
    path('settings/', views.settings_view, name='settings'),

    # Распределение нагрузки
    path('distribution/', views.distribution_view, name='distribution'),

    # Преподаватели кафедры
    path('teachers/', views.teachers_view, name='teachers'),

    # Нагрузка преподавателя
    path('workload/', views.workload_view, name='workload'),

    # Журнал
    path('journal/', views.journal_view, name='journal'),

    # Отчёт
    path('report/', views.report_view, name='report'),

    # Экспорт в Excel
    path('export/<str:export_type>/', views.export_excel_view, name='export_excel'),
]
