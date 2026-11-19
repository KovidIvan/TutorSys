#!/usr/bin/env bash
# ==============================================================================
# S.C.A.L.E — Скрипт автоматической установки и запуска на Linux-сервере (VDSina)
# Использование:
#   chmod +x deploy.sh
#   ./deploy.sh              # Установка, миграции, демо-данные и запуск на 0.0.0.0:8000
#   ./deploy.sh --systemd    # Установка и регистрация фонового сервиса systemd (24/7)
#   ./deploy.sh --no-seed    # Без загрузки демонстрационных данных
# ==============================================================================
set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

PORT="${PORT:-8000}"
LOAD_SEED=true
INSTALL_SYSTEMD=false
STOP_SERVICE=false
CLEAR_DB=false
RESET_DB=false

for arg in "$@"; do
    case "$arg" in
        --no-seed) LOAD_SEED=false ;;
        --systemd) INSTALL_SYSTEMD=true ;;
        --stop) STOP_SERVICE=true ;;
        --clear-db) CLEAR_DB=true ;;
        --reset-db) RESET_DB=true ;;
        --port=*) PORT="${arg#*=}" ;;
    esac
done

if [ "$STOP_SERVICE" = true ]; then
    echo "==> Остановка S.C.A.L.E..."
    if systemctl list-unit-files | grep -q "^scale.service"; then
        sudo systemctl stop scale.service || true
        sudo systemctl disable scale.service || true
        echo "Фоновый сервис systemd (scale.service) остановлен и отключён."
    fi
    if systemctl list-unit-files | grep -q "^scale-bot.service"; then
        sudo systemctl stop scale-bot.service || true
        sudo systemctl disable scale-bot.service || true
        echo "Фоновый сервис systemd (scale-bot.service) остановлен и отключён."
    fi
    pkill -f "manage.py run_telegram_bot" 2>/dev/null && echo "Запущенный процесс Telegram-бота завершён." || echo "Активных процессов Telegram-бота не найдено."
    pkill -f "manage.py runserver" 2>/dev/null && echo "Запущенный процесс runserver завершён." || echo "Активных процессов runserver не найдено."
    exit 0
fi

if [ "$CLEAR_DB" = true ]; then
    echo "==> Полная очистка базы данных (до пустого состояния)..."
    .venv/bin/python manage.py flush --noinput
    echo "База данных полностью очищена."
    exit 0
fi

if [ "$RESET_DB" = true ]; then
    echo "==> Сброс базы данных и повторная загрузка демо-данных..."
    .venv/bin/python manage.py flush --noinput
    .venv/bin/python manage.py shell -c "from core.seed import run; run()"
    echo "Демо-данные и пользователи (deputy_dean, head, teacher) успешно восстановлены."
    exit 0
fi

echo "==> [1/5] Проверка Python и виртуального окружения..."
if ! command -v python3 >/dev/null 2>&1; then
    echo "Python3 не найден. Устанавливаем через apt..."
    sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
fi

if [ ! -d ".venv" ]; then
    python3 -m venv .venv || {
        echo "Устанавливаем python3-venv..."
        sudo apt-get update && sudo apt-get install -y python3-venv
        python3 -m venv .venv
    }
fi

source .venv/bin/activate

echo "==> [2/5] Установка зависимостей..."
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -q

echo "==> [3/5] Настройка файла окружения (.env)..."
if [ ! -f ".env" ]; then
    RAND_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
    cat > .env <<EOF
SECRET_KEY=${RAND_KEY}
DEBUG=True
ALLOWED_HOSTS=*
DB_ENGINE=
DB_NAME=
DB_USER=
DB_PASSWORD=
DB_HOST=
DB_PORT=
EOF
    echo "Создан файл .env со сгенерированным SECRET_KEY и ALLOWED_HOSTS=*."
fi

echo "==> [4/5] Применение миграций и подготовка базы данных..."
python manage.py migrate --noinput

if [ "$LOAD_SEED" = true ]; then
    python manage.py shell -c "
from django.contrib.auth.models import User
from core.seed import run
if not User.objects.exists():
    print('Загрузка демонстрационных данных...')
    run()
else:
    print('База уже содержит пользователей (пропуск seed).')
"
fi

if [ "$INSTALL_SYSTEMD" = true ]; then
    echo "==> [5/5] Создание и запуск фонового сервиса systemd (scale.service)..."
    SERVICE_FILE="/etc/systemd/system/scale.service"
    CURRENT_USER="$(whoami)"
    sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=S.C.A.L.E Web Application
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/manage.py runserver 0.0.0.0:${PORT} --insecure
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now scale.service
    if grep -qE '^TELEGRAM_BOT_TOKEN=.{1,}' .env; then
        BOT_SERVICE_FILE="/etc/systemd/system/scale-bot.service"
        sudo tee "$BOT_SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=S.C.A.L.E Telegram Bot
After=network.target scale.service

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/manage.py run_telegram_bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
        sudo systemctl daemon-reload
        sudo systemctl enable --now scale-bot.service
        echo "Telegram-бот запущен как сервис systemd (scale-bot.service)."
    else
        echo "TELEGRAM_BOT_TOKEN не задан: сервис бота не запускается."
    fi
    echo ""
    echo "=================================================================="
    echo " S.C.A.L.E запущен в фоне как сервис systemd (scale.service)!"
    echo " Адрес: http://<IP_ВАШЕГО_СЕРВЕРА>:${PORT}/accounts/login/"
    echo " Управление сервисом:"
    echo "   sudo systemctl status scale"
    echo "   sudo systemctl restart scale"
    echo "   sudo journalctl -u scale -f"
    echo "   sudo systemctl status scale-bot"
    echo "=================================================================="
else
    echo "==> [5/5] Запуск сервера S.C.A.L.E на 0.0.0.0:${PORT}..."
    echo "Адрес: http://<IP_ВАШЕГО_СЕРВЕРА>:${PORT}/accounts/login/"
    if grep -qE '^TELEGRAM_BOT_TOKEN=.{1,}' .env; then
        python manage.py run_telegram_bot &
        BOT_PID=$!
        trap 'kill "$BOT_PID" 2>/dev/null || true' EXIT INT TERM
        echo "Telegram-бот запущен отдельным процессом (PID ${BOT_PID})."
    else
        echo "TELEGRAM_BOT_TOKEN не задан: Telegram-бот не запускается."
    fi
    python manage.py runserver "0.0.0.0:${PORT}" --insecure
fi
