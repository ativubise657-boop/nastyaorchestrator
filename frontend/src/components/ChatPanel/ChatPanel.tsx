// ChatPanel — главная область чата.
// Оркестрирует: список сообщений, ввод текста, вложения, голосовой ввод, drag-drop, кнопки команд.
import { useEffect, useRef, useState, useCallback } from 'react'
import {
  useMessages,
  useStore,
  useSelectedModel,
  useChatFontSize,
  useWorkerOnline,
} from '../../stores'
import { useChat } from '../../hooks/useChat'
import { useVoiceInput } from '../../hooks/useVoiceInput'
import { SessionsSidebar } from '../SessionsSidebar'
import { ChatHeader } from './ChatHeader'
import { Message, ThinkingTimer, StatuslineMetrics } from './ChatMessage'
import { ModelSelector, FontSizeControl } from './ChatControls'
import '../ChatPanel.css'

// ===== Промпты быстрых кнопок =====

const BOOST_PROMPT = `BOOST — ревью и усиление предыдущего ответа.

Возьми последний ответ ассистента и проведи жёсткий инженерный разбор:

1. Дай оценку от 1 до 100.
2. Перечисли, что уже хорошо.
3. Покажи, что слабо или рискованно: архитектура, баги, UX, безопасность, пропущенные проверки.
4. Дай конкретный план, как довести ответ до максимально сильной версии.
5. В конце спроси: "Доделать? (да/нет)"

Если я отвечу "да", "делай" или "доделай", выполни план доработки end-to-end. Если нужен более широкий проход по нескольким файлам, работай шире и глубже, а не ограничивайся косметикой.

Важно: не просто перечисляй проблемы, а реально исправляй их при следующем шаге. Проверяй, что изменения действительно используются в приложении.`

const MARK_PROMPT = `МАРК — маркетинговый аналитик.

Ты — опытный маркетолог-аналитик. Задача: провести маркетинговое исследование или анализ по заданной теме.

ШАГ 1 — Уточнение.
Задай ОДИН краткий вопрос:
"По какой теме сделать исследование или анализ? Уточни: обзор рынка, конкурентный анализ, ЦА/ICP, позиционирование, контент-стратегия, воронка/конверсия, или что-то другое? Если уже есть продукт/бренд/ниша — назови."

Жди ответ. Без лишних вступлений, одно предложение-вопрос.

ШАГ 2 — Research plan.
После ответа — коротко (3-5 пунктов) скажи, что именно изучишь и в каком порядке. Дождись "ок/погнали" или корректировок.

ШАГ 3 — Исполнение.
Проведи реальный анализ. Используй доступные данные: приложенные документы, активные ссылки (если отмечены), веб-поиск (если доступен), общие знания.
Применяй подходящие фреймворки: SWOT, STP, 4P/7P, JTBD, Porter's 5 forces, AARRR — те что действительно релевантны, а не для галочки.

ШАГ 4 — Отчёт.
Структура:
- **Summary** (2-3 строки — суть)
- **Key insights** (3-7 ключевых находок, с цифрами/фактами где возможно)
- **Recommendations** (конкретные шаги — что делать, а не общие фразы)
- **Next steps** (что изучить дальше, если это пилотный заход)

Правила:
- Числа и факты > мнения. Если данных мало — скажи об этом и предложи как их добыть
- Без воды, без капитанских фраз про "важность анализа"
- Опирайся на отмеченные в сайдбаре документы и ссылки — если они есть, анализируй именно их
- Если Настя попросит углубиться в конкретный раздел — развивай, не начинай заново`

export function ChatPanel() {
  const messages = useMessages()
  const messagesLoading = useStore((s) => s.messagesLoading)
  const online = useWorkerOnline()
  const selectedModel = useSelectedModel()
  const { handleSend, sendingMessage, selectedProjectId, textareaRef, autoResize } = useChat()
  const chatFontSize = useChatFontSize()
  const setChatFontSize = useStore((s) => s.setChatFontSize)
  const taskPhase = useStore((s) => s.taskPhase)
  const voice = useVoiceInput('ru-RU')

  const tasks = useStore((s) => s.tasks)
  // Есть ли задача в работе
  const isThinking = Object.values(tasks).some(
    (t) => t.status === 'queued' || t.status === 'running'
  )
  // Стриминг уже начался (первый чанк)
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

  const attachImages = useCallback((files: File[]) => {
    if (!files.length) return
    setAttachedImages(prev => [...prev, ...files])
    const urls = files.map(f => f.type.startsWith('image/') ? URL.createObjectURL(f) : '')
    setImagePreviews(prev => [...prev, ...urls])
  }, [])

  const removeAttachedImage = useCallback((index: number) => {
    setAttachedImages(prev => prev.filter((_, i) => i !== index))
    setImagePreviews(prev => {
      if (prev[index]) URL.revokeObjectURL(prev[index])
      return prev.filter((_, i) => i !== index)
    })
  }, [])

  // Ctrl+V — вставка изображения из буфера
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

  // Drag-n-drop файлов на чат-панель
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

  // Голосовой ввод
  const startVoice = useCallback(() => {
    if (voice.isListening) return
    voice.start((text, isFinal) => {
      if (isFinal) {
        setInputText((prev) => prev + text)
        autoResize()
      }
    })
  }, [voice, autoResize])

  const toggleVoice = useCallback(() => {
    if (voice.isListening) {
      voice.stop()
    } else {
      startVoice()
    }
  }, [voice, startVoice])

  // Ctrl+Space — push-to-talk
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
      if (e.code === 'Space' && voice.isListening) {
        voice.stop()
      }
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

  // Автоскролл при новых сообщениях
  useEffect(() => {
    if (autoScroll) {
      requestAnimationFrame(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
      })
    }
  }, [messages.length, autoScroll])

  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    setAutoScroll(atBottom)
  }, [])

  // Авторесайз textarea
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

    // Загружаем прикреплённые файлы как документы проекта
    const sentAttachments: Array<{ filename: string; size: number; content_type: string; document_id?: string }> = []
    if (attachedImages.length > 0 && selectedProjectId) {
      for (const file of attachedImages) {
        try {
          const isScratch = file.type.startsWith('image/')
          const formData = new FormData()
          formData.append('file', file)
          const url = `/api/documents/${selectedProjectId}/upload${isScratch ? '?is_scratch=true' : ''}`
          const resp = await fetch(url, {
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
      useStore.getState().loadDocuments(selectedProjectId)
      imagePreviews.forEach(u => { if (u) URL.revokeObjectURL(u) })
      setAttachedImages([])
      setImagePreviews([])
    }

    await handleSend(text, undefined, sentAttachments.length ? sentAttachments : undefined)
    textareaRef.current?.focus()
    setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, 100)
  }

  // B8: блокируем отправку если worker офлайн — сообщение всё равно уйдёт в очередь,
  // но ответа не будет пока worker не поднимется. Явный баннер ниже объясняет причину.
  const canSend = (inputText.trim().length > 0 || attachedImages.length > 0) && !sendingMessage && !!selectedProjectId && online

  return (
    <div
      className={`chat-panel ${isDragging ? 'chat-panel--drag-over' : ''}`}
      style={{ '--chat-font-size': `${chatFontSize}px` } as React.CSSProperties}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Боковая панель сессий */}
      <SessionsSidebar />

      {/* Основная часть чата */}
      <div className="chat-panel__main">

        <ChatHeader />

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

        {/* Индикатор фазы с таймером */}
        {isThinking && !isStreaming && (
          <div className="chat-panel__thinking-row">
            <ThinkingTimer
              phase={taskPhase}
              model={selectedModel}
              hasGitHub={!!taskPhase}
            />
          </div>
        )}

        {/* Кнопка Стоп */}
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
            {/* Кнопка очистки ввода */}
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
            {/* Кнопка прикрепления файла */}
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
              title={!online ? 'Worker офлайн — дождись подключения' : 'Отправить (Enter)'}
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
                className="cmd-btn cmd-btn--mark"
                onClick={() => {
                  if (selectedProjectId && !sendingMessage) {
                    handleSend(MARK_PROMPT, 'gpt-5.3-codex')
                  }
                }}
                disabled={!selectedProjectId || sendingMessage}
                title="Маркетинговый аналитик: исследование/анализ темы с отчётом (SWOT, JTBD, ICP и т.п.)"
              >
                МАРК
              </button>
              <button
                className="cmd-btn cmd-btn--pre"
                onClick={() => {
                  if (selectedProjectId && !sendingMessage) {
                    handleSend('/pre')
                  }
                }}
                disabled={!selectedProjectId || sendingMessage}
                title="Сжать чат: сохранить контекст в файл, уменьшить историю"
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
                title="Восстановить контекст из сохранённого precompact"
              >
                ПОСТ
              </button>
            </div>
          </div>
        </div>

      </div>
    </div>
  )
}
