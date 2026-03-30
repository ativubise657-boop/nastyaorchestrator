import { useEffect } from 'react'
import { StatusBar } from './components/StatusBar'
import { Sidebar } from './components/Sidebar'
import { ChatPanel } from './components/ChatPanel'
import { DocPanel } from './components/DocPanel'
import { useStore, useSidebarOpen, useSelectedProjectId } from './stores'
import { useSSE } from './hooks/useSSE'
import './App.css'

// Пустое состояние — нет выбранного проекта
function EmptyState() {
  return (
    <div className="app__empty">
      <svg className="app__empty-icon" viewBox="0 0 64 64" fill="none">
        <circle cx="32" cy="32" r="24" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="32" cy="32" r="8" fill="currentColor" opacity="0.4" />
        <path d="M32 14v4M32 46v4M14 32h4M46 32h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
      <h2>Выберите проект</h2>
      <p>или создайте новый в боковой панели</p>
    </div>
  )
}

export default function App() {
  const selectedProjectId = useSelectedProjectId()
  const sidebarOpen = useSidebarOpen()

  // Подключаем SSE
  useSSE()

  // Загружаем проекты при старте
  useEffect(() => {
    useStore.getState().loadProjects()
  }, [])

  // Загружаем документы, папки и ссылки при смене проекта
  useEffect(() => {
    if (selectedProjectId) {
      useStore.getState().loadDocuments(selectedProjectId)
      useStore.getState().loadFolders(selectedProjectId)
      useStore.getState().loadLinks(selectedProjectId)
    }
  }, [selectedProjectId])

  // Загружаем статус воркера при старте
  useEffect(() => {
    fetch('/api/system/health')
      .then((res) => res.ok ? res.json() : null)
      .then((data) => {
        if (data?.worker) {
          useStore.getState().setWorkerStatus(
            data.worker.online ?? false,
            data.worker.queue_size ?? 0,
          )
        }
      })
      .catch(() => {})
  }, [])

  // На десктопе — сайдбар открыт по умолчанию; на мобайле — только без проекта
  useEffect(() => {
    const isMobile = window.innerWidth <= 768
    if (!isMobile) {
      useStore.getState().setSidebarOpen(true)
    } else if (!selectedProjectId) {
      useStore.getState().setSidebarOpen(true)
    }
  }, [selectedProjectId])

  return (
    <div className="app">
      <StatusBar />

      <div className="app__body">
        <Sidebar />

        <div
          className={`sidebar-overlay ${sidebarOpen ? 'active' : ''}`}
          onClick={() => useStore.getState().setSidebarOpen(false)}
          aria-hidden="true"
        />

        <main className="app__main">
          {selectedProjectId ? (
            <ChatPanel />
          ) : (
            <EmptyState />
          )}
        </main>

        {/* Правый сайдбар документов */}
        <DocPanel />
      </div>
    </div>
  )
}
