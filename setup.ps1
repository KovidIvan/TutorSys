# ==============================================================================
# S.C.A.L.E — Скрипт быстрой установки и запуска на Windows (PowerShell)
# Использование:
#   .\setup.ps1           # Установка, миграции, демо-данные и запуск сервера
#   .\setup.ps1 -NoSeed   # Запуск без заполнения демо-данными
# ==============================================================================
param (
    [switch]$NoSeed,
    [switch]$StopTelegramBot,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> [1/5] Проверка виртуального окружения (.venv)..." -ForegroundColor Cyan
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$BotPidFile = Join-Path $PSScriptRoot ".telegram-bot.pid"

if ($StopTelegramBot) {
    if (Test-Path $BotPidFile) {
        $BotPid = [int](Get-Content $BotPidFile -TotalCount 1)
        $BotProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$BotPid"
        if ($BotProcess -and $BotProcess.CommandLine -like "*$PSScriptRoot*manage.py run_telegram_bot*") {
            Stop-Process -Id $BotPid
            Remove-Item $BotPidFile
            Write-Host "Telegram-бот остановлен." -ForegroundColor Green
        }
        else {
            Write-Host "Записанный процесс бота не найден или не принадлежит этому проекту." -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "Файл PID Telegram-бота не найден." -ForegroundColor Yellow
    }
    exit 0
}

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
    & $Python manage.py shell -c "from django.contrib.auth.models import User; from core.seed import run; run() if not User.objects.exists() else None"
}

$BotConfigured = & $Python -c "from django.conf import settings; print(bool(settings.TELEGRAM_BOT_TOKEN))"
if ($BotConfigured -eq "True") {
    $BotProcess = Start-Process -FilePath $Python -ArgumentList @("manage.py", "run_telegram_bot") -WorkingDirectory $PSScriptRoot -PassThru
    Set-Content -Path $BotPidFile -Value $BotProcess.Id
    Write-Host "Telegram-бот запущен отдельным процессом (PID $($BotProcess.Id)). Остановка: .\setup.ps1 -StopTelegramBot" -ForegroundColor Green
}
else {
    Write-Host "TELEGRAM_BOT_TOKEN не задан: Telegram-бот не запускается." -ForegroundColor Yellow
}

Write-Host "==> [5/5] Запуск S.C.A.L.E на http://127.0.0.1:$Port/accounts/login/" -ForegroundColor Green
& $Python manage.py runserver "127.0.0.1:$Port"
