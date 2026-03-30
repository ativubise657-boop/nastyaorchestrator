import { useState, useEffect, useRef } from 'react'
import { useStore, type Project, type CreateProjectData } from '../stores'
import './ProjectModal.css'

interface Props {
  project?: Project | null  // null = создание, Project = редактирование
  onClose: () => void
}

export function ProjectModal({ project, onClose }: Props) {
  const createProject = useStore((s) => s.createProject)
  const updateProject = useStore((s) => s.updateProject)

  const [name, setName] = useState(project?.name ?? '')
  const [description, setDescription] = useState(project?.description ?? '')
  const [path, setPath] = useState(project?.path ?? '')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const nameRef = useRef<HTMLInputElement>(null)

  const isEditing = !!project

  useEffect(() => {
    // Фокус на поле имени при открытии
    nameRef.current?.focus()
  }, [])

  // Закрыть по Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) {
      setError('Название обязательно')
      return
    }

    setLoading(true)
    setError(null)

    const data: CreateProjectData = {
      name: name.trim(),
      description: description.trim(),
      ...(path.trim() ? { path: path.trim() } : {}),
    }

    try {
      if (isEditing) {
        await updateProject(project.id, data)
      } else {
        await createProject(data)
      }
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Ошибка сохранения')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={isEditing ? 'Редактировать проект' : 'Новый проект'}
      >
        <div className="modal__header">
          <h2 className="modal__title">
            {isEditing ? 'Редактировать проект' : 'Новый проект'}
          </h2>
          <button className="modal__close" onClick={onClose} aria-label="Закрыть">
            <svg viewBox="0 0 16 16" fill="none">
              <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <form className="modal__body" onSubmit={handleSubmit}>
          {error && <div className="modal__error">{error}</div>}

          <label className="modal__field">
            <span className="modal__label">
              Название <span className="modal__required">*</span>
            </span>
            <input
              ref={nameRef}
              type="text"
              className="modal__input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Например: Маркетинговые материалы"
              maxLength={100}
              disabled={loading}
            />
          </label>

          <label className="modal__field">
            <span className="modal__label">Описание</span>
            <textarea
              className="modal__textarea"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Краткое описание проекта..."
              rows={3}
              maxLength={500}
              disabled={loading}
            />
          </label>

          <label className="modal__field">
            <span className="modal__label">Путь к файлам</span>
            <input
              type="text"
              className="modal__input"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="/home/nastya/projects/example"
              disabled={loading}
            />
            <span className="modal__hint">
              Локальный путь на сервере (необязательно)
            </span>
          </label>

          <div className="modal__footer">
            <button
              type="button"
              className="modal__btn modal__btn--secondary"
              onClick={onClose}
              disabled={loading}
            >
              Отмена
            </button>
            <button
              type="submit"
              className="modal__btn modal__btn--primary"
              disabled={loading || !name.trim()}
            >
              {loading
                ? isEditing
                  ? 'Сохраняем...'
                  : 'Создаём...'
                : isEditing
                  ? 'Сохранить'
                  : 'Создать'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
