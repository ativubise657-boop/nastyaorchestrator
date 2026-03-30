#!/bin/bash
# Деплой Nastya Orchestrator с WSL на сервер 185.93.111.88
# Использование: ./deploy/deploy.sh [--skip-frontend] [--skip-backend]
set -e

# ─── Настройки ───────────────────────────────────────────────────────────────
SERVER="root@185.93.111.88"
KEY="${HOME}/.ssh/ed25519_key"
SSH="ssh -o StrictHostKeyChecking=no -i ${KEY} ${SERVER}"
SCP="scp -o StrictHostKeyChecking=no -i ${KEY}"
REMOTE="/opt/nastya-orch"
# Абсолютный путь к корню проекта (WSL)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

# Флаги из аргументов
SKIP_FRONTEND=false
SKIP_BACKEND=false
for arg in "$@"; do
    case $arg in
        --skip-frontend) SKIP_FRONTEND=true ;;
        --skip-backend)  SKIP_BACKEND=true ;;
        --help)
            echo "Использование: $0 [--skip-frontend] [--skip-backend]"
            exit 0
            ;;
    esac
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Деплой Nastya Orchestrator → ${SERVER}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ─── Backend ─────────────────────────────────────────────────────────────────
if [ "${SKIP_BACKEND}" = false ]; then
    echo ""
    echo "▶ [1/4] Деплой backend..."
    $SCP -r \
        "${PROJECT_DIR}/backend/" \
        "${PROJECT_DIR}/worker/" \
        "${PROJECT_DIR}/config/" \
        "${PROJECT_DIR}/requirements.txt" \
        "${SERVER}:${REMOTE}/"
    echo "  ✓ backend/ worker/ config/ requirements.txt скопированы"
else
    echo "▶ [1/4] Backend — пропущен (--skip-backend)"
fi

# ─── Frontend ────────────────────────────────────────────────────────────────
if [ "${SKIP_FRONTEND}" = false ]; then
    echo ""
    echo "▶ [2/4] Build frontend..."
    cd "${PROJECT_DIR}/frontend"
    # Проверяем наличие node_modules
    if [ ! -d "node_modules" ]; then
        echo "  node_modules не найден, запускаем npm install..."
        npm install
    fi
    npm run build
    echo "  ✓ Build успешен"
    cd "${PROJECT_DIR}"

    echo ""
    echo "▶ [3/4] Деплой frontend/dist → ${REMOTE}/frontend/..."
    $SCP -r "${PROJECT_DIR}/frontend/dist/" "${SERVER}:${REMOTE}/frontend/"
    echo "  ✓ frontend/dist скопирован"
else
    echo "▶ [2/4] Frontend build — пропущен (--skip-frontend)"
    echo "▶ [3/4] Frontend деплой — пропущен (--skip-frontend)"
fi

# ─── Перезапуск сервиса ──────────────────────────────────────────────────────
echo ""
echo "▶ [4/4] Установка зависимостей и перезапуск сервиса..."
$SSH bash -s << 'REMOTE_SCRIPT'
set -e
cd /opt/nastya-orch

# Устанавливаем/обновляем Python-зависимости
echo "  pip install..."
./venv/bin/pip install -r requirements.txt -q

# Перезапускаем сервис
systemctl restart nastya-orch
echo "  ✓ nastya-orch перезапущен"
REMOTE_SCRIPT

# ─── Проверка ────────────────────────────────────────────────────────────────
echo ""
echo "▶ Проверка состояния..."
sleep 2

# Проверяем статус systemd
STATUS=$($SSH "systemctl is-active nastya-orch" 2>/dev/null || echo "failed")
if [ "${STATUS}" = "active" ]; then
    echo "  ✓ systemd: active"
else
    echo "  ✗ systemd: ${STATUS}"
    echo ""
    echo "  Последние логи:"
    $SSH "journalctl -u nastya-orch -n 20 --no-pager"
    exit 1
fi

# Проверяем health endpoint
HEALTH=$($SSH "curl -sf http://127.0.0.1:8781/api/system/health 2>/dev/null" || echo "error")
echo "  Health: ${HEALTH}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ Деплой завершён → https://nr.gnld.ru"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
