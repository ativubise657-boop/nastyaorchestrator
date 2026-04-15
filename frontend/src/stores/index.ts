import { create } from 'zustand'
import { useShallow } from 'zustand/react/shallow'

// ===== Типы данных =====

export interface Project {
  id: string
  name: string
  description: string
  path: string | null
  git_url: string
  created_at: string
}

export interface CreateProjectData {
  name: string
  description: string
  path?: string
}

export type MessageRole = 'user' | 'assistant' | 'system'
export type TaskStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
export type ChatModel = 'gpt-5.4' | 'gpt-5.3-codex'

// Дефолт модели — gpt-5.4 (codex CLI через opera-proxy → OpenAI).
// Был glm-5-turbo (aitunnel) — переключили потому что Дима хочет codex.
const DEFAULT_CHAT_MODEL: ChatModel = 'gpt-5.4'

// Любое legacy значение (glm/gemini/nano/…) нормализуется в одну из двух моделей.
// Reasoning — для задач где нужна глубина, GPT 5 — для всего остального.
const LEGACY_MODEL_ALIASES: Record<string, ChatModel> = {
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

function normalizeChatModel(model?: string | null): ChatModel {
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

export interface TaskInfo {
  id: string
  status: TaskStatus
  result: string | null
  error: string | null
  created_at?: string
  updated_at?: string
  // Накопленный стриминговый текст
  streamBuffer?: string
}

export interface Document {
  id: string
  project_id: string
  filename: string
  size: number
  created_at: string
  content_type?: string
  folder_id: string | null
  content?: string
}

export type DocViewMode = 'project' | 'all'

export interface Folder {
  id: string
  project_id: string
  name: string
  parent_id: string | null
  created_at: string
}

export interface WorkerStatus {
  online: boolean
  last_heartbeat: string | null
  queue_size: number
}

export interface Link {
  id: string
  project_id: string
  title: string
  url: string
  description: string
  folder_id: string | null
  created_at: string
}

// ===== API helper =====

const API_BASE = ''

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  // Для FormData не ставим Content-Type — браузер сам добавит boundary
  const isFormData = options?.body instanceof FormData
  const headers = isFormData
    ? { ...options?.headers }
    : { 'Content-Type': 'application/json', ...options?.headers }

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`API error ${res.status}: ${text || res.statusText}`)
  }
  // 204 No Content — нет тела ответа (DELETE и т.п.)
  if (res.status === 204) return undefined as T
  return res.json()
}

// ===== Store interface =====

interface AppStore {
  // Проекты
  projects: Project[]
  selectedProjectId: string | null
  projectsLoading: boolean
  projectsError: string | null
  loadProjects: () => Promise<void>
  createProject: (data: CreateProjectData) => Promise<void>
  updateProject: (id: string, data: Partial<CreateProjectData>) => Promise<void>
  getAppUpdatePreview: (id: string) => Promise<AppUpdatePreview>
  startAppUpdate: (id: string) => Promise<AppUpdateStatus>
  getAppUpdateStatus: (id: string) => Promise<AppUpdateStatus>
  deleteProject: (id: string) => Promise<void>
  selectProject: (id: string) => void

  // Чат
  messages: ChatMessage[]
  messagesLoading: boolean
  sendingMessage: boolean
  loadHistory: (projectId: string) => Promise<void>
  sendMessage: (message: string, modelOverride?: string, attachments?: ChatAttachment[]) => Promise<void>
  clearMessages: () => void
  addMessage: (message: ChatMessage) => void
  updateMessageContent: (id: string, content: string) => void
  setMessageStreaming: (id: string, streaming: boolean) => void

  // Задачи
  tasks: Record<string, TaskInfo>
  currentTaskId: string | null
  updateTask: (taskId: string, data: Partial<TaskInfo>) => void
  appendTaskStream: (taskId: string, chunk: string) => void
  cancelTask: () => Promise<void>

  // Документы
  documents: Document[]
  documentsLoading: boolean
  selectedDocId: string | null
  docViewMode: DocViewMode
  loadDocuments: (projectId: string) => Promise<void>
  loadAllDocuments: () => Promise<void>
  setDocViewMode: (mode: DocViewMode) => void
  uploadDocument: (projectId: string, file: File, folderId?: string | null) => Promise<void>
  deleteDocument: (projectId: string, docId: string) => Promise<void>
  renameDocument: (projectId: string, docId: string, newName: string) => Promise<void>
  moveDocument: (projectId: string, docId: string, folderId: string | null) => Promise<void>
  selectDocument: (id: string | null) => void
  loadDocumentContent: (projectId: string, docId: string) => Promise<string>

  // Папки
  folders: Folder[]
  loadFolders: (projectId: string) => Promise<void>
  createFolder: (projectId: string, name: string, parentId?: string | null) => Promise<Folder>
  renameFolder: (projectId: string, folderId: string, name: string) => Promise<void>
  deleteFolder: (projectId: string, folderId: string) => Promise<void>

  // Worker
  workerOnline: boolean
  queueSize: number
  taskPhase: string | null
  appVersion: string | null
  setWorkerStatus: (online: boolean, queueSize?: number) => void
  setTaskPhase: (phase: string | null) => void
  setAppVersion: (version: string | null) => void

  // Remote config (GitHub remote-config.json)
  remoteConfig: Record<string, any>
  remoteConfigNotification: {
    visible: boolean
    message: string
    version: number | string | null
  }
  loadRemoteConfig: () => Promise<void>
  applyRemoteConfig: (cfg: Record<string, any>) => void
  dismissRemoteConfigNotification: () => void

  // Модель
  selectedModel: ChatModel
  setSelectedModel: (model: ChatModel) => void

  // UI
  sidebarOpen: boolean
  docPanelOpen: boolean
  chatFontSize: number
  toggleSidebar: () => void
  setSidebarOpen: (open: boolean) => void
  toggleDocPanel: () => void
  setDocPanelOpen: (open: boolean) => void
  setChatFontSize: (size: number) => void

  // Ссылки проекта (URL с описанием)
  links: Link[]
  linksLoading: boolean
  loadLinks: (projectId: string) => Promise<void>
  addLink: (projectId: string, title: string, url: string, description: string) => Promise<void>
  updateLink: (projectId: string, linkId: string, data: { title?: string; url?: string; description?: string }) => Promise<void>
  deleteLink: (projectId: string, linkId: string) => Promise<void>

  // Ссылки на документы в чат (мультиселект)
  docRefsSelected: Set<string>
  toggleDocRef: (docId: string) => void
  clearDocRefs: () => void
  getDocRefsText: () => string

  // Ссылки (URL) отмеченные для анализа — мультиселект
  linkRefsSelected: Set<string>
  toggleLinkRef: (linkId: string) => void
  clearLinkRefs: () => void

  // Runtime statusline metrics
  statusline: StatuslineData | null
  setStatusline: (data: StatuslineData | null) => void
}

export interface StatuslineData {
  rl_5h_pct: number | null
  rl_5h_reset: number | null
  rl_7d_pct: number | null
  ram_used_gb: number
  ram_total_gb: number
  ram_pct: number
  session_cost_usd: number
  context_used_pct: number
  model: string | null
  ts: number
}

export interface AppUpdateCommit {
  sha: string
  summary: string
}

export interface AppUpdateReleaseNote {
  title: string
  version: string | null
  items: string[]
}

export interface AppUpdatePreview {
  current_version: string | null
  target_version: string | null
  current_sha: string
  target_sha: string
  current_label: string
  target_label: string
  branch: string
  origin_url: string
  needs_update: boolean
  local_changes: boolean
  check_error: string | null
  blocked_reason: string | null
  release_notes: AppUpdateReleaseNote[]
  commit_count: number
  commits: AppUpdateCommit[]
  project_path: string
  active_status?: AppUpdateStatus | null
}

export interface AppUpdateStatus {
  operation_id: string | null
  status: 'idle' | 'queued' | 'running' | 'completed' | 'failed'
  phase: string
  progress: number
  message: string
  error: string | null
  updated: boolean
  restarting: boolean
  changed_files: string[]
  logs: string[]
  started_at: string | null
  updated_at: string
  current_version: string | null
  target_version: string | null
  current_sha: string
  target_sha: string
  current_label: string
  target_label: string
  branch: string
  origin_url: string
  needs_update: boolean
  local_changes: boolean
  check_error: string | null
  blocked_reason: string | null
  release_notes: AppUpdateReleaseNote[]
  commit_count: number
  commits: AppUpdateCommit[]
  project_path: string
}

// ===== Zustand Store =====

export const useStore = create<AppStore>((set, get) => ({
  // --- Проекты ---
  projects: [],
  selectedProjectId: localStorage.getItem('selectedProjectId') || null,
  projectsLoading: false,
  projectsError: null,

  loadProjects: async () => {
    set({ projectsLoading: true, projectsError: null })
    try {
      const projects = await apiFetch<Project[]>('/api/projects')
      set({ projects, projectsLoading: false })
    } catch (err) {
      set({
        projectsError: err instanceof Error ? err.message : 'Ошибка загрузки',
        projectsLoading: false,
      })
    }
  },

  createProject: async (data) => {
    const project = await apiFetch<Project>('/api/projects', {
      method: 'POST',
      body: JSON.stringify(data),
    })
    set((state) => ({ projects: [...state.projects, project] }))
  },

  updateProject: async (id, data) => {
    const updated = await apiFetch<Project>(`/api/projects/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    })
    set((state) => ({
      projects: state.projects.map((p) => (p.id === id ? updated : p)),
    }))
  },

  getAppUpdatePreview: async (id) => {
    return apiFetch<AppUpdatePreview>(`/api/projects/${id}/update-app`)
  },

  startAppUpdate: async (id) => {
    return apiFetch<AppUpdateStatus>(`/api/projects/${id}/update-app`, {
      method: 'POST',
    })
  },

  getAppUpdateStatus: async (id) => {
    return apiFetch<AppUpdateStatus>(`/api/projects/${id}/update-app/status`)
  },

  deleteProject: async (id) => {
    await apiFetch(`/api/projects/${id}`, { method: 'DELETE' })
    const wasSelected = get().selectedProjectId === id
    if (wasSelected) localStorage.removeItem('selectedProjectId')
    set((state) => ({
      projects: state.projects.filter((p) => p.id !== id),
      selectedProjectId: wasSelected ? null : state.selectedProjectId,
      messages: wasSelected ? [] : state.messages,
    }))
  },

  selectProject: (id) => {
    localStorage.setItem('selectedProjectId', id)
    set({
      selectedProjectId: id,
      messages: [],
      selectedDocId: null,
      docRefsSelected: new Set<string>(),
      linkRefsSelected: new Set<string>(),
    })
  },

  // --- Чат ---
  messages: [],
  messagesLoading: false,
  sendingMessage: false,

  clearMessages: () => {
    set({ messages: [], tasks: {} })
  },

  loadHistory: async (projectId) => {
    set({ messagesLoading: true })
    try {
      const messages = await apiFetch<ChatMessage[]>(
        `/api/chat/history/${projectId}?limit=100`,
      )
      set({ messages, messagesLoading: false })
    } catch {
      set({ messagesLoading: false })
    }
  },

  sendMessage: async (message, modelOverride?, attachments?) => {
    const { selectedProjectId, selectedModel, documents, selectedDocId, docRefsSelected, links, linkRefsSelected } = get()
    const model = normalizeChatModel(modelOverride || selectedModel)
    if (!selectedProjectId) return

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
          body: JSON.stringify({ project_id: selectedProjectId, message: apiMessage, model, attachments: atts }),
        },
      )

      // Заменяем временное сообщение реальным + сбрасываем чекбоксы документов/ссылок
      // (selectedDocId оставляем — это "активный для превью", он не должен слетать)
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
          },
        },
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

  // --- Задачи ---
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

  // --- Документы ---
  documents: [],
  documentsLoading: false,
  selectedDocId: null,
  docViewMode: 'project' as DocViewMode,

  loadDocuments: async (projectId) => {
    set({ documentsLoading: true })
    try {
      const documents = await apiFetch<Document[]>(`/api/documents/${projectId}`)
      set({ documents, documentsLoading: false })
    } catch {
      set({ documentsLoading: false })
    }
  },

  loadAllDocuments: async () => {
    set({ documentsLoading: true })
    try {
      const data = await apiFetch<{ documents: Document[]; folders: Folder[] }>('/api/documents/all')
      set({ documents: data.documents, folders: data.folders, documentsLoading: false })
    } catch {
      set({ documentsLoading: false })
    }
  },

  setDocViewMode: (mode) => {
    set({ docViewMode: mode })
    if (mode === 'all') {
      get().loadAllDocuments()
    } else {
      const projectId = get().selectedProjectId
      if (projectId) {
        get().loadDocuments(projectId)
        get().loadFolders(projectId)
      }
    }
  },

  uploadDocument: async (projectId, file, folderId) => {
    const formData = new FormData()
    formData.append('file', file)
    const url = folderId
      ? `/api/documents/${projectId}/upload?folder_id=${folderId}`
      : `/api/documents/${projectId}/upload`
    const doc = await apiFetch<Document>(url, {
      method: 'POST',
      body: formData,
      headers: {},
    })
    set((state) => ({ documents: [...state.documents, doc] }))
  },

  deleteDocument: async (projectId, docId) => {
    await apiFetch(`/api/documents/${projectId}/${docId}`, { method: 'DELETE' })
    set((state) => ({
      documents: state.documents.filter((d) => d.id !== docId),
      selectedDocId: state.selectedDocId === docId ? null : state.selectedDocId,
      docRefsSelected: new Set([...state.docRefsSelected].filter((id) => id !== docId)),
    }))
  },

  renameDocument: async (projectId, docId, newName) => {
    await apiFetch(`/api/documents/${projectId}/${docId}/rename`, {
      method: 'PATCH',
      body: JSON.stringify({ filename: newName }),
    })
    set((state) => ({
      documents: state.documents.map((d) =>
        d.id === docId ? { ...d, filename: newName } : d
      ),
    }))
  },

  moveDocument: async (projectId, docId, folderId) => {
    await apiFetch(`/api/documents/${projectId}/${docId}/move`, {
      method: 'PATCH',
      body: JSON.stringify({ folder_id: folderId }),
    })
    set((state) => ({
      documents: state.documents.map((d) =>
        d.id === docId ? { ...d, folder_id: folderId } : d
      ),
    }))
  },

  selectDocument: (id) => set({ selectedDocId: id }),

  loadDocumentContent: async (projectId, docId) => {
    const res = await fetch(`/api/documents/${projectId}/${docId}/content`)
    if (!res.ok) throw new Error(`Ошибка загрузки документа: ${res.status}`)
    return res.text()
  },

  // --- Папки ---
  folders: [],

  loadFolders: async (projectId) => {
    try {
      const folders = await apiFetch<Folder[]>(`/api/documents/${projectId}/folders`)
      set({ folders })
    } catch {
      set({ folders: [] })
    }
  },

  createFolder: async (projectId, name, parentId) => {
    const folder = await apiFetch<Folder>(`/api/documents/${projectId}/folders`, {
      method: 'POST',
      body: JSON.stringify({ name, parent_id: parentId ?? null }),
    })
    set((state) => ({ folders: [...state.folders, folder] }))
    return folder
  },

  renameFolder: async (projectId, folderId, name) => {
    await apiFetch(`/api/documents/${projectId}/folders/${folderId}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    })
    set((state) => ({
      folders: state.folders.map((f) =>
        f.id === folderId ? { ...f, name } : f
      ),
    }))
  },

  deleteFolder: async (projectId, folderId) => {
    await apiFetch(`/api/documents/${projectId}/folders/${folderId}`, {
      method: 'DELETE',
    })
    set((state) => ({
      folders: state.folders.filter((f) => f.id !== folderId),
      // Документы из удалённой папки переедут в корень (backend сделает)
      documents: state.documents.map((d) =>
        d.folder_id === folderId ? { ...d, folder_id: null } : d
      ),
    }))
  },

  // --- Worker ---
  workerOnline: false,
  queueSize: 0,
  taskPhase: null,
  appVersion: null,

  setWorkerStatus: (online, queueSize = 0) => {
    set({ workerOnline: online, queueSize })
  },

  setTaskPhase: (phase) => set({ taskPhase: phase }),
  setAppVersion: (version) => set({ appVersion: version }),

  // --- Remote config ---
  remoteConfig: {},
  remoteConfigNotification: { visible: false, message: '', version: null },

  loadRemoteConfig: async () => {
    try {
      const cfg = await apiFetch<Record<string, any>>('/api/system/remote-config')
      set({ remoteConfig: cfg || {} })
    } catch {
      // игнорируем — endpoint может не существовать в старых билдах
    }
  },

  applyRemoteConfig: (cfg) => {
    const old = get().remoteConfig
    const oldVersion = old?.version ?? null
    const newVersion = cfg?.version ?? null
    set({ remoteConfig: cfg || {} })
    // Показать всплывашку если версия изменилась
    if (newVersion !== null && newVersion !== oldVersion) {
      set({
        remoteConfigNotification: {
          visible: true,
          message: cfg?.notification_message
            || `Обновление оркестратора применено (v${newVersion})`,
          version: newVersion,
        },
      })
    }
  },

  dismissRemoteConfigNotification: () =>
    set({
      remoteConfigNotification: { visible: false, message: '', version: null },
    }),

  // --- Модель ---
  selectedModel: normalizeChatModel(localStorage.getItem('selectedModel')),
  setSelectedModel: (model) => {
    localStorage.setItem('selectedModel', model)
    set({ selectedModel: normalizeChatModel(model) })
  },

  // --- UI ---
  sidebarOpen: false,
  docPanelOpen: false,
  chatFontSize: parseInt(localStorage.getItem('chatFontSize') || '28', 10),

  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  toggleDocPanel: () => set((state) => ({ docPanelOpen: !state.docPanelOpen })),
  setDocPanelOpen: (open) => set({ docPanelOpen: open }),
  setChatFontSize: (size: number) => {
    const clamped = Math.max(14, Math.min(42, size))
    localStorage.setItem('chatFontSize', String(clamped))
    set({ chatFontSize: clamped })
  },

  // --- Ссылки проекта ---
  links: [],
  linksLoading: false,

  loadLinks: async (projectId) => {
    set({ linksLoading: true })
    try {
      const links = await apiFetch<Link[]>(`/api/links/${projectId}`)
      set({ links, linksLoading: false })
    } catch {
      set({ links: [], linksLoading: false })
    }
  },

  addLink: async (projectId, title, url, description) => {
    const link = await apiFetch<Link>(`/api/links/${projectId}`, {
      method: 'POST',
      body: JSON.stringify({ title, url, description }),
    })
    set((state) => ({ links: [link, ...state.links] }))
  },

  updateLink: async (projectId, linkId, data) => {
    const updated = await apiFetch<Link>(`/api/links/${projectId}/${linkId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    set((state) => ({
      links: state.links.map((l) => l.id === linkId ? updated : l),
    }))
  },

  deleteLink: async (projectId, linkId) => {
    await apiFetch(`/api/links/${projectId}/${linkId}`, { method: 'DELETE' })
    set((state) => {
      const nextLinkRefs = new Set(state.linkRefsSelected)
      nextLinkRefs.delete(linkId)
      return {
        links: state.links.filter((l) => l.id !== linkId),
        linkRefsSelected: nextLinkRefs,
      }
    })
  },

  // --- Ссылки на документы (мультиселект для вставки в чат) ---
  docRefsSelected: new Set<string>(),
  toggleDocRef: (docId) => set((state) => {
    const next = new Set(state.docRefsSelected)
    if (next.has(docId)) next.delete(docId)
    else next.add(docId)
    return { docRefsSelected: next }
  }),
  clearDocRefs: () => set({ docRefsSelected: new Set() }),
  getDocRefsText: () => {
    const { docRefsSelected, documents } = get()
    if (docRefsSelected.size === 0) return ''
    const refs = documents
      .filter((d) => docRefsSelected.has(d.id))
      .map((d, i) => `#${i + 1} ${d.filename}`)
    return refs.join(', ')
  },

  // --- Ссылки (URL) для анализа ---
  linkRefsSelected: new Set<string>(),
  toggleLinkRef: (linkId) => set((state) => {
    const next = new Set(state.linkRefsSelected)
    if (next.has(linkId)) next.delete(linkId)
    else next.add(linkId)
    return { linkRefsSelected: next }
  }),
  clearLinkRefs: () => set({ linkRefsSelected: new Set() }),

  // Statusline
  statusline: null,
  setStatusline: (data) => set({ statusline: data }),
}))

// Индивидуальные селекторы — useShallow для массивов/объектов (React 19 совместимость)
export const useProjects = () => useStore(useShallow((s) => s.projects))
export const useSelectedProjectId = () => useStore((s) => s.selectedProjectId)
export const useSelectedProject = () =>
  useStore((s) => {
    if (!s.selectedProjectId) return null
    return s.projects.find((p) => p.id === s.selectedProjectId) ?? null
  })
export const useMessages = () => useStore(useShallow((s) => s.messages))
export const useTasks = () => useStore(useShallow((s) => s.tasks))
export const useWorkerOnline = () => useStore((s) => s.workerOnline)
export const useQueueSize = () => useStore((s) => s.queueSize)
export const useAppVersion = () => useStore((s) => s.appVersion)
export const useSidebarOpen = () => useStore((s) => s.sidebarOpen)
export const useDocuments = () => useStore(useShallow((s) => s.documents))
export const useSelectedModel = () => useStore((s) => s.selectedModel)
export const useSelectedDocId = () => useStore((s) => s.selectedDocId)
export const useChatFontSize = () => useStore((s) => s.chatFontSize)
export const useDocPanelOpen = () => useStore((s) => s.docPanelOpen)
export const useFolders = () => useStore(useShallow((s) => s.folders))
export const useDocViewMode = () => useStore((s) => s.docViewMode)
export const useLinks = () => useStore(useShallow((s) => s.links))
export const useLinkRefsSelected = () => useStore((s) => s.linkRefsSelected)
