import { StateCreator } from 'zustand'
import { apiFetch } from './_shared'
import type { AppStore } from './index'

// ===== Типы сессии =====

// Сессия чата (ChatGPT-style) — один проект может иметь несколько сессий
export interface ChatSession {
  id: string
  project_id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
}

// ===== Интерфейс slice =====

export interface SessionSlice {
  // Сессии чата
  sessions: ChatSession[]
  currentSessionId: string | null
  sessionsLoading: boolean
  loadSessions: (projectId: string) => Promise<void>
  createSession: (projectId: string, title?: string) => Promise<ChatSession>
  switchSession: (sessionId: string) => Promise<void>
  renameSession: (sessionId: string, title: string) => Promise<void>
  deleteSession: (sessionId: string) => Promise<void>
}

// ===== Реализация slice =====

export const createSessionSlice: StateCreator<AppStore, [], [], SessionSlice> = (set, get) => ({
  sessions: [],
  currentSessionId: null,
  sessionsLoading: false,

  loadSessions: async (projectId) => {
    set({ sessionsLoading: true })
    try {
      const sessions = await apiFetch<ChatSession[]>(`/api/chat/sessions/${projectId}`)
      set({
        sessions,
        sessionsLoading: false,
        // Если сессий нет — сбрасываем текущую; иначе ставим первую (свежайшую по API)
        currentSessionId: sessions.length > 0 ? sessions[0].id : null,
      })
    } catch (err) {
      console.warn('Ошибка загрузки сессий:', err)
      set({ sessionsLoading: false })
    }
  },

  createSession: async (projectId, title?) => {
    const session = await apiFetch<ChatSession>('/api/chat/sessions', {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId, ...(title ? { title } : {}) }),
    })
    set((state) => ({
      // Новая сессия — в начало списка (самая свежая)
      sessions: [session, ...state.sessions],
      currentSessionId: session.id,
      messages: [],
      tasks: {},
    }))
    return session
  },

  switchSession: async (sessionId) => {
    set({ currentSessionId: sessionId })
    await get().loadMessages(sessionId)
  },

  renameSession: async (sessionId, title) => {
    const updated = await apiFetch<ChatSession>(`/api/chat/sessions/${sessionId}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    })
    set((state) => ({
      sessions: state.sessions.map((s) => (s.id === sessionId ? updated : s)),
    }))
  },

  deleteSession: async (sessionId) => {
    await apiFetch(`/api/chat/sessions/${sessionId}`, { method: 'DELETE' })
    const { sessions, currentSessionId, selectedProjectId } = get()
    const remaining = sessions.filter((s) => s.id !== sessionId)
    set({ sessions: remaining })

    if (currentSessionId === sessionId) {
      if (remaining.length > 0) {
        // Переключаемся на первую оставшуюся сессию
        await get().switchSession(remaining[0].id)
      } else if (selectedProjectId) {
        // Сессий больше нет — создаём новую пустую
        await get().createSession(selectedProjectId)
      }
    }
  },
})
