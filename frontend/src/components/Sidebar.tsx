import { useSidebarOpen, useStore, useProjects } from '../stores'
import { ProjectList } from './ProjectList'
import './Sidebar.css'

export function Sidebar() {
  const sidebarOpen = useSidebarOpen()
  const setSidebarOpen = useStore((s) => s.setSidebarOpen)
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  const projects = useProjects()

  return (
    <aside className={`sidebar ${sidebarOpen ? 'sidebar--open' : 'sidebar--closed'}`}>
      {/* Кнопка-вкладка для открытия/закрытия (десктоп) */}
      <button
        className="sidebar__toggle"
        onClick={toggleSidebar}
        title={sidebarOpen ? 'Скрыть проекты' : 'Показать проекты'}
      >
        <svg
          viewBox="0 0 16 16"
          fill="none"
          className={sidebarOpen ? '' : 'sidebar__toggle-icon--closed'}
        >
          <path d="M10 4L6 8l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {!sidebarOpen && projects.length > 0 && (
          <span className="sidebar__toggle-badge">{projects.length}</span>
        )}
      </button>

      {sidebarOpen && (
        <div className="sidebar__inner">
          {/* Список проектов */}
          <section className="sidebar__section">
            <ProjectList />
          </section>
        </div>
      )}

      {/* Кнопка скрыть (только мобайл) */}
      <button
        className="sidebar__mobile-close"
        onClick={() => setSidebarOpen(false)}
        aria-label="Закрыть панель"
      >
        <svg viewBox="0 0 16 16" fill="none">
          <path d="M10 4L6 8l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
    </aside>
  )
}
