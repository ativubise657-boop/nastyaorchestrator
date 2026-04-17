// Заголовок чат-панели: кнопка меню, хлебная крошка проекта, название сессии
import { useStore } from '../../stores'
import { useCurrentSession } from '../../stores'

// Хлебная крошка с именем проекта
function ProjectBreadcrumb() {
  const selectedProject = useStore((s) =>
    s.projects.find((p) => p.id === s.selectedProjectId),
  )

  if (!selectedProject) return <span className="chat-panel__no-project">Выберите проект</span>

  return (
    <div className="chat-panel__breadcrumb">
      <span className="chat-panel__project-icon" aria-hidden="true">
        {selectedProject.name.charAt(0).toUpperCase()}
      </span>
      <span className="chat-panel__project-name">{selectedProject.name}</span>
    </div>
  )
}

export function ChatHeader() {
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const currentSession = useCurrentSession()

  return (
    <div className="chat-panel__header">
      <button
        className="chat-panel__menu-btn"
        onClick={toggleSidebar}
        aria-label={sidebarOpen ? 'Скрыть панель' : 'Открыть панель'}
        title="Панель проектов"
      >
        <svg viewBox="0 0 16 16" fill="none">
          <path d="M2 4h12M2 8h12M2 12h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>

      <ProjectBreadcrumb />

      {currentSession && (
        <span className="chat-panel__session-title" title={currentSession.title}>
          {currentSession.title}
        </span>
      )}
    </div>
  )
}
