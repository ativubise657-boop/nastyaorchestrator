import { useState } from 'react'
import { useStore, useProjects, useSelectedProjectId, type Project } from '../stores'
import { ProjectModal } from './ProjectModal'
import './ProjectList.css'

export function ProjectList() {
  const projects = useProjects()
  const selectedId = useSelectedProjectId()
  const selectProject = useStore((s) => s.selectProject)
  const deleteProject = useStore((s) => s.deleteProject)
  const updateApp = useStore((s) => s.updateApp)
  const projectsLoading = useStore((s) => s.projectsLoading)

  const [modalOpen, setModalOpen] = useState(false)
  const [editingProject, setEditingProject] = useState<Project | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [updatingId, setUpdatingId] = useState<string | null>(null)
  const [contextMenu, setContextMenu] = useState<{
    project: Project
    x: number
    y: number
  } | null>(null)

  const handleSelect = (id: string) => {
    selectProject(id)
    // Закрываем контекстное меню если было открыто
    setContextMenu(null)
  }

  const handleContextMenu = (e: React.MouseEvent, project: Project) => {
    e.preventDefault()
    setContextMenu({ project, x: e.clientX, y: e.clientY })
  }

  const handleEdit = (project: Project) => {
    setEditingProject(project)
    setModalOpen(true)
    setContextMenu(null)
  }

  const handleDelete = async (project: Project) => {
    if (!confirm(`Удалить проект "${project.name}"?\nИстория чата будет потеряна.`)) return
    setContextMenu(null)
    setDeletingId(project.id)
    try {
      await deleteProject(project.id)
    } finally {
      setDeletingId(null)
    }
  }

  const handleUpdateApp = async (project: Project) => {
    if (!confirm('Скачать обновление из GitHub, пересобрать приложение и перезапустить сервисы?')) return
    setContextMenu(null)
    setUpdatingId(project.id)
    try {
      const result = await updateApp(project.id)
      alert(result.message)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Не удалось обновить приложение'
      alert(message)
    } finally {
      setUpdatingId(null)
    }
  }

  const closeModal = () => {
    setModalOpen(false)
    setEditingProject(null)
  }

  return (
    <div className="project-list">
      <div className="project-list__header">
        <span className="project-list__label">Проекты</span>
        <button
          className="project-list__add-btn"
          onClick={() => { setEditingProject(null); setModalOpen(true) }}
          title="Создать проект"
          aria-label="Новый проект"
        >
          <svg viewBox="0 0 16 16" fill="none">
            <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      <div className="project-list__items">
        {projectsLoading && projects.length === 0 && (
          <div className="project-list__loading">
            <span className="project-list__spinner" />
          </div>
        )}

        {!projectsLoading && projects.length === 0 && (
          <div className="project-list__empty">
            <p>Нет проектов</p>
            <button
              className="project-list__create-hint"
              onClick={() => { setEditingProject(null); setModalOpen(true) }}
            >
              Создать первый →
            </button>
          </div>
        )}

        {projects.map((project) => (
          <button
            key={project.id}
            className={`project-item ${selectedId === project.id ? 'project-item--active' : ''} ${deletingId === project.id ? 'project-item--deleting' : ''}`}
            onClick={() => handleSelect(project.id)}
            onContextMenu={(e) => handleContextMenu(e, project)}
            title={project.description || project.name}
          >
            {/* Иконка проекта */}
            <span className="project-item__icon" aria-hidden="true">
              {project.name.charAt(0).toUpperCase()}
            </span>

            <div className="project-item__content">
              <span className="project-item__name">{project.name}</span>
              {project.description && (
                <span className="project-item__desc">{project.description}</span>
              )}
            </div>

            {(deletingId === project.id || updatingId === project.id) && (
              <span className="project-item__spinner" />
            )}
          </button>
        ))}
      </div>

      {/* Контекстное меню */}
      {contextMenu && (
        <>
          <div
            className="context-overlay"
            onClick={() => setContextMenu(null)}
          />
          <div
            className="context-menu"
            style={{ top: contextMenu.y, left: contextMenu.x }}
          >
            {contextMenu.project.name === 'nastyaorchestrator' && (
              <button
                className="context-menu__item"
                onClick={() => handleUpdateApp(contextMenu.project)}
              >
                <svg viewBox="0 0 16 16" fill="none">
                  <path d="M8 3v3m0 0l2-2M8 6L6 4M3.5 8a4.5 4.5 0 109 0" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                Обновить приложение
              </button>
            )}
            <button
              className="context-menu__item"
              onClick={() => handleEdit(contextMenu.project)}
            >
              <svg viewBox="0 0 16 16" fill="none">
                <path d="M11.5 2.5l2 2-8 8H3.5v-2l8-8z" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              Редактировать
            </button>
            <button
              className="context-menu__item context-menu__item--danger"
              onClick={() => handleDelete(contextMenu.project)}
            >
              <svg viewBox="0 0 16 16" fill="none">
                <path d="M4 5h8M6 5V4h4v1M5 5v7h6V5H5z" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              Удалить
            </button>
          </div>
        </>
      )}

      {/* Modal создания/редактирования */}
      {modalOpen && (
        <ProjectModal
          project={editingProject}
          onClose={closeModal}
        />
      )}
    </div>
  )
}
