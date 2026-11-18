"""
URL-маршруты приложения core.
"""

from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    # Деканат (интерфейс заместителя декана)
    path('dean/', views.dean_dashboard_view, name='dean_dashboard'),
    path('dean/specialties/', views.specialties_view, name='specialties'),
    path('dean/departments/', views.departments_view, name='departments'),
    path('dean/groups/', views.groups_view, name='groups'),
    path('dean/curricula/', views.curricula_view, name='curricula'),
    path('dean/disciplines/', views.disciplines_view, name='disciplines'),

    # Параметры расчёта и импорт исходных данных
    path('settings/', views.settings_view, name='settings'),
    path('data-import/', views.data_import_view, name='data_import'),

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
