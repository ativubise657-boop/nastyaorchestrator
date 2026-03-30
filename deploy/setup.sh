#!/bin/bash
# Первоначальная настройка Nastya Orchestrator на сервере 185.93.111.88
# Запускать от root прямо на сервере: bash /opt/nastya-orch/deploy/setup.sh
# Предполагается, что файлы проекта уже скопированы в /opt/nastya-orch/
set -e

REMOTE="/opt/nastya-orch"
DOMAIN="nr.gnld.ru"
SERVICE_NAME="nastya-orch"
NGINX_CONF="/etc/nginx/sites-enabled/${DOMAIN}"
HTPASSWD_FILE="/etc/nginx/.htpasswd-nastya"

# Проверяем что запущено от root
if [ "$(id -u)" -ne 0 ]; then
    echo "✗ Скрипт должен выполняться от root"
    exit 1
fi

# Проверяем что файлы проекта на месте
if [ ! -f "${REMOTE}/requirements.txt" ]; then
    echo "✗ Файлы проекта не найдены в ${REMOTE}/"
    echo "  Сначала скопируйте проект: scp -r ... root@185.93.111.88:${REMOTE}/"
    exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Настройка Nastya Orchestrator"
echo "  Домен: ${DOMAIN}"
echo "  Путь: ${REMOTE}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ─── Зависимости ОС ──────────────────────────────────────────────────────────
echo ""
echo "▶ [1/8] Установка системных зависимостей..."
apt-get update -q
# python3-venv — для создания venv
# apache2-utils — для htpasswd
# certbot + python3-certbot-nginx — для SSL
apt-get install -y -q python3-venv python3-pip apache2-utils certbot python3-certbot-nginx
echo "  ✓ Зависимости установлены"

# ─── Директории ──────────────────────────────────────────────────────────────
echo ""
echo "▶ [2/8] Создание директорий..."
mkdir -p "${REMOTE}/"{backend,frontend/dist,config,data/documents,worker,deploy}
echo "  ✓ Директории созданы"

# ─── Python venv ─────────────────────────────────────────────────────────────
echo ""
echo "▶ [3/8] Python virtualenv..."
if [ ! -d "${REMOTE}/venv" ]; then
    python3 -m venv "${REMOTE}/venv"
    echo "  ✓ venv создан"
else
    echo "  ✓ venv уже существует"
fi
"${REMOTE}/venv/bin/pip" install --upgrade pip -q
"${REMOTE}/venv/bin/pip" install -r "${REMOTE}/requirements.txt" -q
echo "  ✓ Python-зависимости установлены"

# ─── .env ────────────────────────────────────────────────────────────────────
echo ""
echo "▶ [4/8] Файл .env..."
if [ ! -f "${REMOTE}/.env" ]; then
    # Генерируем случайный 64-символьный токен для worker
    WORKER_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    cat > "${REMOTE}/.env" << EOF
# Токен для аутентификации воркеров (Bearer token в заголовке Authorization)
WORKER_TOKEN=${WORKER_TOKEN}

# Директории данных
DATA_DIR=${REMOTE}/data
DOCUMENTS_DIR=${REMOTE}/data/documents
EOF
    echo "  ✓ .env создан (WORKER_TOKEN сгенерирован)"
    echo "  ⚠  Сохрани токен: ${WORKER_TOKEN}"
else
    echo "  ✓ .env уже существует — пропускаем"
fi

# ─── Права файловой системы ──────────────────────────────────────────────────
echo ""
echo "▶ [5/8] Права доступа..."
chown -R www-data:www-data "${REMOTE}"
# deploy/ и setup.sh сам — только root читает (там SSH-ключ не хранится, но на всякий)
chmod 750 "${REMOTE}/deploy/"
chmod +x "${REMOTE}/deploy/deploy.sh" "${REMOTE}/deploy/setup.sh"
echo "  ✓ Права установлены (owner: www-data)"

# ─── systemd сервис ──────────────────────────────────────────────────────────
echo ""
echo "▶ [6/8] Systemd сервис..."
cp "${REMOTE}/deploy/nastya-orchestrator.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
echo "  ✓ Сервис ${SERVICE_NAME} зарегистрирован и включён"

# ─── nginx ───────────────────────────────────────────────────────────────────
echo ""
echo "▶ [7/8] nginx..."

# Проверяем нет ли уже конфига
if [ -f "${NGINX_CONF}" ]; then
    echo "  ⚠  ${NGINX_CONF} уже существует, создаём резервную копию..."
    cp "${NGINX_CONF}" "${NGINX_CONF}.bak.$(date +%Y%m%d-%H%M%S)"
fi

cp "${REMOTE}/deploy/nginx.conf" "${NGINX_CONF}"

# Проверяем конфиг перед reload — критично!
nginx -t
echo "  ✓ nginx -t прошёл"
systemctl reload nginx
echo "  ✓ nginx перезагружен"

# ─── SSL (certbot) ───────────────────────────────────────────────────────────
echo ""
echo "  Получение SSL-сертификата для ${DOMAIN}..."
# --nginx — certbot сам правит nginx.conf (добавит ssl_certificate и т.д.)
# --non-interactive — без вопросов
# --redirect — принудительный HTTPS (у нас уже есть редирект в конфиге, но пусть подтвердит)
certbot --nginx \
    -d "${DOMAIN}" \
    --non-interactive \
    --agree-tos \
    -m admin@geniled.ru \
    --redirect
echo "  ✓ SSL-сертификат получен"

# Проверяем что SSL не сломал другие сайты
echo "  Проверка SSL соседних сайтов..."
GENILED_CERT=$(openssl s_client -connect 185.93.111.88:443 -servername geniled.ru 2>/dev/null | grep "subject=" | head -1 || echo "не проверен")
echo "  geniled.ru cert: ${GENILED_CERT}"

# ─── Basic Auth ──────────────────────────────────────────────────────────────
echo ""
echo "▶ [8/8] Basic Auth..."
if [ -f "${HTPASSWD_FILE}" ]; then
    echo "  ✓ ${HTPASSWD_FILE} уже существует — пропускаем"
else
    echo "  Создаём пользователя 'nastya' для Basic Auth:"
    htpasswd -c "${HTPASSWD_FILE}" nastya
    echo "  ✓ Basic Auth настроен"
fi

# ─── Запуск ──────────────────────────────────────────────────────────────────
echo ""
echo "▶ Запуск сервиса..."
systemctl start "${SERVICE_NAME}"
sleep 2

STATUS=$(systemctl is-active "${SERVICE_NAME}" || echo "failed")
if [ "${STATUS}" = "active" ]; then
    echo "  ✓ ${SERVICE_NAME}: active"
else
    echo "  ✗ ${SERVICE_NAME}: ${STATUS}"
    echo ""
    journalctl -u "${SERVICE_NAME}" -n 30 --no-pager
    exit 1
fi

# Проверяем health
HEALTH=$(curl -sf "http://127.0.0.1:8781/api/system/health" 2>/dev/null || echo "нет ответа")
echo "  Health: ${HEALTH}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ Настройка завершена!"
echo ""
echo "  URL:    https://${DOMAIN}"
echo "  Логи:   journalctl -u ${SERVICE_NAME} -f"
echo "  Статус: systemctl status ${SERVICE_NAME}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
