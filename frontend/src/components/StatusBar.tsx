import { useState } from 'react'
import { useWorkerOnline, useQueueSize } from '../stores'
import './StatusBar.css'

const API_BASE = import.meta.env.VITE_API_URL || ''

// Список интеграций/вебхуков — статус подключения
const INTEGRATIONS = [
  { name: 'Bitrix24 CRM', desc: 'Поиск компаний, контактов, сделок', status: 'active' as const, icon: '🔗' },
  { name: 'Б24 входящие вебхуки', desc: 'POST /api/webhooks/b24 — приём событий', status: 'active' as const, icon: '📥' },
  { name: 'GitHub API', desc: 'Read-only доступ к репозиториям проектов', status: 'active' as const, icon: '🐙' },
  { name: 'Документы', desc: 'Загрузка и анализ файлов (CSV, PDF, TXT)', status: 'active' as const, icon: '📄' },
  { name: 'RAG / FTS5 поиск', desc: 'Семантический поиск по документам', status: 'planned' as const, icon: '🔍' },
  { name: 'Б24 задачи', desc: 'Создание/обновление задач в Битрикс24', status: 'planned' as const, icon: '✅' },
  { name: 'Б24 уведомления', desc: 'Отправка уведомлений через Б24', status: 'planned' as const, icon: '🔔' },
  { name: 'Email', desc: 'Отправка писем через SMTP', status: 'planned' as const, icon: '📧' },
]

function IntegrationsModal({ onClose }: { onClose: () => void }) {
  const active = INTEGRATIONS.filter(i => i.status === 'active')
  const planned = INTEGRATIONS.filter(i => i.status === 'planned')

  return (
    <div className="integrations-overlay" onClick={onClose}>
      <div className="integrations-modal" onClick={e => e.stopPropagation()}>
        <div className="integrations-modal__header">
          <h3>Интеграции и вебхуки</h3>
          <button className="integrations-modal__close" onClick={onClose}>×</button>
        </div>

        <div className="integrations-modal__section">
          <div className="integrations-modal__section-title">
            <span className="integrations-modal__dot integrations-modal__dot--active" />
            Подключено ({active.length})
          </div>
          {active.map(i => (
            <div key={i.name} className="integrations-modal__item">
              <span className="integrations-modal__icon">{i.icon}</span>
              <div>
                <div className="integrations-modal__name">{i.name}</div>
                <div className="integrations-modal__desc">{i.desc}</div>
              </div>
            </div>
          ))}
        </div>

        <div className="integrations-modal__section">
          <div className="integrations-modal__section-title">
            <span className="integrations-modal__dot integrations-modal__dot--planned" />
            В планах ({planned.length})
          </div>
          {planned.map(i => (
            <div key={i.name} className="integrations-modal__item integrations-modal__item--planned">
              <span className="integrations-modal__icon">{i.icon}</span>
              <div>
                <div className="integrations-modal__name">{i.name}</div>
                <div className="integrations-modal__desc">{i.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

interface UpdateStep {
  step: string
  ok: boolean
  output: string
}

interface UpdateResult {
  ok: boolean
  steps: UpdateStep[]
  needs_restart: boolean
  changed_files?: string[]
  message: string
}

function UpdateModal({ onClose }: { onClose: () => void }) {
  const [updating, setUpdating] = useState(false)
  const [result, setResult] = useState<UpdateResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const runUpdate = async () => {
    setUpdating(true)
    setError(null)
    setResult(null)
    try {
      const res = await fetch(`${API_BASE}/api/system/update`, { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data: UpdateResult = await res.json()
      setResult(data)

      // Если обновился только фронтенд — предложим перезагрузку
      if (data.ok && !data.needs_restart) {
        const hasNewFrontend = data.steps?.some(s => s.step === 'frontend build' && s.ok)
        if (hasNewFrontend) {
          setTimeout(() => window.location.reload(), 2000)
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка обновления')
    } finally {
      setUpdating(false)
    }
  }

  return (
    <div className="integrations-overlay" onClick={onClose}>
      <div className="integrations-modal" onClick={e => e.stopPropagation()} style={{ width: 460 }}>
        <div className="integrations-modal__header">
          <h3>Обновление приложения</h3>
          <button className="integrations-modal__close" onClick={onClose}>×</button>
        </div>

        <div className="integrations-modal__section">
          {!result && !error && (
            <div style={{ textAlign: 'center', padding: '12px 0' }}>
              <p style={{ color: 'var(--text-secondary)', fontSize: 13, margin: '0 0 16px' }}>
                Загрузит обновления из GitHub, пересоберёт фронтенд и обновит зависимости при необходимости.
              </p>
              <button
                className="update-btn"
                onClick={runUpdate}
                disabled={updating}
              >
                {updating ? 'Обновляю...' : 'Обновить'}
              </button>
            </div>
          )}

          {updating && (
            <div style={{ textAlign: 'center', padding: '20px 0' }}>
              <div className="update-spinner" />
              <p style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 12 }}>
                git pull + build...
              </p>
            </div>
          )}

          {error && (
            <div style={{ padding: '8px 0' }}>
              <div className="update-step update-step--fail">
                <span>✗</span> Ошибка: {error}
              </div>
              <button className="update-btn" onClick={runUpdate} style={{ marginTop: 12 }}>
                Повторить
              </button>
            </div>
          )}

          {result && (
            <div style={{ padding: '4px 0' }}>
              {result.steps.map((s, i) => (
                <div key={i} className={`update-step ${s.ok ? 'update-step--ok' : 'update-step--fail'}`}>
                  <span>{s.ok ? '✓' : '✗'}</span>
                  <strong>{s.step}</strong> — {s.output}
                </div>
              ))}

              {result.changed_files && result.changed_files.length > 0 && (
                <details style={{ marginTop: 8, fontSize: 11, color: 'var(--text-muted)' }}>
                  <summary style={{ cursor: 'pointer' }}>
                    Изменённые файлы ({result.changed_files.length})
                  </summary>
                  <pre style={{ margin: '4px 0 0', whiteSpace: 'pre-wrap', fontSize: 11 }}>
                    {result.changed_files.join('\n')}
                  </pre>
                </details>
              )}

              <div className={`update-message ${result.ok ? 'update-message--ok' : 'update-message--fail'}`}>
                {result.message}
              </div>

              {result.needs_restart && (
                <p style={{ fontSize: 11, color: 'var(--status-queued)', marginTop: 8 }}>
                  Для полного обновления перезапустите start.bat
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export function StatusBar() {
  const online = useWorkerOnline()
  const queueSize = useQueueSize()
  const [showIntegrations, setShowIntegrations] = useState(false)
  const [showUpdate, setShowUpdate] = useState(false)

  return (
    <header className="statusbar">
      <div className="statusbar__brand">
        <img src="/avatar-nastya.png" alt="Настя" className="statusbar__avatar" />
        <span className="statusbar__title">Nastya Orchestrator</span>
      </div>

      <div className="statusbar__status">
        <button
          className="statusbar__update-btn"
          onClick={() => setShowUpdate(true)}
          title="Обновить приложение"
        >
          ↻
        </button>

        <button
          className="statusbar__integrations-btn"
          onClick={() => setShowIntegrations(true)}
          title="Интеграции и вебхуки"
        >
          🔗
        </button>

        {online ? (
          <>
            <span className="statusbar__dot statusbar__dot--online" />
            <span className="statusbar__label statusbar__label--online">
              Worker онлайн
            </span>
            {queueSize > 0 && (
              <span className="statusbar__queue">
                {queueSize} в очереди
              </span>
            )}
          </>
        ) : (
          <>
            <span className="statusbar__dot statusbar__dot--offline" />
            <span className="statusbar__label statusbar__label--offline">
              Worker офлайн
            </span>
          </>
        )}
      </div>

      {showIntegrations && <IntegrationsModal onClose={() => setShowIntegrations(false)} />}
      {showUpdate && <UpdateModal onClose={() => setShowUpdate(false)} />}
    </header>
  )
}
