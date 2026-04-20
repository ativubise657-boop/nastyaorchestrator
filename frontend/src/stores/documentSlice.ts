import { StateCreator } from 'zustand'
import { apiFetch } from './_shared'
import type { AppStore } from './index'

// ===== Типы документов и папок =====

export interface Document {
  id: string
  project_id: string
  filename: string
  size: number
  created_at: string
  content_type?: string
  folder_id: string | null
  content?: string
  parse_status?: 'parsed' | 'failed' | 'skipped' | 'pending'
  parse_error?: string
  parse_method?: 'cache' | 'markitdown' | 'pdfminer' | 'aitunnel_gemini' | ''
}

export type DocViewMode = 'project' | 'all'

export interface Folder {
  id: string
  project_id: string
  name: string
  parent_id: string | null
  created_at: string
}

// ===== Интерфейс slice =====

export interface DocumentSlice {
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
  updateDocumentParseStatus: (docId: string, parseStatus: Document['parse_status'], parseError?: string, parseMethod?: Document['parse_method']) => void

  // Папки
  folders: Folder[]
  loadFolders: (projectId: string) => Promise<void>
  createFolder: (projectId: string, name: string, parentId?: string | null) => Promise<Folder>
  renameFolder: (projectId: string, folderId: string, name: string) => Promise<void>
  deleteFolder: (projectId: string, folderId: string) => Promise<void>
}

// ===== Реализация slice =====

export const createDocumentSlice: StateCreator<AppStore, [], [], DocumentSlice> = (set, get) => ({
  documents: [],
  documentsLoading: false,
  selectedDocId: null,
  docViewMode: 'project' as DocViewMode,

  loadDocuments: async (projectId) => {
    set({ documentsLoading: true })
    try {
      const documents = await apiFetch<Document[]>(`/api/documents/${projectId}`)
      set({ documents, documentsLoading: false })
    } catch (err) {
      set({ documentsLoading: false })
      console.error('loadDocuments failed:', err)
      const isNetwork = err instanceof TypeError && err.message.includes('fetch')
      const text = isNetwork
        ? 'Backend не отвечает. Проверь что приложение запущено.'
        : `Не удалось загрузить документы: ${err instanceof Error ? err.message : String(err)}`
      get().showToast({ kind: 'error', text })
    }
  },

  loadAllDocuments: async () => {
    set({ documentsLoading: true })
    try {
      const data = await apiFetch<{ documents: Document[]; folders: Folder[] }>('/api/documents/all')
      set({ documents: data.documents, folders: data.folders, documentsLoading: false })
    } catch (err) {
      set({ documentsLoading: false })
      console.error('loadAllDocuments failed:', err)
      const isNetwork = err instanceof TypeError && err.message.includes('fetch')
      const text = isNetwork
        ? 'Backend не отвечает. Проверь что приложение запущено.'
        : `Не удалось загрузить список документов: ${err instanceof Error ? err.message : String(err)}`
      get().showToast({ kind: 'error', text })
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

  // Fix 4.1A: обновление parse_status через SSE event document_parsed
  updateDocumentParseStatus: (docId, parseStatus, parseError, parseMethod) =>
    set((state) => ({
      documents: state.documents.map((d) =>
        d.id === docId
          ? {
              ...d,
              parse_status: parseStatus,
              parse_error: parseError ?? '',
              parse_method: parseMethod ?? d.parse_method ?? '',
            }
          : d
      ),
    })),

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
})
