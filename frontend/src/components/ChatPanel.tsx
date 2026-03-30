import { useEffect, useRef, useState, useCallback, memo } from 'react'
import {
  useMessages,
  useTasks,
  useWorkerOnline,
  useSelectedModel,
  useChatFontSize,
  useStore,
  type ChatMessage,
  type TaskStatus,
  type StatuslineData,
} from '../stores'
import { useChat, renderMarkdown } from '../hooks/useChat'
import { useVoiceInput } from '../hooks/useVoiceInput'
import './ChatPanel.css'

/** Компактные метрики из Claude Code statusline */
const StatuslineMetrics = memo(() => {
  const statusline = useStore(s => s.statusline)
  const setStatusline = useStore(s => s.setStatusline)

  // Polling каждые 10 секунд
  useEffect(() => {
    const fetchStatusline = () => {
      fetch('/api/system/statusline')
        .then(r => r.json())
        .then((data: StatuslineData) => {
          if (data && data.ts) setStatusline(data)
        })
        .catch(() => {})
    }
    fetchStatusline()
    const interval = setInterval(fetchStatusline, 10_000)
    return () => clearInterval(interval)
  }, [setStatusline])

  if (!statusline) return null

  const has5h = statusline.rl_5h_pct != null
  const has7d = statusline.rl_7d_pct != null
  if (!has5h && !has7d) return null

  const rlColor = (pct: number) => pct >= 80 ? '#ef4444' : pct >= 50 ? '#eab308' : '#4ade80'

  let resetStr = ''
  if (statusline.rl_5h_reset) {
    const diff = statusline.rl_5h_reset - Math.floor(Date.now() / 1000)
    if (diff > 0) {
      const h = Math.floor(diff / 3600)
      const m = Math.floor((diff % 3600) / 60)
      resetStr = ` (${h}h${m}m)`
    }
  }

  const ramColor = statusline.ram_pct >= 85 ? '#ef4444' : statusline.ram_pct >= 70 ? '#eab308' : '#4ade80'

  return (
    <div className="statusline-metrics">
      {has5h && (
        <span className="sl-pill" title={`Rate limit 5h: ${statusline.rl_5h_pct}%${resetStr}`}>
          <span style={{ color: rlColor(statusline.rl_5h_pct!) }}>5h:{statusline.rl_5h_pct}%</span>
          {resetStr && <span className="sl-dim">{resetStr}</span>}
        </span>
      )}
      {has7d && (
        <span className="sl-pill" title={`Rate limit 7d: ${statusline.rl_7d_pct}%`}>
          <span style={{ color: rlColor(statusline.rl_7d_pct!) }}>7d:{statusline.rl_7d_pct}%</span>
        </span>
      )}
      <span className="sl-pill" title={`RAM: ${statusline.ram_used_gb}/${statusline.ram_total_gb}G`}>
        <span style={{ color: ramColor }}>
          RAM:{statusline.ram_used_gb}/{statusline.ram_total_gb}G {statusline.ram_pct}%
        </span>
      </span>
    </div>
  )
})
StatuslineMetrics.displayName = 'StatuslineMetrics'

// ===== Промпт для кнопки Boost =====

const BOOST_PROMPT = `⚡ BOOST — ревью и усиление предыдущего ответа.

Возьми предыдущий ответ ассистента (последний ответ в истории чата) и проведи глубокий анализ:

1. **Оценка** — поставь балл от 1 до 100, где:
   - 1-30: критические проблемы, нужно переделывать
   - 31-60: работает, но много недочётов
   - 61-80: хорошо, но есть что улучшить
   - 81-95: отлично, мелкие доработки
   - 96-100: идеально, лучше не придумаешь

2. **Что хорошо** — конкретные сильные стороны

3. **Что не так** — конкретные проблемы по категориям:
   - Архитектура (правильный ли компонент? правильные ли файлы?)
   - Код (баги, edge cases, дубликаты)
   - UX (удобство, доступность)
   - Безопасность

4. **План доработки до 100** — конкретные шаги, что исправить

5. В конце спроси: **"Доделать? (да/нет)"**

Если я отвечу "да", "делай", "доделай" — выполни план доработки используя agr режим.
Используй ag+ если нужно (5+ файлов). Доведи до 100 баллов.

ВАЖНО: Не просто описывай проблемы — ИСПРАВЛЯЙ их при доработке. Проверяй что компоненты реально рендерятся (ищи импорты в App.tsx). Не пиши код в мёртвые компоненты.`

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
// CSS-фильтры аватарки Claude по модели
const AVATAR_FILTERS: Record<string, string> = {
  opus: 'none',
  sonnet: 'hue-rotate(200deg) saturate(1.3)',
  haiku: 'saturate(0.2) brightness(0.8)',
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
            alt="Claude"
            className="message__avatar-img"
            style={{ filter: AVATAR_FILTERS[selectedModel] ?? 'none' }}
          />
        )}
      </div>

      <div className="message__body">
        {/* Имя + время */}
        <div className="message__header">
          <span className="message__name">{isUser ? 'Настя' : 'Claude'}</span>
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
        </div>
      </div>
    </div>
  )
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
  haiku: 10,
  sonnet: 20,
  opus: 45,
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
    label = 'Claude думает...'
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
    await handleSend(text)
    // Вернуть фокус в textarea
    textareaRef.current?.focus()
    // Принудительный скролл после отправки
    setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, 100)
  }

  const canSend = inputText.trim().length > 0 && !sendingMessage && !!selectedProjectId

  return (
    <div className="chat-panel" style={{ '--chat-font-size': `${chatFontSize}px` } as React.CSSProperties}>
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
            placeholder={
              selectedProjectId
                ? 'Напишите задачу... (Ctrl+Space — зажми и говори)'
                : 'Выберите проект чтобы начать'
            }
            rows={1}
            disabled={!selectedProjectId || sendingMessage}
            maxLength={10000}
            aria-label="Поле ввода задачи"
          />
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
          <StatuslineMetrics />
          <div className="cmd-buttons">
            <button
              className="cmd-btn cmd-btn--boost"
              onClick={() => {
                if (selectedProjectId && !sendingMessage) {
                  handleSend(BOOST_PROMPT, 'opus')
                }
              }}
              disabled={!selectedProjectId || sendingMessage}
              title="Оценить предыдущий ответ 1-100 и предложить доработку (Opus)"
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
              title="Сохранить уроки + CLAUDE.md"
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
const MODELS = ['haiku', 'sonnet', 'opus'] as const

function ModelSelector({ selected }: { selected: string }) {
  return (
    <div className="model-selector">
      {MODELS.map((m) => (
        <button
          key={m}
          className={`model-selector__btn ${m === selected ? 'model-selector__btn--active' : ''}`}
          onClick={() => useStore.getState().setSelectedModel(m)}
        >
          {m}
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
