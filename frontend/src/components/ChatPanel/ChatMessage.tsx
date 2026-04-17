// Одно сообщение чата + вспомогательные компоненты: TypingIndicator, TaskStatusBadge, ThinkingTimer
import { memo, useState, useEffect } from 'react'
import { useStore, useTasks, useSelectedModel, type ChatMessage, type ChatModel, type TaskStatus } from '../../stores'
import { renderMarkdown } from '../../hooks/useChat'

// CSS-фильтры аватарки по модели
const AVATAR_FILTERS: Record<string, string> = {
  'gpt-5.4': 'hue-rotate(155deg) saturate(1.2) brightness(1.02)',
  'gpt-5.3-codex': 'hue-rotate(290deg) saturate(1.35) brightness(0.98)',
}

function formatTime(iso: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`
}

function attachmentIcon(contentType?: string, filename?: string): string {
  const ct = (contentType || '').toLowerCase()
  const ext = (filename || '').toLowerCase().split('.').pop() || ''
  if (ct.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) return '🖼️'
  if (ct === 'application/pdf' || ext === 'pdf') return '📕'
  if (['xlsx', 'xls', 'csv'].includes(ext) || ct.includes('spreadsheet') || ct.includes('excel')) return '📊'
  if (['docx', 'doc'].includes(ext) || ct.includes('word')) return '📝'
  if (['pptx', 'ppt'].includes(ext) || ct.includes('presentation')) return '📑'
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return '🗜️'
  if (['txt', 'md', 'log', 'json', 'xml', 'yaml', 'yml'].includes(ext)) return '📄'
  return '📎'
}

// Бейдж статуса задачи
function TaskStatusBadge({ status }: { status: TaskStatus }) {
  const labels: Record<TaskStatus, string> = {
    queued: 'В очереди',
    running: 'Выполняется',
    completed: 'Готово',
    failed: 'Ошибка',
    cancelled: 'Отменено',
  }
  return (
    <span className={`task-badge task-badge--${status}`}>
      {status === 'running' && <span className="task-badge__pulse" />}
      {labels[status]}
    </span>
  )
}

// Анимированный индикатор печатания
export function TypingIndicator() {
  return (
    <div className="typing-indicator" aria-label="Ассистент печатает...">
      <span /><span /><span />
    </div>
  )
}

// Оценки времени ответа по модели
const ESTIMATE_SECONDS: Record<string, number> = {
  'gpt-5.4': 16,
  'gpt-5.3-codex': 36,
}
const GITHUB_EXTRA = 4

export function ThinkingTimer({ phase, model, hasGitHub }: {
  phase: string | null
  model: string
  hasGitHub: boolean
}) {
  const estimate = (ESTIMATE_SECONDS[model] ?? 20) + (hasGitHub ? GITHUB_EXTRA : 0)
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    setElapsed(0)
    const timer = setInterval(() => setElapsed((e) => e + 1), 1000)
    return () => clearInterval(timer)
  }, [])

  const remaining = Math.max(0, estimate - elapsed)
  const overdue = elapsed > estimate

  let label: string
  if (phase) {
    label = phase
  } else if (overdue) {
    label = 'Ещё немного...'
  } else {
    label = 'ИИ думает...'
  }

  const timerText = overdue
    ? `+${elapsed - estimate}с`
    : `~${remaining}с`

  return (
    <div className={`chat-panel__thinking ${phase ? 'chat-panel__thinking--github' : ''} ${overdue ? 'chat-panel__thinking--overdue' : ''}`}>
      <div className="chat-panel__thinking-dot" />
      <span className="shimmer-text">{label}</span>
      <span className="chat-panel__thinking-timer">{timerText}</span>
    </div>
  )
}

// Компактные метрики CLI (statusline)
const CLI_CHAT_MODELS: ChatModel[] = ['gpt-5.4', 'gpt-5.3-codex']

function isCliChatModel(model: ChatModel): boolean {
  return CLI_CHAT_MODELS.includes(model)
}

export const StatuslineMetrics = memo(({ selectedModel }: { selectedModel: ChatModel }) => {
  const statusline = useStore(s => s.statusline)
  const setStatusline = useStore(s => s.setStatusline)
  const [statuslineChecked, setStatuslineChecked] = useState(false)
  const isCliModel = isCliChatModel(selectedModel)

  useEffect(() => {
    if (!isCliModel) {
      setStatusline(null)
      setStatuslineChecked(false)
      return
    }

    let active = true
    const fetchStatusline = () => {
      fetch('/api/system/statusline')
        .then(r => r.json())
        .then((data) => {
          if (!active) return
          const hasLimits = !!data && (
            data.rl_5h_pct != null ||
            data.rl_7d_pct != null ||
            data.context_used_pct != null
          )
          setStatusline(hasLimits && data.ts ? data : null)
          setStatuslineChecked(true)
        })
        .catch(() => {
          if (!active) return
          setStatusline(null)
          setStatuslineChecked(true)
        })
    }
    fetchStatusline()
    const interval = setInterval(fetchStatusline, 10_000)
    return () => {
      active = false
      clearInterval(interval)
    }
  }, [isCliModel, setStatusline])

  if (!isCliModel) return null

  if (!statusline) {
    if (!statuslineChecked) return null
    return (
      <div className="statusline-metrics">
        <span className="sl-pill sl-pill--muted" title="Codex CLI пока не отдал runtime metrics">
          CLI лимиты: н/д
        </span>
      </div>
    )
  }

  const has5h = statusline.rl_5h_pct != null
  const has7d = statusline.rl_7d_pct != null
  const hasContext = statusline.context_used_pct != null
  if (!has5h && !has7d && !hasContext) return null

  const remainingPct = (pct: number) => Math.max(0, 100 - pct)
  const leftColor = (pct: number) => pct <= 20 ? '#ef4444' : pct <= 50 ? '#eab308' : '#4ade80'

  let resetStr = ''
  if (statusline.rl_5h_reset) {
    const diff = statusline.rl_5h_reset - Math.floor(Date.now() / 1000)
    if (diff > 0) {
      const h = Math.floor(diff / 3600)
      const m = Math.floor((diff % 3600) / 60)
      resetStr = `сброс ${h}ч ${m}м`
    }
  }

  const fiveHourLeft = has5h ? remainingPct(statusline.rl_5h_pct!) : null
  const sevenDayLeft = has7d ? remainingPct(statusline.rl_7d_pct!) : null
  const contextLeft = hasContext ? remainingPct(statusline.context_used_pct) : null

  return (
    <div className="statusline-metrics">
      {fiveHourLeft != null && (
        <span className="sl-pill" title={`Остаток 5ч: ${fiveHourLeft}%`}>
          <span className="sl-label">5ч:</span>
          <span style={{ color: leftColor(fiveHourLeft) }}>{fiveHourLeft}%</span>
          {resetStr && <span className="sl-dim">{resetStr}</span>}
        </span>
      )}
      {sevenDayLeft != null && (
        <span className="sl-pill" title={`Остаток 7д: ${sevenDayLeft}%`}>
          <span className="sl-label">7д:</span>
          <span style={{ color: leftColor(sevenDayLeft) }}>{sevenDayLeft}%</span>
        </span>
      )}
      {contextLeft != null && (
        <span className="sl-pill" title={`Свободный контекст: ${contextLeft}%`}>
          <span className="sl-label">ctx:</span>
          <span style={{ color: leftColor(contextLeft) }}>{contextLeft}%</span>
        </span>
      )}
    </div>
  )
})
StatuslineMetrics.displayName = 'StatuslineMetrics'

// Одно сообщение
interface MessageProps {
  message: ChatMessage
  msgNumber: number
  onRefClick: (num: number) => void
}

export function Message({ message, msgNumber, onRefClick }: MessageProps) {
  const tasks = useTasks()
  const task = message.task_id ? tasks[message.task_id] : null
  const selectedModel = useSelectedModel()
  const isUser = message.role === 'user'
  const isSystem = message.role === 'system'

  return (
    <div className={`message message--${message.role} ${message.streaming ? 'message--streaming' : ''}`}>
      {/* Номер сообщения */}
      <button
        className="message__number"
        onClick={() => onRefClick(msgNumber)}
        title={`Вставить ссылку на сообщение #${msgNumber}`}
      >
        #{msgNumber}
      </button>

      {/* Аватар */}
      <div className="message__avatar" aria-hidden="true">
        {isUser ? (
          <img src="/avatar-nastya.png" alt="Настя" className="message__avatar-img" />
        ) : (
          <img
            src="/avatar-claude.png"
            alt="Ассистент"
            className="message__avatar-img"
            style={{ filter: AVATAR_FILTERS[selectedModel] ?? 'none' }}
          />
        )}
      </div>

      <div className="message__body">
        <div className="message__header">
          <span className="message__name">{isUser ? 'Настя' : 'Ассистент'}</span>
          <span className="message__time">{formatTime(message.created_at)}</span>
          {task && !isUser && <TaskStatusBadge status={task.status} />}
        </div>

        <div className="message__content">
          {isUser || isSystem ? (
            <p className="message__text">{message.content}</p>
          ) : message.streaming && !message.content ? (
            <TypingIndicator />
          ) : (
            <div
              className="message__markdown"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }}
            />
          )}
          {message.streaming && message.content && (
            <span className="message__stream-cursor" aria-hidden="true" />
          )}
          {/* Прикреплённые файлы */}
          {message.attachments && message.attachments.length > 0 && (
            <div className="message__attachments">
              {message.attachments.map((att, i) => (
                <div key={`${att.filename}-${i}`} className="message__attachment" title={att.filename}>
                  <span className="message__attachment-icon">{attachmentIcon(att.content_type, att.filename)}</span>
                  <span className="message__attachment-name">{att.filename}</span>
                  {att.size ? (
                    <span className="message__attachment-size">{formatSize(att.size)}</span>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
