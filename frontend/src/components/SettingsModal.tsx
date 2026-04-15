import { useEffect, useState } from 'react'

interface ProxySettings {
  enabled: boolean
  host: string
  port: number
  user: string
  password: string
  no_proxy: string
}

const EMPTY: ProxySettings = {
  enabled: true,
  host: '',
  port: 3128,
  user: '',
  password: '',
  no_proxy: 'localhost,127.0.0.1,::1',
}

const SANDBOX_LABELS: Record<string, string> = {
  'workspace-write': 'Безопасный (только папка проекта + документы)',
  'read-only': 'Только чтение (без изменений файлов)',
  'danger-full-access': 'Полный доступ (по всему компьютеру) ⚠',
}

export function SettingsModal({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<ProxySettings>(EMPTY)
  const [sandbox, setSandbox] = useState<string>('danger-full-access')
  const [sandboxSaving, setSandboxSaving] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [savedAt, setSavedAt] = useState<number | null>(null)

  // AITunnel API key state
  const [aitunnelMasked, setAitunnelMasked] = useState<string>('')
  const [aitunnelSource, setAitunnelSource] = useState<string>('')
  const [aitunnelInput, setAitunnelInput] = useState<string>('')
  const [aitunnelSaving, setAitunnelSaving] = useState(false)

  useEffect(() => {
    fetch('/api/settings/proxy')
      .then((r) => r.json())
      .then((data) => setSettings(data))
      .catch(() => {})
      .finally(() => setLoading(false))
    fetch('/api/settings/sandbox')
      .then((r) => r.json())
      .then((data) => data?.mode && setSandbox(data.mode))
      .catch(() => {})
    fetch('/api/settings/aitunnel_key')
      .then((r) => r.json())
      .then((data) => {
        setAitunnelMasked(data?.masked || '')
        setAitunnelSource(data?.source || '')
      })
      .catch(() => {})
  }, [])

  const saveAitunnelKey = async (apiKey: string) => {
    setAitunnelSaving(true)
    try {
      const res = await fetch('/api/settings/aitunnel_key', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: apiKey }),
      })
      const data = await res.json()
      setAitunnelMasked(data?.masked || '')
      setAitunnelSource(apiKey.trim() ? 'db' : (data?.present ? 'env' : ''))
      setAitunnelInput('')
    } finally {
      setAitunnelSaving(false)
    }
  }

  const saveSandbox = async (mode: string) => {
    setSandbox(mode)
    setSandboxSaving(true)
    try {
      await fetch('/api/settings/sandbox', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      })
    } finally {
      setSandboxSaving(false)
    }
  }

  const update = <K extends keyof ProxySettings>(key: K, value: ProxySettings[K]) => {
    setSettings((s) => ({ ...s, [key]: value }))
    setSavedAt(null)
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const res = await fetch('/api/settings/proxy', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      })
      if (res.ok) setSavedAt(Date.now())
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await fetch('/api/settings/proxy/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      })
      const data = await res.json()
      setTestResult({ ok: data.ok, message: data.message })
    } catch (e: any) {
      setTestResult({ ok: false, message: String(e?.message ?? e) })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="integrations-overlay" onClick={onClose}>
      <div className="integrations-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 520 }}>
        <div className="integrations-modal__header">
          <h3>Настройки → Прокси</h3>
          <button className="integrations-modal__close" onClick={onClose}>×</button>
        </div>

        {loading ? (
          <div style={{ padding: 20 }}>Загрузка…</div>
        ) : (
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="checkbox"
                checked={settings.enabled}
                onChange={(e) => update('enabled', e.target.checked)}
              />
              <span>Использовать прокси для всех исходящих соединений</span>
            </label>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 100px', gap: 8 }}>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ fontSize: 12, opacity: 0.7 }}>Host</span>
                <input
                  type="text"
                  value={settings.host}
                  onChange={(e) => update('host', e.target.value)}
                  disabled={!settings.enabled}
                />
              </label>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ fontSize: 12, opacity: 0.7 }}>Port</span>
                <input
                  type="number"
                  value={settings.port}
                  onChange={(e) => update('port', parseInt(e.target.value) || 0)}
                  disabled={!settings.enabled}
                />
              </label>
            </div>

            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 12, opacity: 0.7 }}>Пользователь</span>
              <input
                type="text"
                value={settings.user}
                onChange={(e) => update('user', e.target.value)}
                disabled={!settings.enabled}
              />
            </label>

            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 12, opacity: 0.7 }}>Пароль (хранится открытым текстом)</span>
              <input
                type="text"
                value={settings.password}
                onChange={(e) => update('password', e.target.value)}
                disabled={!settings.enabled}
              />
            </label>

            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 12, opacity: 0.7 }}>NO_PROXY (через запятую)</span>
              <textarea
                rows={2}
                value={settings.no_proxy}
                onChange={(e) => update('no_proxy', e.target.value)}
              />
            </label>

            {testResult && (
              <div
                style={{
                  padding: 8,
                  borderRadius: 4,
                  background: testResult.ok ? '#1f3f1f' : '#3f1f1f',
                  color: testResult.ok ? '#9fef9f' : '#ef9f9f',
                  fontSize: 13,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {testResult.ok ? '✓ ' : '✗ '}
                {testResult.message}
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 8 }}>
              <button onClick={handleTest} disabled={testing}>
                {testing ? 'Проверка…' : 'Тест'}
              </button>
              <button onClick={handleSave} disabled={saving} style={{ fontWeight: 600 }}>
                {saving ? 'Сохранение…' : savedAt ? 'Сохранено ✓' : 'Сохранить'}
              </button>
            </div>

            <div style={{ fontSize: 11, opacity: 0.6, marginTop: 8 }}>
              Изменения применяются мгновенно к новым исходящим запросам backend.
              Worker подхватит при следующем перезапуске.
            </div>

            {/* Секция: AITunnel API key (для Gemini PDF OCR) */}
            <div style={{ marginTop: 20, paddingTop: 16, borderTop: '1px solid rgba(255,255,255,0.1)' }}>
              <h4 style={{ margin: '0 0 8px 0', fontSize: 14 }}>AITunnel API key</h4>
              <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 8 }}>
                Используется для Gemini 2.5 Flash — OCR сканированных PDF когда markitdown/pdfminer не справляются.
                {aitunnelMasked
                  ? (
                    <> Сейчас активен ключ <code style={{ background: 'rgba(255,255,255,0.08)', padding: '1px 4px', borderRadius: 3 }}>{aitunnelMasked}</code> (источник: {aitunnelSource === 'db' ? 'приложение' : aitunnelSource === 'env' ? '.env файл' : '—'}).</>
                  )
                  : <> Ключ не задан — OCR-уровень работать не будет.</>
                }
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  type="password"
                  placeholder="sk-aitunnel-..."
                  value={aitunnelInput}
                  onChange={(e) => setAitunnelInput(e.target.value)}
                  disabled={aitunnelSaving}
                  style={{ flex: 1 }}
                />
                <button
                  onClick={() => saveAitunnelKey(aitunnelInput)}
                  disabled={aitunnelSaving || !aitunnelInput.trim()}
                >
                  {aitunnelSaving ? 'Сохранение…' : 'Сохранить'}
                </button>
                {aitunnelSource === 'db' && (
                  <button
                    onClick={() => saveAitunnelKey('')}
                    disabled={aitunnelSaving}
                    title="Удалить пользовательский ключ, вернуться к .env"
                  >
                    Сбросить
                  </button>
                )}
              </div>
              <div style={{ fontSize: 11, opacity: 0.5, marginTop: 6 }}>
                Ключ применяется мгновенно — следующий upload PDF сможет использовать его.
                Хранится в локальной БД приложения.
              </div>
            </div>

            {/* Секция: Доступ ассистента к файлам */}
            <div style={{ marginTop: 20, paddingTop: 16, borderTop: '1px solid rgba(255,255,255,0.1)' }}>
              <h4 style={{ margin: '0 0 8px 0', fontSize: 14 }}>Доступ ассистента к файлам</h4>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {Object.entries(SANDBOX_LABELS).map(([value, label]) => (
                  <label key={value} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                    <input
                      type="radio"
                      name="sandbox"
                      value={value}
                      checked={sandbox === value}
                      onChange={() => saveSandbox(value)}
                      disabled={sandboxSaving}
                    />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
              <div style={{ fontSize: 11, opacity: 0.6, marginTop: 6 }}>
                «Полный доступ» позволит ассистенту искать и менять файлы по всему компьютеру.
                Используй осознанно — Codex может ошибиться и перезаписать важное.
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
