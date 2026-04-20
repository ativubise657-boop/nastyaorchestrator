import { useEffect } from 'react'
import { useStore } from '../stores'
import './ErrorToast.css'

/**
 * Универсальный toast для ошибок / инфо / успех.
 * Показывается снизу-справа, auto-dismiss через 5 секунд.
 * Пример вызова из store: get().showToast({ kind: 'error', text: '...' })
 */
export function ErrorToast() {
  const toast = useStore((s) => s.toastMessage)
  const hideToast = useStore((s) => s.hideToast)

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => hideToast(), 5000)
    return () => clearTimeout(timer)
  }, [toast, hideToast])

  if (!toast) return null

  // Иконки по типу уведомления
  const icons: Record<string, string> = {
    error: '⚠️',
    info: 'ℹ️',
    success: '✅',
  }

  return (
    <div
      className={`error-toast error-toast--${toast.kind}`}
      onClick={hideToast}
      role="alert"
      aria-live="assertive"
    >
      <div className="error-toast__icon">{icons[toast.kind] ?? '⚠️'}</div>
      <div className="error-toast__body">
        <div className="error-toast__message">{toast.text}</div>
      </div>
      <button
        className="error-toast__close"
        onClick={(e) => {
          e.stopPropagation()
          hideToast()
        }}
        aria-label="Закрыть"
      >
        ×
      </button>
    </div>
  )
}
