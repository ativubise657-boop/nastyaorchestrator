import { StateCreator } from 'zustand'
import { apiFetch } from './_shared'
import type { AppStore } from './index'

// ===== Типы задачи =====

export type TaskStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface TaskInfo {
  id: string
  status: TaskStatus
  result: string | null
  error: string | null
  created_at?: string
  updated_at?: string
  // Накопленный стриминговый текст
  streamBuffer?: string
  // Сессия, в которой была создана задача (для фильтрации SSE-чанков при смене сессии)
  session_id?: string | null
}

// ===== Интерфейс slice =====

export interface TaskSlice {
  // Задачи
  tasks: Record<string, TaskInfo>
  currentTaskId: string | null
  updateTask: (taskId: string, data: Partial<TaskInfo>) => void
  appendTaskStream: (taskId: string, chunk: string) => void
  cancelTask: () => Promise<void>
}

// ===== Реализация slice =====

export const createTaskSlice: StateCreator<AppStore, [], [], TaskSlice> = (set, get) => ({
  tasks: {},
  currentTaskId: null,

  cancelTask: async () => {
    let { currentTaskId } = get()
    // Fallback: если currentTaskId потерялся — ищем активную задачу
    if (!currentTaskId) {
      const activeTask = Object.entries(get().tasks).find(
        ([, t]) => t.status === 'running' || t.status === 'queued'
      )
      if (activeTask) currentTaskId = activeTask[0]
    }
    if (!currentTaskId) return

    try {
      await apiFetch('/api/chat/cancel', {
        method: 'POST',
        body: JSON.stringify({ task_id: currentTaskId }),
      })
      set((state) => ({
        currentTaskId: null,
        sendingMessage: false,
        tasks: {
          ...state.tasks,
          [currentTaskId]: { ...state.tasks[currentTaskId], status: 'cancelled', id: currentTaskId },
        },
        // Обновляем pending-reply — показываем что отменено
        messages: state.messages.map((m) =>
          m.task_id === currentTaskId && m.role === 'assistant' && m.streaming
            ? { ...m, content: m.content || '⛔ Задача отменена', streaming: false }
            : m,
        ),
      }))
    } catch (err) {
      console.error('Ошибка отмены задачи:', err)
    }
  },

  updateTask: (taskId, data) => {
    set((state) => ({
      tasks: {
        ...state.tasks,
        [taskId]: { ...state.tasks[taskId], ...data, id: taskId },
      },
    }))
  },

  appendTaskStream: (taskId, chunk) => {
    set((state) => {
      const task = state.tasks[taskId] || { id: taskId, status: 'running', result: null, error: null }
      const newBuffer = (task.streamBuffer || '') + chunk

      // Обновляем также pending-reply сообщение в чате
      const messages = state.messages.map((m) =>
        m.task_id === taskId && m.role === 'assistant'
          ? { ...m, content: newBuffer, streaming: true }
          : m,
      )

      return {
        tasks: {
          ...state.tasks,
          [taskId]: { ...task, streamBuffer: newBuffer, status: 'running' },
        },
        messages,
      }
    })
  },
})
