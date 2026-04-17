import { StateCreator } from 'zustand'
import { apiFetch } from './_shared'
import type { AppStore } from './index'

// ===== Типы ссылок =====

export interface Link {
  id: string
  project_id: string
  title: string
  url: string
  description: string
  folder_id: string | null
  created_at: string
}

// ===== Интерфейс slice =====

export interface LinkSlice {
  // Ссылки проекта (URL с описанием)
  links: Link[]
  linksLoading: boolean
  loadLinks: (projectId: string) => Promise<void>
  addLink: (projectId: string, title: string, url: string, description: string) => Promise<void>
  updateLink: (projectId: string, linkId: string, data: { title?: string; url?: string; description?: string }) => Promise<void>
  deleteLink: (projectId: string, linkId: string) => Promise<void>
}

// ===== Реализация slice =====

export const createLinkSlice: StateCreator<AppStore, [], [], LinkSlice> = (set, _get) => ({
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
})
