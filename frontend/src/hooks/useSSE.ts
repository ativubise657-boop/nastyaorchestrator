import { useEffect, useRef, useCallback } from 'react'
import { useStore } from '../stores'
import { playNotificationSound } from './useNotificationSound'

// Интервалы для exponential backoff (мс)
const BACKOFF_INTERVALS = [1000, 2000, 4000, 8000, 15000, 30000]

interface SSETaskUpdate {
  task_id: string
  status: string
  result?: string
  error?: string
}

interface SSEWorkerStatus {
  online: boolean
  queue_size: number
}

interface SSENewMessage {
  id: string
  role: string
  content: string
  task_id: string | null
  created_at: string
}

interface SSEResultChunk {
  task_id: string
  chunk: string
}

export function useSSE() {
  const esRef = useRef<EventSource | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryCountRef = useRef(0)
  const mountedRef = useRef(true)

  const updateTask = useStore((s) => s.updateTask)
  const appendTaskStream = useStore((s) => s.appendTaskStream)
  const setWorkerStatus = useStore((s) => s.setWorkerStatus)
  const addMessage = useStore((s) => s.addMessage)
  const setMessageStreaming = useStore((s) => s.setMessageStreaming)
  const setTaskPhase = useStore((s) => s.setTaskPhase)
  const loadMessages = useStore((s) => s.loadMessages)
  const loadDocuments = useStore((s) => s.loadDocuments)
  const selectedProjectId = useStore((s) => s.selectedProjectId)

  const connect = useCallback(() => {
    if (!mountedRef.current) return
    if (esRef.current) {
      esRef.current.close()
      esRef.current = null
    }

    const es = new EventSource('/api/events/stream')
    esRef.current = es

    es.onopen = () => {
      // Соединение установлено — сбрасываем счётчик попыток
      const wasReconnect = retryCountRef.current > 0
      retryCountRef.current = 0

      // После переподключения — подтягиваем пропущенные сообщения текущей сессии
      if (wasReconnect) {
        const sid = useStore.getState().currentSessionId
        if (sid) {
          loadMessages(sid)
        }
      }
    }

    // Обновление статуса задачи
    es.addEventListener('task_update', (e: MessageEvent) => {
      try {
        const data: SSETaskUpdate = JSON.parse(e.data)
        const status = data.status as 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
        updateTask(data.task_id, { status, result: data.result ?? null, error: data.error ?? null })

        // Если задача завершена — убираем стриминг и сбрасываем фазу
        if (status === 'completed' || status === 'failed' || status === 'cancelled') {
          // Звуковое оповещение
          if (status === 'completed') playNotificationSound()
          useStore.setState({ currentTaskId: null })
          setTaskPhase(null)

          // B2: если задача принадлежит другой сессии — не трогаем messages текущей.
          // Пользователь вернётся в ту сессию — loadMessages(A) покажет полный ответ из БД.
          const task = useStore.getState().tasks[data.task_id]
          const currentSid = useStore.getState().currentSessionId
          const isCurrentSession = !task?.session_id || task.session_id === currentSid

          if (isCurrentSession) {
            const messages = useStore.getState().messages
            const pendingMsg = messages.find(
              (m) => m.task_id === data.task_id && m.role === 'assistant',
            )
            if (pendingMsg) {
              const finalContent =
                task?.streamBuffer ||
                data.result ||
                (status === 'failed' ? `❌ Ошибка: ${data.error}` : '')
              useStore.getState().updateMessageContent(pendingMsg.id, finalContent)
              setMessageStreaming(pendingMsg.id, false)
            }

            // Перезагружаем историю текущей сессии для получения реального сообщения с сервера
            if (currentSid) {
              setTimeout(() => loadMessages(currentSid), 500)
            }
          }
        }
      } catch (err) {
        console.warn('SSE task_update parse error:', err)
      }
    })

    // Статус воркера
    es.addEventListener('worker_status', (e: MessageEvent) => {
      try {
        const data: SSEWorkerStatus = JSON.parse(e.data)
        setWorkerStatus(data.online, data.queue_size)
      } catch (err) {
        console.warn('SSE worker_status parse error:', err)
      }
    })

    // Новое сообщение (от бэкенда напрямую)
    es.addEventListener('new_message', (e: MessageEvent) => {
      try {
        const data: SSENewMessage = JSON.parse(e.data)
        addMessage({
          id: data.id,
          role: data.role as 'user' | 'assistant' | 'system',
          content: data.content,
          task_id: data.task_id,
          created_at: data.created_at,
        })
      } catch (err) {
        console.warn('SSE new_message parse error:', err)
      }
    })

    // Стриминг результата по кускам
    es.addEventListener('task_chunk', (e: MessageEvent) => {
      try {
        const data: SSEResultChunk = JSON.parse(e.data)

        // B2: фильтруем чанки чужих сессий — task state обновляем (для StatusBar),
        // но messages не трогаем (они принадлежат другой сессии)
        const task = useStore.getState().tasks[data.task_id]
        const currentSid = useStore.getState().currentSessionId
        if (task?.session_id && task.session_id !== currentSid) {
          // Только обновляем streamBuffer задачи — без записи в messages
          updateTask(data.task_id, {
            streamBuffer: (task.streamBuffer || '') + data.chunk,
            status: 'running',
          })
          return
        }

        appendTaskStream(data.task_id, data.chunk)
      } catch (err) {
        console.warn('SSE task_chunk parse error:', err)
      }
    })

    // Фаза выполнения (например: "Роюсь в GitHub...")
    es.addEventListener('task_phase', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as { task_id: string; phase: string }
        setTaskPhase(data.phase || null)
      } catch (err) {
        console.warn('SSE task_phase parse error:', err)
      }
    })

    // Документ создан (асcистент создал файл в ответе)
    es.addEventListener('document_created', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as { project_id: string; filename: string }
        // Обновляем список документов если это текущий проект
        const pid = useStore.getState().selectedProjectId
        if (pid && (data.project_id === pid || data.project_id === '__common__')) {
          loadDocuments(pid)
        }
      } catch (err) {
        console.warn('SSE document_created parse error:', err)
      }
    })

    // Fix 4.1A: парсинг upload'а завершился — обновляем parse_status в списке документов
    es.addEventListener('document_parsed', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as {
          id: string
          project_id: string
          parse_status: 'parsed' | 'failed' | 'skipped' | 'pending'
          parse_error?: string
          parse_method?: 'cache' | 'markitdown' | 'pdfminer' | 'aitunnel_gemini' | ''
        }
        useStore.getState().updateDocumentParseStatus(
          data.id, data.parse_status, data.parse_error, data.parse_method,
        )
      } catch (err) {
        console.warn('SSE document_parsed parse error:', err)
      }
    })

    // Remote config обновлён на сервере — применяем + показываем всплывашку
    es.addEventListener('remote_config_updated', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        if (data?.config) {
          useStore.getState().applyRemoteConfig(data.config)
        }
      } catch (err) {
        console.warn('SSE remote_config_updated parse error:', err)
      }
    })

    // Ping — просто игнорируем, он нужен чтобы соединение не закрылось
    es.addEventListener('ping', () => {})

    es.onerror = () => {
      es.close()
      esRef.current = null
      setWorkerStatus(false, 0)

      if (!mountedRef.current) return

      // Exponential backoff переподключение
      const delay = BACKOFF_INTERVALS[Math.min(retryCountRef.current, BACKOFF_INTERVALS.length - 1)] ?? 30000
      retryCountRef.current++

      reconnectTimerRef.current = setTimeout(() => {
        if (mountedRef.current) connect()
      }, delay)
    }
  }, [updateTask, appendTaskStream, setWorkerStatus, addMessage, setMessageStreaming, setTaskPhase, loadMessages, loadDocuments])

  useEffect(() => {
    mountedRef.current = true
    connect()

    return () => {
      mountedRef.current = false
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
      if (esRef.current) {
        esRef.current.close()
        esRef.current = null
      }
    }
  }, [connect])

  // При смене проекта — перезагружаем документы.
  // Историю сообщений грузить не нужно: selectProject в store уже вызывает loadMessages
  // для активной сессии, дублирование здесь приводило к loadMessages(projectId) вместо sessionId
  useEffect(() => {
    if (selectedProjectId) {
      useStore.getState().loadDocuments(selectedProjectId)
    }
  }, [selectedProjectId])
}
