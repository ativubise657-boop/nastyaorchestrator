import { useEffect, useRef, useState } from 'react'
import {
  useAppVersion,
  useProjects,
  useStore,
  type AppUpdatePreview,
  type AppUpdateReleaseNote,
  type AppUpdateStatus,
} from '../stores'
import './ProjectModal.css'
import './AppUpdateModal.css'

const APP_PROJECT_NAME = 'nastyaorchestrator'

function extractApiError(error: unknown): string {
  const raw = error instanceof Error ? error.message : 'Не удалось выполнить обновление'
  const match = raw.match(/API error \d+:\s*(.*)$/)
  const payload = match ? match[1] : raw

  try {
    const parsed = JSON.parse(payload)
    if (parsed?.detail) return String(parsed.detail)
  } catch {
    // ignore non-json payloads
  }

  return payload
}

function formatCommitOverflow(preview: AppUpdatePreview | null) {
  if (!preview) return null
  const extra = preview.commit_count - preview.commits.length
  if (extra <= 0) return null
  return `И ещё ${extra} изменений`
}

function formatReleaseTitle(note: AppUpdateReleaseNote) {
  if (note.version && note.title.trim() === `v${note.version}`) {
    return `Версия v${note.version}`
  }
  return note.title
}

interface Props {
  onClose: () => void
}

export function AppUpdateModal({ onClose }: Props) {
  const appVersion = useAppVersion()
  const projects = useProjects()
  const getAppUpdatePreview = useStore((s) => s.getAppUpdatePreview)
  const startAppUpdate = useStore((s) => s.startAppUpdate)
  const getAppUpdateStatus = useStore((s) => s.getAppUpdateStatus)

  const project = projects.find((item) => item.name === APP_PROJECT_NAME) ?? null

  const [preview, setPreview] = useState<AppUpdatePreview | null>(null)
  const [status, setStatus] = useState<AppUpdateStatus | null>(null)
  const [loadingPreview, setLoadingPreview] = useState(true)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const reloadTimerRef = useRef<number | null>(null)

  useEffect(() => {
    if (!project) {
      setLoadingPreview(false)
      setError('Проект приложения не найден в списке проектов.')
      return
    }

    let cancelled = false
    setLoadingPreview(true)
    setError(null)

    getAppUpdatePreview(project.id)
      .then((data) => {
        if (cancelled) return
        setPreview(data)
        if (data.active_status) {
          setStatus(data.active_status)
        }
      })
      .catch((err) => {
        if (cancelled) return
        setError(extractApiError(err))
      })
      .finally(() => {
        if (!cancelled) setLoadingPreview(false)
      })

    return () => {
      cancelled = true
      if (reloadTimerRef.current) {
        window.clearTimeout(reloadTimerRef.current)
      }
    }
  }, [getAppUpdatePreview, project])

  useEffect(() => {
    if (!project || !status || !['queued', 'running'].includes(status.status)) return

    let cancelled = false
    const interval = window.setInterval(async () => {
      try {
        const next = await getAppUpdateStatus(project.id)
        if (cancelled) return
        setStatus(next)

        if (next.status === 'completed' && next.restarting && !reloadTimerRef.current) {
          reloadTimerRef.current = window.setTimeout(() => {
            window.location.reload()
          }, 3500)
        }
      } catch (err) {
        if (cancelled) return
        if (status.restarting || status.phase === 'restart') {
          if (!reloadTimerRef.current) {
            reloadTimerRef.current = window.setTimeout(() => {
              window.location.reload()
            }, 3500)
          }
          return
        }
        setError(extractApiError(err))
      }
    }, 1000)

    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [getAppUpdateStatus, project, status])

  const handleStart = async () => {
    if (!project) return
    setStarting(true)
    setError(null)
    try {
      const next = await startAppUpdate(project.id)
      setStatus(next)
    } catch (err) {
      setError(extractApiError(err))
    } finally {
      setStarting(false)
    }
  }

  const effectiveCurrentLabel = status?.current_label ?? preview?.current_label ?? '...'
  const effectiveTargetLabel = status?.target_label ?? preview?.target_label ?? '...'
  const effectiveAppVersion = appVersion ?? status?.current_version ?? preview?.current_version ?? null
  const effectiveReleaseNotes = status?.release_notes ?? preview?.release_notes ?? []
  const effectiveCommits = status?.commits ?? preview?.commits ?? []
  const checkError = status?.check_error ?? preview?.check_error ?? null
  const blockedReason = status?.blocked_reason ?? preview?.blocked_reason ?? checkError ?? null
  const hasLocalChanges = status?.local_changes ?? preview?.local_changes ?? false
  const needsUpdate = status?.needs_update ?? preview?.needs_update ?? false
  const isRunning = status ? ['queued', 'running'].includes(status.status) : false
  const isFinished = status ? ['completed', 'failed'].includes(status.status) : false
  const canStart = !loadingPreview && !starting && !isRunning && needsUpdate && !hasLocalChanges && !checkError
  const modalTitle = loadingPreview
    ? 'Проверяем обновления...'
    : checkError
      ? 'Проверка обновления недоступна'
      : error && !preview && !status
        ? 'Не удалось проверить обновление'
      : needsUpdate
        ? `Обновление до ${effectiveTargetLabel}`
        : 'Приложение уже обновлено'

  return (
    <div className="modal-backdrop" onClick={isRunning ? undefined : onClose}>
      <div
        className="modal modal--wide"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Обновление приложения"
      >
        <div className="modal__header">
          <div>
            <div className="app-update__meta">
              <div className="app-update__eyebrow">Системное обновление</div>
              {effectiveAppVersion && (
                <div className="app-update__app-version">Nastya Orchestrator v{effectiveAppVersion}</div>
              )}
            </div>
            <h2 className="modal__title">{modalTitle}</h2>
          </div>
          <button className="modal__close" onClick={onClose} aria-label="Закрыть">
            <svg viewBox="0 0 16 16" fill="none">
              <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <div className="modal__body">
          {loadingPreview && (
            <div className="app-update__loading">
              <span className="app-update__spinner" />
              <p>Проверяем GitHub, текущую версию и список изменений...</p>
            </div>
          )}

          {!loadingPreview && (
            <>
              <div className="app-update__version-row">
                <div className={`app-update__version-card ${!needsUpdate ? 'app-update__version-card--success' : ''}`}>
                  <span className="app-update__version-label">Сейчас</span>
                  <strong>{effectiveCurrentLabel}</strong>
                </div>
                <div className="app-update__version-arrow">→</div>
                <div className="app-update__version-card app-update__version-card--accent">
                  <span className="app-update__version-label">После обновления</span>
                  <strong>{effectiveTargetLabel}</strong>
                </div>
              </div>

              {error && <div className="modal__error">{error}</div>}

              {blockedReason && (
                <div className="app-update__warning">
                  {blockedReason}
                </div>
              )}

              {!status && (
                <div className="app-update__summary">
                  <p className="app-update__summary-text">
                    {checkError
                      ? 'Приложение работает как обычно. Сейчас не получилось связаться с GitHub, поэтому обновление временно недоступно.'
                      : needsUpdate
                      ? 'Загрузим изменения из GitHub, при необходимости обновим зависимости, пересоберём интерфейс и мягко перезапустим приложение.'
                      : 'Приложение уже на последней версии. Можно просто закрыть окно.'}
                  </p>

                  <div className="app-update__section">
                    <div className="app-update__section-title">
                      {checkError ? 'Текущая версия' : needsUpdate ? 'Что изменится' : 'Текущая версия'}
                    </div>
                    {effectiveReleaseNotes.length > 0 ? (
                      <div className="app-update__release-list">
                        {effectiveReleaseNotes.map((note) => (
                          <section
                            key={`${note.version ?? 'release'}-${note.title}`}
                            className="app-update__release-card"
                          >
                            <div className="app-update__release-title">
                              {formatReleaseTitle(note)}
                            </div>
                            <ul className="app-update__release-items">
                              {note.items.map((item) => (
                                <li key={`${note.title}-${item}`} className="app-update__release-item">
                                  {item}
                                </li>
                              ))}
                            </ul>
                          </section>
                        ))}
                      </div>
                    ) : effectiveCommits.length > 0 ? (
                      <ul className="app-update__commit-list">
                        {effectiveCommits.map((commit) => (
                          <li key={`${commit.sha}-${commit.summary}`} className="app-update__commit-item">
                            <span className="app-update__commit-sha">{commit.sha}</span>
                            <span>{commit.summary}</span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <div className="app-update__empty-note">
                        {checkError
                          ? 'Когда доступ к GitHub восстановится, здесь снова появится описание новой версии.'
                          : needsUpdate
                          ? 'Описание релиза пока не заполнено, но новая версия уже обнаружена.'
                          : 'Для этой версии ещё не добавили отдельное описание релиза.'}
                      </div>
                    )}
                    {!effectiveReleaseNotes.length && formatCommitOverflow(preview) && (
                      <div className="app-update__overflow">{formatCommitOverflow(preview)}</div>
                    )}
                  </div>
                </div>
              )}

              {status && (
                <div className="app-update__progress">
                  <div className="app-update__progress-head">
                    <span>{status.message}</span>
                    <strong>{status.progress}%</strong>
                  </div>
                  <div className="app-update__progress-bar">
                    <div
                      className={`app-update__progress-fill ${status.status === 'failed' ? 'app-update__progress-fill--failed' : ''}`}
                      style={{ width: `${Math.max(4, status.progress)}%` }}
                    />
                  </div>

                  <div className="app-update__section">
                    <div className="app-update__section-title">Ход обновления</div>
                    <ul className="app-update__log-list">
                      {status.logs.length > 0 ? (
                        status.logs.map((line, index) => (
                          <li key={`${line}-${index}`} className="app-update__log-item">
                            {line}
                          </li>
                        ))
                      ) : (
                        <li className="app-update__log-item app-update__log-item--muted">
                          Ожидаем запуск обновления...
                        </li>
                      )}
                    </ul>
                  </div>

                  {status.changed_files.length > 0 && (
                    <details className="app-update__details">
                      <summary>Изменённые файлы ({status.changed_files.length})</summary>
                      <pre>{status.changed_files.join('\n')}</pre>
                    </details>
                  )}

                  {status.status === 'completed' && status.restarting && (
                    <div className="app-update__success">
                      Приложение перезапускается. Страница обновится автоматически.
                    </div>
                  )}
                  {status.status === 'failed' && status.error && (
                    <div className="modal__error">{status.error}</div>
                  )}
                </div>
              )}
            </>
          )}

          <div className="modal__footer">
            <button
              type="button"
              className="modal__btn modal__btn--secondary"
              onClick={onClose}
              disabled={isRunning}
            >
              {isFinished ? 'Закрыть' : 'Отмена'}
            </button>
            <button
              type="button"
              className="modal__btn modal__btn--primary"
              onClick={handleStart}
              disabled={!canStart}
            >
              {starting
                ? 'Запускаем...'
                : isRunning
                  ? 'Обновляем...'
                  : status?.status === 'failed'
                    ? 'Повторить'
                    : isFinished
                      ? 'Готово'
                      : checkError
                        ? 'Недоступно'
                        : 'Обновить'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
