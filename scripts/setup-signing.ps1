#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Настройка Tauri updater signing: перегенерация keypair, обновление pubkey
  в tauri.conf.json, подготовка base64 для GitHub Secret, тестовая локальная
  сборка, коммит изменений.

.DESCRIPTION
  Запускай ОДНОРАЗОВО когда связка ключ + пароль разъехалась (Tauri CLI
  ругается "failed to decode secret key"). После — повседневные релизы
  идут без вмешательства.

  Шаги:
    1. Перегенерирует %USERPROFILE%\.tauri\nastya.key (+ .pub) с паролем
    2. Конвертит .pub в base64, вставляет в tauri.conf.json → plugins.updater.pubkey
    3. Кладёт base64 приватного ключа в буфер обмена
    4. Ставит env-переменные и делает тестовую cargo tauri build
    5. Коммитит изменение tauri.conf.json

  После скрипта ты вручную:
    - Обновляешь GitHub Secret TAURI_SIGNING_PRIVATE_KEY (Ctrl+V из буфера)
    - TAURI_SIGNING_PRIVATE_KEY_PASSWORD проверяешь (дефолт 1231)
    - git push + bump версии (например через скилл `нр`)

.PARAMETER Password
  Пароль для нового приватного ключа. Дефолт: 1231.

.PARAMETER KeyPath
  Путь к файлу приватного ключа. Дефолт: %USERPROFILE%\.tauri\nastya.key

.PARAMETER NoBuild
  Пропустить тестовую локальную сборку (быстрее, но не валидирует связку).

.PARAMETER NoCommit
  Не коммитить обновлённый tauri.conf.json (ты сам).

.EXAMPLE
  .\scripts\setup-signing.ps1
    # Полный путь: keypair → pubkey в конфиге → билд → commit

.EXAMPLE
  .\scripts\setup-signing.ps1 -NoBuild
    # Быстрая перегенерация без локального билда
#>
[CmdletBinding()]
param(
    [string]$Password = "1231",
    [string]$KeyPath = "$env:USERPROFILE\.tauri\nastya.key",
    [switch]$NoBuild,
    [switch]$NoCommit
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$msg)
    Write-Host ""
    Write-Host "═══ $msg ═══" -ForegroundColor Cyan
}

function Fail {
    param([string]$msg)
    Write-Host "❌ $msg" -ForegroundColor Red
    exit 1
}

function Assert-Tool {
    param([string]$cmd, [string]$hint)
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Fail "Не найдена команда '$cmd'. $hint"
    }
}

# 0. Preflight
Assert-Tool cargo "Установи rustup-init и dev-shell (PortableBuildTools)."
Assert-Tool git "Установи git."

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$confPath = Join-Path $projectRoot "src-tauri\tauri.conf.json"
if (-not (Test-Path $confPath)) {
    Fail "Не найден tauri.conf.json по пути $confPath"
}

Write-Step "Проект: $projectRoot"
Write-Host "Ключ:     $KeyPath"
Write-Host "Пароль:   $Password"

# 1. Генерация keypair
Write-Step "Генерация keypair (cargo tauri signer generate)"
$keyDir = Split-Path -Parent $KeyPath
if (-not (Test-Path $keyDir)) {
    New-Item -ItemType Directory -Path $keyDir -Force | Out-Null
}

$genOutput = cargo tauri signer generate --write-keys $KeyPath --password $Password --force 2>&1 | Out-String
Write-Host $genOutput
if ($LASTEXITCODE -ne 0) {
    Fail "cargo tauri signer generate упал (код $LASTEXITCODE)"
}

$pubPath = "$KeyPath.pub"
if (-not (Test-Path $pubPath)) {
    Fail "Публичный ключ не создан по пути $pubPath"
}

# Файл .pub уже содержит base64-кодированный minisign-текст. Tauri сам декодирует
# значение pubkey из конфига, так что в tauri.conf.json должно лежать СОДЕРЖИМОЕ
# файла как есть — иначе получаем двойной base64 и парсер падает с
# "Missing encoded key in public key".
$pubKeyContent = ([IO.File]::ReadAllText($pubPath)).Trim()
Write-Host "✓ Публичный ключ: $($pubKeyContent.Substring(0, [Math]::Min(60, $pubKeyContent.Length)))..."

# 2. Обновление pubkey в tauri.conf.json
Write-Step "Обновляю pubkey в tauri.conf.json"
$conf = Get-Content $confPath -Raw | ConvertFrom-Json
if (-not $conf.plugins.updater) {
    Fail "В tauri.conf.json отсутствует plugins.updater — проверь конфиг"
}
$oldPub = $conf.plugins.updater.pubkey
$conf.plugins.updater.pubkey = $pubKeyContent

# В PowerShell 5.1 -Encoding utf8 = UTF-8 с BOM, а Tauri JSON-парсер падает на BOM
# ("expected value at line 1 column 1"). Пишем байтами через .NET → чистый UTF-8.
$json = ($conf | ConvertTo-Json -Depth 20) + "`n"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllBytes($confPath, $utf8NoBom.GetBytes($json))

Write-Host "✓ pubkey заменён"
Write-Host "  старый: $($oldPub.Substring(0, [Math]::Min(40, $oldPub.Length)))..."
Write-Host "  новый:  $($pubKeyContent.Substring(0, [Math]::Min(40, $pubKeyContent.Length)))..."

# 3. Приватник → буфер (как есть, НЕ base64)
# Файл nastya.key уже содержит base64-кодированный minisign-текст. Tauri v2 сам
# декодирует base64 из env-переменной, поэтому передавать надо СОДЕРЖИМОЕ файла,
# а не ToBase64String(ReadAllBytes) — иначе получаем двойной base64 и парсер
# minisign падает с "Missing encoded key in secret key".
Write-Step "Приватник → буфер обмена"
$privContent = [IO.File]::ReadAllText($KeyPath)
Set-Clipboard -Value $privContent
Write-Host "✓ В буфере, длина $($privContent.Length) символов"
Write-Host ""
Write-Host "→ Обнови GitHub Secret:" -ForegroundColor Yellow
Write-Host "  https://github.com/ativubise657-boop/nastyaorchestrator/settings/secrets/actions"
Write-Host "  TAURI_SIGNING_PRIVATE_KEY → Update → Ctrl+V → Save"
Write-Host "  TAURI_SIGNING_PRIVATE_KEY_PASSWORD → $Password"

# 4. Тестовая сборка
if (-not $NoBuild) {
    Write-Step "Тестовая локальная сборка (cargo tauri build)"
    Write-Host "Проверяем что связка keypair + пароль работает..." -ForegroundColor Yellow

    $env:TAURI_SIGNING_PRIVATE_KEY = $privContent
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = $Password

    Push-Location (Join-Path $projectRoot "src-tauri")
    try {
        # $ErrorActionPreference=Stop + перенаправление stderr ломаются на cargo info-
        # сообщениях. Отключаем Stop на время нативного вызова, проверяем по $LASTEXITCODE.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        cargo tauri build
        $ErrorActionPreference = $prevEAP
        if ($LASTEXITCODE -ne 0) {
            Fail "Локальный билд упал (код $LASTEXITCODE). Проверь что keypair + пароль совпадают."
        }
    } finally {
        Pop-Location
    }

    # Проверка updater-артефактов
    $bundleDir = Join-Path $projectRoot "src-tauri\target\release\bundle\nsis"
    $zipExists = Get-ChildItem -Path $bundleDir -Filter "*.nsis.zip" -ErrorAction SilentlyContinue | Select-Object -First 1
    $sigExists = Get-ChildItem -Path $bundleDir -Filter "*.nsis.zip.sig" -ErrorAction SilentlyContinue | Select-Object -First 1

    if ($zipExists -and $sigExists) {
        Write-Host "✓ Updater-артефакты сгенерированы:" -ForegroundColor Green
        Write-Host "  $($zipExists.Name)"
        Write-Host "  $($sigExists.Name)"
    } else {
        Fail "Updater-артефакты отсутствуют несмотря на успешный билд. Проверь createUpdaterArtifacts=true в tauri.conf.json."
    }
} else {
    Write-Host "(пропустил тестовую сборку по -NoBuild)" -ForegroundColor DarkGray
}

# 5. Git commit
if (-not $NoCommit) {
    Write-Step "Коммит обновлённого tauri.conf.json"
    git add src-tauri/tauri.conf.json
    $status = git status --porcelain src-tauri/tauri.conf.json
    if (-not $status) {
        Write-Host "(pubkey не изменился — нечего коммитить)" -ForegroundColor DarkGray
    } else {
        git commit -m "tauri: перегенерирован signing keypair"
        Write-Host "✓ Commit сделан. Дальше:"
        Write-Host "  1. Убедись что GitHub Secret TAURI_SIGNING_PRIVATE_KEY обновлён (шаг 3)"
        Write-Host "  2. git push origin master"
        Write-Host "  3. bump версии + tag (или скилл `нр`)"
    }
} else {
    Write-Host "(пропустил коммит по -NoCommit)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "═══ Готово ═══" -ForegroundColor Green
