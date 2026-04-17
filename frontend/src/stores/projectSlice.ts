import { StateCreator } from 'zustand'
import { apiFetch } from './_shared'
import type { AppStore } from './index'

// ===== Типы проекта =====

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

// ===== Интерфейс slice =====

export interface ProjectSlice {
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
  selectProject: (id: string) => Promise<void>
}

// ===== Реализация slice =====

export const createProjectSlice: StateCreator<AppStore, [], [], ProjectSlice> = (set, get) => ({
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

  selectProject: async (id) => {
    localStorage.setItem('selectedProjectId', id)
    set({
      selectedProjectId: id,
      messages: [],
      selectedDocId: null,
      docRefsSelected: new Set<string>(),
      linkRefsSelected: new Set<string>(),
    })
    // Загружаем сессии для нового проекта
    await get().loadSessions(id)
    const { sessions } = get()
    if (sessions.length === 0) {
      // Новый проект — создаём стартовую пустую сессию
      await get().createSession(id)
    } else {
      // Загружаем историю самой свежей сессии
      await get().loadMessages(sessions[0].id)
    }
  },
})
