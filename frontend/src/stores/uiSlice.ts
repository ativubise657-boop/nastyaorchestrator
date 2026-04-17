import { StateCreator } from 'zustand'
import type { AppStore } from './index'

// ===== Типы UI =====

// Режим обработки задачи — передаётся в POST /api/chat/send
export type ChatMode = 'auto' | 'ag+' | 'rev' | 'solo'

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

// ===== Интерфейс slice =====

export interface UISlice {
  // Панели и шрифты
  sidebarOpen: boolean
  docPanelOpen: boolean
  chatFontSize: number
  toggleSidebar: () => void
  setSidebarOpen: (open: boolean) => void
  toggleDocPanel: () => void
  setDocPanelOpen: (open: boolean) => void
  setChatFontSize: (size: number) => void

  // Ссылки на документы в чат (мультиселект)
  docRefsSelected: Set<string>
  toggleDocRef: (docId: string) => void
  clearDocRefs: () => void
  getDocRefsText: () => string

  // Ссылки (URL) отмеченные для анализа — мультиселект
  linkRefsSelected: Set<string>
  toggleLinkRef: (linkId: string) => void
  clearLinkRefs: () => void

  // Режим обработки задачи (B4: передаётся в POST /api/chat/send)
  selectedMode: ChatMode
  setSelectedMode: (mode: ChatMode) => void

  // Runtime statusline metrics
  statusline: StatuslineData | null
  setStatusline: (data: StatuslineData | null) => void
}

// ===== Реализация slice =====

export const createUISlice: StateCreator<AppStore, [], [], UISlice> = (set, get) => ({
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

  // Режим обработки задачи — по умолчанию 'auto' (совместимо с backend default)
  selectedMode: 'auto',
  setSelectedMode: (mode) => set({ selectedMode: mode }),

  // Statusline
  statusline: null,
  setStatusline: (data) => set({ statusline: data }),
})
