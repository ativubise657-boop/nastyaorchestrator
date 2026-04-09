/**
 * TauriUpdateModal — модалка обновления через Tauri Updater.
 *
 * Показывается когда useTauriUpdater.available !== null.
 * Пользователь видит:
 *   - Новая версия (номер + release notes)
 *   - Кнопки "Обновить сейчас" / "Позже"
 *   - Прогресс скачивания после клика
 *   - Автоматический relaunch после установки
 */
import { TauriUpdaterState } from '../hooks/useTauriUpdater'
import './TauriUpdateModal.css'

interface Props {
  state: TauriUpdaterState
  onInstall: () => void
  onDismiss: () => void
}

function formatBytes(n: number): string {
  if (n <= 0) return '—'
  const mb = n / (1024 * 1024)
  if (mb >= 1) return `${mb.toFixed(1)} МБ`
  const kb = n / 1024
  return `${kb.toFixed(0)} КБ`
}

function shortVersion(v: string): string {
  // "1.0.0" → "v1", "2.3.1" → "v2.3.1" (оставляем полную если не round)
  const m = v.match(/^(\d+)\.0\.0$/)
  if (m) return `v${m[1]}`
  return `v${v}`
}

export function TauriUpdateModal({ state, onInstall, onDismiss }: Props) {
  if (!state.available) return null

  const { available, phase, progress, downloaded, total, error } = state
  const isDownloading = phase === 'downloading'
  const isInstalling = phase === 'installing'
  const isReady = phase === 'ready-to-restart'
  const hasError = phase === 'error'

  return (
    <div className="tauri-update-backdrop">
      <div className="tauri-update-modal" role="dialog" aria-modal="true">
        <div className="tauri-update-header">
          <span className="tauri-update-icon">🚀</span>
          <h2 className="tauri-update-title">
            Доступно обновление {shortVersion(available.version)}
          </h2>
        </div>

        {available.notes && (
          <div className="tauri-update-notes">
            <div className="tauri-update-notes-label">Что нового:</div>
            <pre className="tauri-update-notes-body">{available.notes}</pre>
          </div>
        )}

        {phase === 'idle' && (
          <div className="tauri-update-actions">
            <button className="tauri-update-btn tauri-update-btn--primary" onClick={onInstall}>
              🚀 Обновить сейчас
            </button>
            <button className="tauri-update-btn tauri-update-btn--secondary" onClick={onDismiss}>
              Позже
            </button>
          </div>
        )}

        {(isDownloading || isInstalling || isReady) && (
          <div className="tauri-update-progress">
            <div className="tauri-update-progress-label">
              {isDownloading && `Скачивание... ${progress}%`}
              {isInstalling && 'Применение обновления...'}
              {isReady && 'Перезапуск приложения...'}
            </div>
            <div className="tauri-update-progress-bar">
              <div
                className="tauri-update-progress-fill"
                style={{ width: `${isDownloading ? progress : 100}%` }}
              />
            </div>
            {isDownloading && total > 0 && (
              <div className="tauri-update-progress-bytes">
                {formatBytes(downloaded)} / {formatBytes(total)}
              </div>
            )}
          </div>
        )}

        {hasError && (
          <div className="tauri-update-error">
            <div className="tauri-update-error-label">Ошибка обновления:</div>
            <pre className="tauri-update-error-body">{error}</pre>
            <div className="tauri-update-actions">
              <button className="tauri-update-btn tauri-update-btn--secondary" onClick={onDismiss}>
                Закрыть
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
