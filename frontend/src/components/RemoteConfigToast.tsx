import { useEffect } from 'react'
import { useStore } from '../stores'
import './RemoteConfigToast.css'

/**
 * Всплывающее уведомление в правом верхнем углу когда бэкенд получил
 * новую версию remote-config.json с GitHub и применил её.
 * Автоматически скрывается через 8 секунд.
 */
export function RemoteConfigToast() {
  const notification = useStore((s) => s.remoteConfigNotification)
  const dismiss = useStore((s) => s.dismissRemoteConfigNotification)

  useEffect(() => {
    if (!notification.visible) return
    const timer = setTimeout(() => dismiss(), 8000)
    return () => clearTimeout(timer)
  }, [notification.visible, notification.version, dismiss])

  if (!notification.visible) return null

  return (
    <div className="remote-toast" onClick={dismiss}>
      <div className="remote-toast__icon">🚀</div>
      <div className="remote-toast__body">
        <div className="remote-toast__title">Обновление оркестратора</div>
        <div className="remote-toast__message">{notification.message}</div>
      </div>
      <button
        className="remote-toast__close"
        onClick={(e) => {
          e.stopPropagation()
          dismiss()
        }}
        aria-label="Закрыть"
      >
        ×
      </button>
    </div>
  )
}
