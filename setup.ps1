# ==============================================================================
# S.C.A.L.E — Скрипт быстрой установки и запуска на Windows (PowerShell)
# Использование:
#   .\setup.ps1           # Установка, миграции, демо-данные и запуск сервера
#   .\setup.ps1 -NoSeed   # Запуск без заполнения демо-данными
# ==============================================================================
param (
    [switch]$NoSeed,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> [1/5] Проверка виртуального окружения (.venv)..." -ForegroundColor Cyan
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

Write-Host "==> [2/5] Установка зависимостей..." -ForegroundColor Cyan
& $Python -m pip install --upgrade pip -q
& $Python -m pip install -r requirements.txt -q

Write-Host "==> [3/5] Проверка конфигурации (.env)..." -ForegroundColor Cyan
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Создан файл .env из .env.example" -ForegroundColor Green
}

Write-Host "==> [4/5] Применение миграций..." -ForegroundColor Cyan
& $Python manage.py migrate --noinput

if (-not $NoSeed) {
    $HasUsers = & $Python manage.py shell -c "from django.contrib.auth.models import User; print(User.objects.exists())"
    if ($HasUsers.Trim() -eq "False") {
        Write-Host "Загрузка демонстрационных данных..." -ForegroundColor Cyan
        & $Python manage.py shell -c "from core.seed import run; run()"
    }
}

Write-Host "==> [5/5] Запуск S.C.A.L.E на http://127.0.0.1:$Port/accounts/login/" -ForegroundColor Green
& $Python manage.py runserver "127.0.0.1:$Port"
