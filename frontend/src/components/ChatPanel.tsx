import { useEffect, useRef, useState, useCallback, memo } from 'react'
import {
  useMessages,
  useTasks,
  useWorkerOnline,
  useSelectedModel,
  useChatFontSize,
  useStore,
  type ChatMessage,
  type ChatModel,
  type TaskStatus,
  type StatuslineData,
} from '../stores'
import { useChat, renderMarkdown } from '../hooks/useChat'
import { useVoiceInput } from '../hooks/useVoiceInput'
import './ChatPanel.css'

/** Компактные метрики рантайма */
const CLI_CHAT_MODELS: ChatModel[] = ['gpt-5.4', 'gpt-5.3-codex']

function isCliChatModel(model: ChatModel): boolean {
  return CLI_CHAT_MODELS.includes(model)
}

const StatuslineMetrics = memo(({ selectedModel }: { selectedModel: ChatModel }) => {
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
        .then((data: StatuslineData) => {
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

// ===== Промпт для кнопки Boost =====

const BOOST_PROMPT = `BOOST — ревью и усиление предыдущего ответа.

Возьми последний ответ ассистента и проведи жёсткий инженерный разбор:

1. Дай оценку от 1 до 100.
2. Перечисли, что уже хорошо.
3. Покажи, что слабо или рискованно: архитектура, баги, UX, безопасность, пропущенные проверки.
4. Дай конкретный план, как довести ответ до максимально сильной версии.
5. В конце спроси: "Доделать? (да/нет)"

Если я отвечу "да", "делай" или "доделай", выполни план доработки end-to-end. Если нужен более широкий проход по нескольким файлам, работай шире и глубже, а не ограничивайся косметикой.

Важно: не просто перечисляй проблемы, а реально исправляй их при следующем шаге. Проверяй, что изменения действительно используются в приложении.`

// ===== Типизированный helper для статуса задачи =====
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

// ===== Индикатор печатания =====
function TypingIndicator() {
  return (
    <div className="typing-indicator" aria-label="Ассистент печатает...">
      <span /><span /><span />
    </div>
  )
}

// ===== Одно сообщение — full-width строка =====
// CSS-фильтры аватарки Codex по модели
const AVATAR_FILTERS: Record<string, string> = {
  'glm-5-turbo': 'none',
  'glm-4.7-flash': 'hue-rotate(38deg) saturate(1.35)',
  'gpt-5.4-nano': 'hue-rotate(205deg) saturate(1.2) brightness(0.95)',
  'gpt-5.4': 'hue-rotate(155deg) saturate(1.2) brightness(1.02)',
  'gpt-5.3-codex': 'hue-rotate(290deg) saturate(1.35) brightness(0.98)',
}

function Message({ message, msgNumber, onRefClick }: {
  message: ChatMessage
  msgNumber: number
  onRefClick: (num: number) => void
}) {
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
            alt="Codex"
            className="message__avatar-img"
            style={{ filter: AVATAR_FILTERS[selectedModel] ?? 'none' }}
          />
        )}
      </div>

      <div className="message__body">
        {/* Имя + время */}
        <div className="message__header">
          <span className="message__name">{isUser ? 'Настя' : 'Codex'}</span>
          <span className="message__time">{formatTime(message.created_at)}</span>
          {task && !isUser && <TaskStatusBadge status={task.status} />}
        </div>

        {/* Контент */}
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
          {/* Прикреплённые файлы (PDF, изображения, XLSX, DOCX) */}
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

// Иконка по типу файла
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

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`
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

// ===== Оценка времени ответа по модели =====
const ESTIMATE_SECONDS: Record<string, number> = {
  'glm-4.7-flash': 8,
  'glm-5-turbo': 18,
  'gpt-5.4-nano': 12,
  'gpt-5.4': 16,
  'gpt-5.3-codex': 36,
}
const GITHUB_EXTRA = 4 // доп. секунды на GitHub API

function ThinkingTimer({ phase, model, hasGitHub }: {
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

  // Текст фазы
  let label: string
  if (phase) {
    label = phase
  } else if (overdue) {
    label = 'Ещё немного...'
  } else {
    label = 'Codex думает...'
  }

  // Таймер
  const timerText = overdue
    ? `+${elapsed - estimate}с`
    : `~${remaining}с`

  return (
    <div className={`chat-panel__thinking ${phase ? 'chat-panel__thinking--github' : ''} ${overdue ? 'chat-panel__thinking--overdue' : ''}`}>
      <div className="chat-panel__thinking-dot" />
      <span>{label}</span>
      <span className="chat-panel__thinking-timer">{timerText}</span>
    </div>
  )
}

// ===== ChatPanel =====
export function ChatPanel() {
  const messages = useMessages()
  const messagesLoading = useStore((s) => s.messagesLoading)
  const online = useWorkerOnline()
  const selectedModel = useSelectedModel()
  const { handleSend, sendingMessage, selectedProjectId, textareaRef, autoResize } = useChat()
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const clearMessages = useStore((s) => s.clearMessages)
  const chatFontSize = useChatFontSize()
  const setChatFontSize = useStore((s) => s.setChatFontSize)
  const taskPhase = useStore((s) => s.taskPhase)
  const voice = useVoiceInput('ru-RU')

  const tasks = useTasks()
  // Есть ли задача в работе (queued или running)
  const isThinking = Object.values(tasks).some(
    (t) => t.status === 'queued' || t.status === 'running'
  )
  // Есть ли стриминг (первый чанк уже пришёл для ТЕКУЩЕЙ running задачи)
  const isStreaming = Object.values(tasks).some(
    (t) => t.status === 'running' && !!t.streamBuffer
  )

  const [inputText, setInputText] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)

  // Прикреплённые файлы (paste / drag-n-drop / кнопка 📎)
  const [attachedImages, setAttachedImages] = useState<File[]>([])
  const [imagePreviews, setImagePreviews] = useState<string[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)
  // Модель до автосвитча — для восстановления после отправки
  const modelBeforeAutoSwitch = useRef<ChatModel | null>(null)

  // Добавить файлы в очередь на отправку
  const attachImages = useCallback((files: File[]) => {
    if (!files.length) return
    setAttachedImages(prev => [...prev, ...files])
    // Генерируем превью: для изображений — URL, для остальных — пустая строка
    const urls = files.map(f => f.type.startsWith('image/') ? URL.createObjectURL(f) : '')
    setImagePreviews(prev => [...prev, ...urls])
    // Автопереключение на Gemini Flash при прикреплении не-изображений
    const hasDocuments = files.some(f => !f.type.startsWith('image/'))
    if (hasDocuments) {
      const currentModel = useStore.getState().selectedModel
      if (currentModel !== 'gemini-2.5-flash') {
        modelBeforeAutoSwitch.current = currentModel
        useStore.getState().setSelectedModel('gemini-2.5-flash')
      }
    }
  }, [])

  // Удалить прикреплённый файл по индексу
  const removeAttachedImage = useCallback((index: number) => {
    setAttachedImages(prev => {
      const next = prev.filter((_, i) => i !== index)
      // Если убрали все документы — вернуть модель
      if (next.length === 0 && modelBeforeAutoSwitch.current) {
        useStore.getState().setSelectedModel(modelBeforeAutoSwitch.current)
        modelBeforeAutoSwitch.current = null
      }
      return next
    })
    setImagePreviews(prev => {
      if (prev[index]) URL.revokeObjectURL(prev[index])
      return prev.filter((_, i) => i !== index)
    })
  }, [])

  // Ctrl+V — вставка изображения из буфера обмена
  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items
    if (!items) return
    const imageFiles: File[] = []
    for (const item of Array.from(items)) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile()
        if (file) imageFiles.push(file)
      }
    }
    if (imageFiles.length > 0) {
      e.preventDefault()
      attachImages(imageFiles)
    }
  }, [attachImages])

  // Drag-n-drop изображений на чат-панель
  const [isDragging, setIsDragging] = useState(false)
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(true)
  }, [])
  const handleDragLeave = useCallback(() => setIsDragging(false), [])
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    const files = Array.from(e.dataTransfer.files)
    attachImages(files)
  }, [attachImages])

  // Голосовой ввод: старт записи
  const startVoice = useCallback(() => {
    if (voice.isListening) return
    voice.start((text, isFinal) => {
      if (isFinal) {
        setInputText((prev) => prev + text)
        autoResize()
      }
    })
  }, [voice, autoResize])

  // Toggle для кнопки микрофона
  const toggleVoice = useCallback(() => {
    if (voice.isListening) {
      voice.stop()
    } else {
      startVoice()
    }
  }, [voice, startVoice])

  // Ctrl+Space — push-to-talk (только когда фокус в textarea)
  // Зажми Ctrl+Space — запись пошла. Отпусти Space (Ctrl держи) — пауза.
  // Снова Space — запись продолжится. Отпусти Ctrl — конец.
  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.code === 'Space' && e.ctrlKey && !e.repeat) {
        e.preventDefault()
        startVoice()
      }
    }
    const handleKeyUp = (e: KeyboardEvent) => {
      // Отпустил Space — пауза записи
      if (e.code === 'Space' && voice.isListening) {
        voice.stop()
      }
      // Отпустил Ctrl — полная остановка
      if ((e.key === 'Control' || e.code === 'ControlLeft' || e.code === 'ControlRight') && voice.isListening) {
        voice.stop()
      }
    }
    textarea.addEventListener('keydown', handleKeyDown)
    textarea.addEventListener('keyup', handleKeyUp)
    return () => {
      textarea.removeEventListener('keydown', handleKeyDown)
      textarea.removeEventListener('keyup', handleKeyUp)
    }
  }, [startVoice, voice, textareaRef])

  // Автоскролл вниз при новых сообщениях
  useEffect(() => {
    if (autoScroll) {
      // requestAnimationFrame чтобы DOM успел обновиться
      requestAnimationFrame(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
      })
    }
  }, [messages.length, autoScroll])

  // Определяем нужно ли автоскроллить (если пользователь не уехал вверх)
  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    setAutoScroll(atBottom)
  }, [])

  // Авторесайз textarea при каждом изменении текста (после обновления DOM)
  useEffect(() => {
    autoResize()
  }, [inputText, autoResize])

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInputText(e.target.value)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submitMessage()
    }
  }

  const submitMessage = async () => {
    if (!inputText.trim() || sendingMessage) return
    const text = inputText
    setInputText('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
    setAutoScroll(true)

    // Загрузить прикреплённые файлы как документы проекта (перед отправкой)
    // и собрать метаданные для отображения в bubble сообщения
    const sentAttachments: Array<{ filename: string; size: number; content_type: string; document_id?: string }> = []
    if (attachedImages.length > 0 && selectedProjectId) {
      for (const file of attachedImages) {
        try {
          const formData = new FormData()
          formData.append('file', file)
          const resp = await fetch(`/api/documents/${selectedProjectId}/upload`, {
            method: 'POST',
            body: formData,
          })
          let docId: string | undefined
          if (resp.ok) {
            try {
              const data = await resp.json()
              docId = data?.id
            } catch { /* ignore */ }
          }
          sentAttachments.push({
            filename: file.name,
            size: file.size,
            content_type: file.type || '',
            document_id: docId,
          })
        } catch (err) {
          console.warn('Ошибка загрузки вложения:', err)
        }
      }
      // Обновить список документов чтобы worker увидел новые
      useStore.getState().loadDocuments(selectedProjectId)
      // Очистить превью
      imagePreviews.forEach(u => { if (u) URL.revokeObjectURL(u) })
      setAttachedImages([])
      setImagePreviews([])
      // Вернуть модель если был автосвитч
      if (modelBeforeAutoSwitch.current) {
        useStore.getState().setSelectedModel(modelBeforeAutoSwitch.current)
        modelBeforeAutoSwitch.current = null
      }
    }

    await handleSend(text, undefined, sentAttachments.length ? sentAttachments : undefined)
    // Вернуть фокус в textarea
    textareaRef.current?.focus()
    // Принудительный скролл после отправки
    setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, 100)
  }

  const canSend = (inputText.trim().length > 0 || attachedImages.length > 0) && !sendingMessage && !!selectedProjectId

  return (
    <div
      className={`chat-panel ${isDragging ? 'chat-panel--drag-over' : ''}`}
      style={{ '--chat-font-size': `${chatFontSize}px` } as React.CSSProperties}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Хедер чат-панели */}
      <div className="chat-panel__header">
        {/* Кнопка сайдбара (мобайл) */}
        <button
          className="chat-panel__menu-btn"
          onClick={toggleSidebar}
          aria-label={sidebarOpen ? 'Скрыть панель' : 'Открыть панель'}
          title="Панель проектов"
        >
          <svg viewBox="0 0 16 16" fill="none">
            <path d="M2 4h12M2 8h12M2 12h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>

        <ProjectBreadcrumb />
      </div>

      {/* Список сообщений */}
      <div
        className="chat-panel__messages"
        ref={scrollContainerRef}
        onScroll={handleScroll}
        role="log"
        aria-live="polite"
        aria-label="История чата"
      >
        {messagesLoading && (
          <div className="chat-panel__loading">
            <span className="chat-panel__spinner" />
            <span>Загружаем историю...</span>
          </div>
        )}

        {!messagesLoading && messages.length === 0 && selectedProjectId && (
          <div className="chat-panel__welcome">
            <div className="chat-panel__welcome-icon" aria-hidden="true">
              <svg viewBox="0 0 40 40" fill="none">
                <circle cx="20" cy="20" r="15" stroke="currentColor" strokeWidth="1.5" />
                <circle cx="20" cy="20" r="5" fill="currentColor" />
              </svg>
            </div>
            <h3>Начните диалог</h3>
            <p>Напишите задачу — система её выполнит</p>
          </div>
        )}

        {messages.map((msg, idx) => (
          <Message
            key={msg.id}
            message={msg}
            msgNumber={idx + 1}
            onRefClick={(num) => {
              const ref = `[сообщение #${num}] `
              setInputText((prev) => prev + ref)
              textareaRef.current?.focus()
            }}
          />
        ))}

        <div ref={messagesEndRef} aria-hidden="true" />
      </div>

      {/* Предупреждение об офлайн воркере */}
      {!online && (
        <div className="chat-panel__offline-warning" role="alert">
          <svg viewBox="0 0 16 16" fill="none">
            <path d="M8 6v3M8 11v.5M3 13h10L8 4 3 13z" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Worker офлайн. Задача будет выполнена когда worker подключится.
        </div>
      )}

      {/* Индикатор фазы с таймером (до начала стриминга) */}
      {isThinking && !isStreaming && (
        <div className="chat-panel__thinking-row">
          <ThinkingTimer
            phase={taskPhase}
            model={selectedModel}
            hasGitHub={!!taskPhase}
          />
        </div>
      )}

      {/* Кнопка Стоп — видна всё время пока задача выполняется */}
      {isThinking && (
        <div className="chat-panel__stop-row">
          <button
            className="chat-panel__stop-btn"
            onClick={() => useStore.getState().cancelTask()}
            title="Остановить задачу"
          >
            ■ Стоп
          </button>
        </div>
      )}

      {/* Input area */}
      <div className="chat-panel__input-area">
        {/* Кнопка нового чата — над инпутом */}
        {selectedProjectId && messages.length > 0 && (
          <div className="chat-panel__new-chat-bar">
            <button
              className="chat-panel__new-chat-btn"
              onClick={clearMessages}
              title="Начать новый чат (история сохранится)"
            >
              <svg viewBox="0 0 14 14" fill="none">
                <path d="M7 2v10M2 7h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
              Новый чат
            </button>
          </div>
        )}
        {/* Превью прикреплённых файлов */}
        {imagePreviews.length > 0 && (
          <div className="chat-input__previews">
            {imagePreviews.map((url, i) => (
              <div key={url || `file-${i}`} className="chat-input__preview">
                {url ? (
                  <img src={url} alt={attachedImages[i]?.name || 'preview'} />
                ) : (
                  <div className="chat-input__preview-file">
                    <span className="chat-input__preview-file-icon">📄</span>
                  </div>
                )}
                <button
                  className="chat-input__preview-remove"
                  onClick={() => removeAttachedImage(i)}
                  title="Убрать"
                >
                  <svg viewBox="0 0 12 12" fill="none">
                    <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                  </svg>
                </button>
                <span className="chat-input__preview-name">{attachedImages[i]?.name}</span>
              </div>
            ))}
          </div>
        )}
        <div className="chat-input" onClick={() => textareaRef.current?.focus()}>
          {/* Кнопка очистки ввода — перед текстом */}
          {inputText.length > 0 && (
            <button
              className="chat-input__clear-btn"
              onClick={() => {
                setInputText('')
                if (textareaRef.current) textareaRef.current.style.height = 'auto'
                textareaRef.current?.focus()
              }}
              aria-label="Очистить ввод"
              title="Очистить"
            >
              <svg viewBox="0 0 16 16" fill="none">
                <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </button>
          )}
          <textarea
            ref={textareaRef}
            className="chat-input__textarea"
            value={inputText}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={
              selectedProjectId
                ? 'Напишите задачу... (Ctrl+V — вставить скриншот)'
                : 'Выберите проект чтобы начать'
            }
            rows={1}
            disabled={!selectedProjectId || sendingMessage}
            maxLength={10000}
            aria-label="Поле ввода задачи"
          />
          {/* Кнопка прикрепления файла (📎) */}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*,.pdf,.xlsx,.xls,.csv,.docx,.doc,.pptx"
            multiple
            style={{ display: 'none' }}
            onChange={(e) => {
              if (e.target.files) {
                attachImages(Array.from(e.target.files))
              }
              e.target.value = ''
            }}
          />
          <button
            className="chat-input__attach-btn"
            onClick={() => fileInputRef.current?.click()}
            disabled={!selectedProjectId || sendingMessage}
            aria-label="Прикрепить файл"
            title="Прикрепить файл (изображение, PDF, Excel)"
          >
            <svg viewBox="0 0 20 20" fill="none">
              <path d="M17.5 9.5l-7.8 7.8a4.2 4.2 0 01-6-6L11.5 3.5a2.8 2.8 0 014 4l-7.8 7.8a1.4 1.4 0 01-2-2l7-7"
                stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
          {/* Кнопка голосового ввода */}
          {voice.isSupported && (
            <button
              className={`chat-input__voice-btn ${voice.isListening ? 'chat-input__voice-btn--active' : ''}`}
              onClick={toggleVoice}
              disabled={!selectedProjectId || sendingMessage}
              aria-label={voice.isListening ? 'Остановить запись' : 'Голосовой ввод'}
              title={voice.isListening ? 'Остановить запись' : 'Голосовой ввод (русский)'}
            >
              {voice.isListening ? (
                <svg viewBox="0 0 20 20" fill="none">
                  <rect x="5" y="5" width="10" height="10" rx="2" fill="currentColor" />
                </svg>
              ) : (
                <svg viewBox="0 0 20 20" fill="none">
                  <rect x="7" y="2" width="6" height="11" rx="3" stroke="currentColor" strokeWidth="1.5" />
                  <path d="M4 10a6 6 0 0 0 12 0" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                  <path d="M10 16v2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              )}
              {voice.isListening && <span className="chat-input__voice-pulse" />}
            </button>
          )}
          <button
            className={`chat-input__send-btn ${canSend ? 'chat-input__send-btn--active' : ''}`}
            onClick={submitMessage}
            disabled={!canSend}
            aria-label="Отправить сообщение"
            title="Отправить (Enter)"
          >
            {sendingMessage ? (
              <span className="chat-input__spinner" />
            ) : (
              <svg viewBox="0 0 20 20" fill="none">
                <path
                  d="M4 10h12M11 5l5 5-5 5"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            )}
          </button>
        </div>
        {/* Промежуточный текст голосового ввода */}
        {voice.isListening && voice.interimText && (
          <div className="chat-input__voice-interim">
            {voice.interimText}
          </div>
        )}
        <div className="chat-panel__bottom-bar">
          <ModelSelector selected={selectedModel} />
          <FontSizeControl size={chatFontSize} onChange={setChatFontSize} />
          <StatuslineMetrics selectedModel={selectedModel} />
          <div className="cmd-buttons">
            <button
              className="cmd-btn cmd-btn--boost"
              onClick={() => {
                if (selectedProjectId && !sendingMessage) {
                  handleSend(BOOST_PROMPT, 'gpt-5.3-codex')
                }
              }}
              disabled={!selectedProjectId || sendingMessage}
              title="Оценить предыдущий ответ 1-100 и предложить доработку (GPT-5 Thinking через CLI)"
            >
              ⚡ BOOST
            </button>

            <span className="cmd-buttons__separator" />

            <button
              className="cmd-btn cmd-btn--lai"
              onClick={() => {
                if (selectedProjectId && !sendingMessage) {
                  handleSend('/lai')
                }
              }}
              disabled={!selectedProjectId || sendingMessage}
              title="Сохранить уроки + инструкции проекта"
            >
              LAI
            </button>
            <button
              className="cmd-btn cmd-btn--pre"
              onClick={() => {
                if (selectedProjectId && !sendingMessage) {
                  handleSend('/pre')
                }
              }}
              disabled={!selectedProjectId || sendingMessage}
              title="Скомпоновать чат (сохранить контекст)"
            >
              ПРЕ
            </button>
            <button
              className="cmd-btn cmd-btn--post"
              onClick={() => {
                if (selectedProjectId && !sendingMessage) {
                  handleSend('/post')
                }
              }}
              disabled={!selectedProjectId || sendingMessage}
              title="Раскомпоновать чат (восстановить контекст)"
            >
              ПОСТ
            </button>
            <button
              className="cmd-btn cmd-btn--rev"
              onClick={() => {
                if (selectedProjectId && !sendingMessage) {
                  handleSend('/rev')
                }
              }}
              disabled={!selectedProjectId || sendingMessage}
              title="Анализ изменений + рекомендация ревью"
            >
              REV
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// Переключатель модели
const MODEL_OPTIONS: Array<{ id: ChatModel; label: string }> = [
  { id: 'glm-4.7-flash', label: 'GLM 4.7 Flash' },
  { id: 'glm-5-turbo', label: 'GLM 5 Turbo' },
  { id: 'gpt-5.4-nano', label: 'GPT 5.4 Nano' },
  { id: 'gpt-5.4', label: 'GPT 5' },
  { id: 'gpt-5.3-codex', label: 'GPT 5 Thinking' },
  { id: 'gemini-2.5-flash', label: 'Gemini Flash' },
]

function ModelSelector({ selected }: { selected: ChatModel }) {
  return (
    <div className="model-selector">
      {MODEL_OPTIONS.map((model) => (
        <button
          key={model.id}
          className={`model-selector__btn ${model.id === selected ? 'model-selector__btn--active' : ''}`}
          onClick={() => useStore.getState().setSelectedModel(model.id)}
        >
          {model.label}
        </button>
      ))}
    </div>
  )
}

// Контрол размера шрифта
function FontSizeControl({ size, onChange }: { size: number; onChange: (s: number) => void }) {
  return (
    <div className="font-size-control">
      <button
        className="font-size-control__btn"
        onClick={() => onChange(size - 2)}
        title="Уменьшить шрифт"
      >
        A−
      </button>
      <span className="font-size-control__value">{size}</span>
      <button
        className="font-size-control__btn"
        onClick={() => onChange(size + 2)}
        title="Увеличить шрифт"
      >
        A+
      </button>
    </div>
  )
}

// Хлебная крошка с именем проекта
function ProjectBreadcrumb() {
  const selectedProject = useStore((s) =>
    s.projects.find((p) => p.id === s.selectedProjectId),
  )

  if (!selectedProject) return <span className="chat-panel__no-project">Выберите проект</span>

  return (
    <div className="chat-panel__breadcrumb">
      <span className="chat-panel__project-icon" aria-hidden="true">
        {selectedProject.name.charAt(0).toUpperCase()}
      </span>
      <span className="chat-panel__project-name">{selectedProject.name}</span>
    </div>
  )
}
