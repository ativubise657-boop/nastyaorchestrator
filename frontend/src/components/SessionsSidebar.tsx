import { useState, useRef, useEffect, useCallback, KeyboardEvent } from 'react'
import {
  useSessions,
  useCurrentSessionId,
  useSessionsLoading,
  useStore,
  type ChatSession,
} from '../stores'
import './SessionsSidebar.scss'

// ===== Хелпер относительной даты =====
function formatRelativeDate(iso: string): string {
  if (!iso) return ''
  try {
    const date = new Date(iso)
    if (isNaN(date.getTime())) return ''
    const now = Date.now()
    const diff = now - date.getTime()
    const minutes = Math.floor(diff / 60_000)
    const hours = Math.floor(diff / 3_600_000)
    const days = Math.floor(diff / 86_400_000)

    if (diff < 60_000) return 'только что'
    if (minutes < 60) return `${minutes} мин назад`
    if (hours < 24) return `${hours} ч назад`
    if (days === 1) return 'вчера'
    if (days < 7) return `${days} дня назад`
    return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' })
  } catch {
    return ''
  }
}

// ===== Один элемент сессии =====
interface SessionItemProps {
  session: ChatSession
  isActive: boolean
  onSwitch: (id: string) => void
  onRename: (id: string, title: string) => Promise<void>
  onDelete: (session: ChatSession) => void
  isFocused: boolean
  itemRef: (el: HTMLLIElement | null) => void
}

function SessionItem({
  session,
  isActive,
  onSwitch,
  onRename,
  onDelete,
  isFocused,
  itemRef,
}: SessionItemProps) {
  // Состояние inline-редактирования
  const [editing, setEditing] = useState(false)
  const [editValue, setEditValue] = useState(session.title)
  const inputRef = useRef<HTMLInputElement>(null)

  // Фокус на инпут при старте редактирования
  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.select()
    }
  }, [editing])

  const startEdit = () => {
    setEditValue(session.title)
    setEditing(true)
  }

  const commitEdit = async () => {
    const trimmed = editValue.trim()
    if (trimmed && trimmed !== session.title) {
      await onRename(session.id, trimmed)
    }
    setEditing(false)
  }

  const cancelEdit = () => {
    setEditValue(session.title)
    setEditing(false)
  }

  const handleEditKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      commitEdit()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      cancelEdit()
    }
  }

  const handleDoubleClick = () => {
    startEdit()
  }

  return (
    <li
      ref={itemRef}
      className={[
        'sessions-sidebar__item',
        isActive ? 'sessions-sidebar__item--active' : '',
        isFocused ? 'sessions-sidebar__item--focused' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      onClick={() => !editing && onSwitch(session.id)}
      onDoubleClick={handleDoubleClick}
      title={session.title}
      role="option"
      aria-selected={isActive}
      tabIndex={-1}
    >
      {/* Заголовок / инпут редактирования */}
      <div className="sessions-sidebar__item-body">
        {editing ? (
          <input
            ref={inputRef}
            className="sessions-sidebar__item-edit"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onKeyDown={handleEditKeyDown}
            onBlur={commitEdit}
            maxLength={100}
            aria-label="Переименовать чат"
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <span className="sessions-sidebar__item-title">{session.title}</span>
        )}
        <span className="sessions-sidebar__item-date">
          {formatRelativeDate(session.updated_at)}
        </span>
      </div>

      {/* Счётчик сообщений */}
      {session.message_count > 0 && !editing && (
        <span className="sessions-sidebar__item-count" aria-label={`${session.message_count} сообщений`}>
          {session.message_count > 99 ? '99+' : session.message_count}
        </span>
      )}

      {/* Кнопка удаления — видна при hover */}
      {!editing && (
        <button
          className="sessions-sidebar__item-delete"
          onClick={(e) => {
            e.stopPropagation()
            onDelete(session)
          }}
          aria-label={`Удалить чат "${session.title}"`}
          title="Удалить чат"
          tabIndex={-1}
        >
          <svg viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <path
              d="M2 3.5h10M5.5 3.5V2.5h3V3.5M3.5 3.5l.7 8h5.6l.7-8"
              stroke="currentColor"
              strokeWidth="1.3"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      )}
    </li>
  )
}

// ===== Skeleton-заглушки при загрузке =====
function SessionsSkeleton() {
  return (
    <ul className="sessions-sidebar__list" aria-busy="true" aria-label="Загрузка чатов...">
      {[1, 2, 3].map((i) => (
        <li key={i} className="sessions-sidebar__skeleton" aria-hidden="true">
          <span className="sessions-sidebar__skeleton-title" />
          <span className="sessions-sidebar__skeleton-date" />
        </li>
      ))}
    </ul>
  )
}

// ===== Основной компонент =====
export function SessionsSidebar() {
  const sessions = useSessions()
  const currentSessionId = useCurrentSessionId()
  const sessionsLoading = useSessionsLoading()
  const selectedProjectId = useStore((s) => s.selectedProjectId)
  const createSession = useStore((s) => s.createSession)
  const switchSession = useStore((s) => s.switchSession)
  const renameSession = useStore((s) => s.renameSession)
  const deleteSession = useStore((s) => s.deleteSession)

  // Клавиатурная навигация: индекс «сфокусированного» элемента (не активного)
  const [focusedIdx, setFocusedIdx] = useState<number>(-1)
  const itemRefs = useRef<(HTMLLIElement | null)[]>([])

  // Сбрасываем focusedIdx при смене списка
  useEffect(() => {
    setFocusedIdx(-1)
  }, [sessions.length])

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLUListElement>) => {
      if (!sessions.length) return
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setFocusedIdx((prev) => {
          const next = Math.min(prev + 1, sessions.length - 1)
          itemRefs.current[next]?.focus()
          return next
        })
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setFocusedIdx((prev) => {
          const next = Math.max(prev - 1, 0)
          itemRefs.current[next]?.focus()
          return next
        })
      } else if (e.key === 'Enter' && focusedIdx >= 0) {
        const session = sessions[focusedIdx]
        if (session) switchSession(session.id)
      }
    },
    [sessions, focusedIdx, switchSession],
  )

  const handleNewChat = async () => {
    if (!selectedProjectId) return
    await createSession(selectedProjectId)
  }

  const handleDelete = async (session: ChatSession) => {
    const confirmed = window.confirm(
      `Удалить чат "${session.title}"? Все сообщения и прикреплённые картинки будут удалены.`,
    )
    if (confirmed) {
      await deleteSession(session.id)
    }
  }

  return (
    <aside className="sessions-sidebar" aria-label="История чатов">
      {/* Заголовок + кнопка нового чата */}
      <div className="sessions-sidebar__header">
        <span className="sessions-sidebar__header-title">Чаты</span>
        <button
          className="sessions-sidebar__new-btn"
          onClick={handleNewChat}
          disabled={!selectedProjectId}
          title="Новый чат"
          aria-label="Создать новый чат"
        >
          <svg viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <path d="M7 2v10M2 7h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      {/* Содержимое */}
      {sessionsLoading ? (
        <SessionsSkeleton />
      ) : sessions.length === 0 ? (
        /* Empty state */
        <div className="sessions-sidebar__empty">
          <p>Чаты появятся здесь</p>
          <button
            className="sessions-sidebar__empty-btn"
            onClick={handleNewChat}
            disabled={!selectedProjectId}
          >
            <svg viewBox="0 0 14 14" fill="none" aria-hidden="true">
              <path d="M7 2v10M2 7h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
            Создать первый чат
          </button>
        </div>
      ) : (
        <ul
          className="sessions-sidebar__list"
          role="listbox"
          aria-label="Список чатов"
          onKeyDown={handleKeyDown}
          tabIndex={0}
          aria-activedescendant={
            focusedIdx >= 0 ? `session-item-${sessions[focusedIdx]?.id}` : undefined
          }
        >
          {sessions.map((session, idx) => (
            <SessionItem
              key={session.id}
              session={session}
              isActive={session.id === currentSessionId}
              onSwitch={switchSession}
              onRename={renameSession}
              onDelete={handleDelete}
              isFocused={idx === focusedIdx}
              itemRef={(el) => {
                itemRefs.current[idx] = el
              }}
            />
          ))}
        </ul>
      )}
    </aside>
  )
}
