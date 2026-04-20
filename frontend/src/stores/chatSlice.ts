import { StateCreator } from 'zustand'
import { apiFetch } from './_shared'
import type { AppStore } from './index'

// ===== Типы чата =====

export type MessageRole = 'user' | 'assistant' | 'system'
export type ChatModel = 'gpt-5.4' | 'gpt-5.3-codex'

// Дефолт модели — gpt-5.4 (codex CLI через opera-proxy → OpenAI).
// Был glm-5-turbo (aitunnel) — переключили потому что Дима хочет codex.
export const DEFAULT_CHAT_MODEL: ChatModel = 'gpt-5.4'

// Любое legacy значение (glm/gemini/nano/…) нормализуется в одну из двух моделей.
// Reasoning — для задач где нужна глубина, GPT 5 — для всего остального.
export const LEGACY_MODEL_ALIASES: Record<string, ChatModel> = {
  gpt5: 'gpt-5.4',
  'gpt5-thinking': 'gpt-5.3-codex',
  'gpt5-reasoning': 'gpt-5.3-codex',
  opus: 'gpt-5.3-codex',
  thinking: 'gpt-5.3-codex',
  reasoning: 'gpt-5.3-codex',
  default: DEFAULT_CHAT_MODEL,
  max: DEFAULT_CHAT_MODEL,
  // снятые модели маппим на GPT 5 (дефолт)
  'glm-4.7-flash': 'gpt-5.4',
  'glm-5-turbo': 'gpt-5.4',
  'gpt-5.4-nano': 'gpt-5.4',
  'gemini-2.5-flash': 'gpt-5.4',
  glm: 'gpt-5.4',
  gemini: 'gpt-5.4',
  haiku: 'gpt-5.4',
  sonnet: 'gpt-5.4',
  nano: 'gpt-5.4',
  mini: 'gpt-5.4',
}

export function normalizeChatModel(model?: string | null): ChatModel {
  if (!model) return DEFAULT_CHAT_MODEL
  if (model === 'gpt-5.4' || model === 'gpt-5.3-codex') {
    return model
  }
  return LEGACY_MODEL_ALIASES[model] ?? DEFAULT_CHAT_MODEL
}

export interface ChatAttachment {
  filename: string
  size?: number
  content_type?: string
  document_id?: string | null
}

export interface ChatMessage {
  id: string
  role: MessageRole
  content: string
  task_id: string | null
  attachments?: ChatAttachment[]
  created_at: string
  // Временные поля для стриминга (только в UI, не из API)
  streaming?: boolean
}

// ===== Интерфейс slice =====

export interface ChatSlice {
  // Чат
  messages: ChatMessage[]
  messagesLoading: boolean
  sendingMessage: boolean
  loadMessages: (sessionId: string) => Promise<void>
  sendMessage: (message: string, modelOverride?: string, attachments?: ChatAttachment[]) => Promise<void>
  clearMessages: () => Promise<void>
  addMessage: (message: ChatMessage) => void
  updateMessageContent: (id: string, content: string) => void
  setMessageStreaming: (id: string, streaming: boolean) => void
}

// ===== Реализация slice =====

export const createChatSlice: StateCreator<AppStore, [], [], ChatSlice> = (set, get) => ({
  messages: [],
  messagesLoading: false,
  sendingMessage: false,

  // Кнопка «Новый чат» — создаёт новую сессию вместо простой очистки
  clearMessages: async () => {
    const { selectedProjectId, createSession } = get()
    if (!selectedProjectId) return
    await createSession(selectedProjectId)
  },

  loadMessages: async (sessionId) => {
    set({ messagesLoading: true })
    try {
      const messages = await apiFetch<ChatMessage[]>(
        `/api/chat/history/${sessionId}?limit=100`,
      )
      set({ messages, messagesLoading: false })
    } catch (err) {
      set({ messagesLoading: false })
      console.error('loadMessages failed:', err)
      // Определяем причину — network error или backend ответил с ошибкой
      const isNetwork = err instanceof TypeError && err.message.includes('fetch')
      const text = isNetwork
        ? 'Backend не отвечает. Проверь что приложение запущено.'
        : `Не удалось загрузить историю чата: ${err instanceof Error ? err.message : String(err)}`
      get().showToast({ kind: 'error', text })
    }
  },

  sendMessage: async (message, modelOverride?, attachments?) => {
    const { selectedProjectId, selectedModel, selectedMode, documents, selectedDocId, docRefsSelected, links, linkRefsSelected } = get()
    const model = normalizeChatModel(modelOverride || selectedModel)
    if (!selectedProjectId) return

    // Защита от race condition: если сессии ещё нет — создаём перед отправкой
    if (!get().currentSessionId) {
      await get().createSession(selectedProjectId)
    }
    const currentSessionId = get().currentSessionId

    // Собираем финальный список attachments:
    // 1) явные (переданные аргументом)
    // 2) отмеченные чекбоксом в сайдбаре (docRefsSelected)
    // 3) активный для превью (selectedDocId) — тот, что кликнут в панели
    // Дедуп по document_id.
    const atts: ChatAttachment[] = []
    const seenIds = new Set<string>()
    const pushDoc = (docId: string) => {
      if (seenIds.has(docId)) return
      const doc = documents.find((d) => d.id === docId)
      if (!doc) return
      atts.push({
        filename: doc.filename,
        size: doc.size,
        content_type: doc.content_type,
        document_id: doc.id,
      })
      seenIds.add(docId)
    }
    for (const a of attachments || []) {
      atts.push(a)
      if (a.document_id) seenIds.add(a.document_id)
    }
    for (const docId of docRefsSelected) pushDoc(docId)
    if (selectedDocId) pushDoc(selectedDocId)

    // Если отмечены ссылки чекбоксами — добавляем их блоком в начало API-сообщения.
    // В UI показываем оригинальный текст (без служебного блока), Codex получает с префиксом.
    const activeLinks = links.filter((l) => linkRefsSelected.has(l.id))
    const linksBlock = activeLinks.length
      ? '[Активные ссылки для анализа — используй именно их как основной источник]\n' +
        activeLinks.map((l) => `- ${l.url}${l.title ? ` — ${l.title}` : ''}${l.description ? ` (${l.description})` : ''}`).join('\n') +
        '\n\n'
      : ''
    const apiMessage = linksBlock + message

    // Оптимистично добавляем сообщение пользователя (с attachments)
    const tempUserMsg: ChatMessage = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content: message,
      task_id: null,
      attachments: atts,
      created_at: new Date().toISOString(),
    }
    set((state) => ({
      messages: [...state.messages, tempUserMsg],
      sendingMessage: true,
    }))

    try {
      const result = await apiFetch<{ task_id: string; message_id: string }>(
        '/api/chat/send',
        {
          method: 'POST',
          body: JSON.stringify({
            project_id: selectedProjectId,
            session_id: currentSessionId,
            message: apiMessage,
            model,
            mode: selectedMode,   // B4: передаём режим в backend (auto/ag+/rev/solo)
            attachments: atts,
          }),
        },
      )

      // Заменяем временное сообщение реальным + сбрасываем чекбоксы документов/ссылок
      // (selectedDocId оставляем — это "активный для превью", он не должен слетать)
      const nowIso = new Date().toISOString()
      set((state) => ({
        messages: state.messages.map((m) =>
          m.id === tempUserMsg.id
            ? { ...m, id: result.message_id, task_id: result.task_id }
            : m,
        ),
        sendingMessage: false,
        currentTaskId: result.task_id,
        docRefsSelected: new Set<string>(),
        linkRefsSelected: new Set<string>(),
        tasks: {
          ...state.tasks,
          [result.task_id]: {
            id: result.task_id,
            status: 'queued',
            result: null,
            error: null,
            // Запоминаем сессию для фильтрации SSE-чанков при смене сессии (B2)
            session_id: currentSessionId ?? null,
          },
        },
        // Оптимистично обновляем сессию: updated_at → вверх списка, +1 к счётчику
        sessions: currentSessionId
          ? (() => {
              const updated = state.sessions.map((s) =>
                s.id === currentSessionId
                  ? { ...s, updated_at: nowIso, message_count: s.message_count + 1 }
                  : s,
              )
              // Переставляем обновлённую сессию в начало (сортировка по свежести)
              const idx = updated.findIndex((s) => s.id === currentSessionId)
              if (idx > 0) {
                const [moved] = updated.splice(idx, 1)
                updated.unshift(moved)
              }
              return updated
            })()
          : state.sessions,
      }))

      // Добавляем pending-reply для ответа ассистента
      const pendingReplyMsg: ChatMessage = {
        id: `pending-reply-${result.task_id}`,
        role: 'assistant',
        content: '',
        task_id: result.task_id,
        created_at: new Date().toISOString(),
        streaming: true,
      }
      set((state) => ({ messages: [...state.messages, pendingReplyMsg] }))
    } catch (err) {
      // Помечаем ошибку в сообщении
      set((state) => ({
        messages: state.messages.map((m) =>
          m.id === tempUserMsg.id
            ? { ...m, content: m.content + '\n\n⚠️ Ошибка отправки' }
            : m,
        ),
        sendingMessage: false,
      }))
      console.error('Ошибка отправки:', err)
    }
  },

  addMessage: (message) => {
    set((state) => {
      // Убираем pending-reply если есть для этой задачи
      const filtered = message.task_id
        ? state.messages.filter(
            (m) => m.id !== `pending-reply-${message.task_id}`,
          )
        : state.messages
      // Не дублируем — проверяем по id И по task_id+role (оптимистичные сообщения)
      if (filtered.some((m) =>
        m.id === message.id ||
        (message.task_id && m.task_id === message.task_id && m.role === message.role)
      )) return state
      return { messages: [...filtered, message] }
    })
  },

  updateMessageContent: (id, content) => {
    set((state) => ({
      messages: state.messages.map((m) => (m.id === id ? { ...m, content } : m)),
    }))
  },

  setMessageStreaming: (id, streaming) => {
    set((state) => ({
      messages: state.messages.map((m) =>
        m.id === id ? { ...m, streaming } : m,
      ),
    }))
  },
})
