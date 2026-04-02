import { useState } from 'react'
import { useProjects, useQueueSize, useWorkerOnline } from '../stores'
import { AppUpdateModal } from './AppUpdateModal'
import './StatusBar.css'

const INTEGRATIONS = [
  { name: 'Bitrix24 CRM', desc: 'Поиск компаний, контактов, сделок', status: 'active' as const, icon: '🔗' },
  { name: 'Б24 входящие вебхуки', desc: 'POST /api/webhooks/b24 — приём событий', status: 'active' as const, icon: '📥' },
  { name: 'GitHub API', desc: 'Read-only доступ к репозиториям проектов', status: 'active' as const, icon: '🐙' },
  { name: 'Документы', desc: 'Загрузка и анализ файлов (CSV, PDF, TXT)', status: 'active' as const, icon: '📄' },
  { name: 'RAG / FTS5 поиск', desc: 'Семантический поиск по документам', status: 'planned' as const, icon: '🔍' },
  { name: 'Б24 задачи', desc: 'Создание и обновление задач в Битрикс24', status: 'planned' as const, icon: '✅' },
  { name: 'Б24 уведомления', desc: 'Отправка уведомлений через Б24', status: 'planned' as const, icon: '🔔' },
  { name: 'Email', desc: 'Отправка писем через SMTP', status: 'planned' as const, icon: '📧' },
]

function IntegrationsModal({ onClose }: { onClose: () => void }) {
  const active = INTEGRATIONS.filter((item) => item.status === 'active')
  const planned = INTEGRATIONS.filter((item) => item.status === 'planned')

  return (
    <div className="integrations-overlay" onClick={onClose}>
      <div className="integrations-modal" onClick={(event) => event.stopPropagation()}>
        <div className="integrations-modal__header">
          <h3>Интеграции и вебхуки</h3>
          <button className="integrations-modal__close" onClick={onClose}>×</button>
        </div>

        <div className="integrations-modal__section">
          <div className="integrations-modal__section-title">
            <span className="integrations-modal__dot integrations-modal__dot--active" />
            Подключено ({active.length})
          </div>
          {active.map((item) => (
            <div key={item.name} className="integrations-modal__item">
              <span className="integrations-modal__icon">{item.icon}</span>
              <div>
                <div className="integrations-modal__name">{item.name}</div>
                <div className="integrations-modal__desc">{item.desc}</div>
              </div>
            </div>
          ))}
        </div>

        <div className="integrations-modal__section">
          <div className="integrations-modal__section-title">
            <span className="integrations-modal__dot integrations-modal__dot--planned" />
            В планах ({planned.length})
          </div>
          {planned.map((item) => (
            <div key={item.name} className="integrations-modal__item integrations-modal__item--planned">
              <span className="integrations-modal__icon">{item.icon}</span>
              <div>
                <div className="integrations-modal__name">{item.name}</div>
                <div className="integrations-modal__desc">{item.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export function StatusBar() {
  const online = useWorkerOnline()
  const queueSize = useQueueSize()
  const projects = useProjects()
  const [showIntegrations, setShowIntegrations] = useState(false)
  const [showUpdate, setShowUpdate] = useState(false)

  const hasAppProject = projects.some((project) => project.name === 'nastyaorchestrator')

  return (
    <header className="statusbar">
      <div className="statusbar__brand">
        <img src="/avatar-nastya.png" alt="Настя" className="statusbar__avatar" />
        <span className="statusbar__title">Nastya Orchestrator</span>
      </div>

      <div className="statusbar__status">
        <button
          className="statusbar__action statusbar__action--update"
          onClick={() => setShowUpdate(true)}
          title="Загрузить обновление из GitHub"
          disabled={!hasAppProject}
        >
          <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M8 2.5a5.5 5.5 0 015.35 4.2M8 13.5a5.5 5.5 0 01-5.35-4.2M11 3.5h2v2M3 10.5H1v2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span>Обновить приложение</span>
        </button>

        <button
          className="statusbar__action"
          onClick={() => setShowIntegrations(true)}
          title="Интеграции и вебхуки"
        >
          <span className="statusbar__action-emoji" aria-hidden="true">🔗</span>
          <span>Интеграции</span>
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
      {showUpdate && <AppUpdateModal onClose={() => setShowUpdate(false)} />}
    </header>
  )
}
