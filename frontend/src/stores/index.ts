import { create } from 'zustand'
import { useShallow } from 'zustand/react/shallow'

// ===== Слайсы =====
import { createProjectSlice } from './projectSlice'
import { createSessionSlice } from './sessionSlice'
import { createChatSlice } from './chatSlice'
import { createTaskSlice } from './taskSlice'
import { createDocumentSlice } from './documentSlice'
import { createLinkSlice } from './linkSlice'
import { createWorkerSlice } from './workerSlice'
import { createSettingsSlice } from './settingsSlice'
import { createUISlice } from './uiSlice'

// ===== Реэкспорт типов из slice-файлов (обратная совместимость) =====
export type { Project, CreateProjectData, AppUpdateCommit, AppUpdateReleaseNote, AppUpdatePreview, AppUpdateStatus } from './projectSlice'
export type { ChatSession } from './sessionSlice'
export type { MessageRole, ChatModel, ChatAttachment, ChatMessage } from './chatSlice'
export type { TaskStatus, TaskInfo } from './taskSlice'
export type { Document, DocViewMode, Folder } from './documentSlice'
export type { Link } from './linkSlice'
export type { WorkerStatus } from './workerSlice'
export type { StatuslineData, ChatMode, ToastKind, ToastMessage } from './uiSlice'

// ===== Типы slice-интерфейсов =====
import type { ProjectSlice } from './projectSlice'
import type { SessionSlice } from './sessionSlice'
import type { ChatSlice } from './chatSlice'
import type { TaskSlice } from './taskSlice'
import type { DocumentSlice } from './documentSlice'
import type { LinkSlice } from './linkSlice'
import type { WorkerSlice } from './workerSlice'
import type { SettingsSlice } from './settingsSlice'
import type { UISlice } from './uiSlice'

// ===== Общий тип store — объединение всех слайсов =====
export type AppStore =
  & ProjectSlice
  & SessionSlice
  & ChatSlice
  & TaskSlice
  & DocumentSlice
  & LinkSlice
  & WorkerSlice
  & SettingsSlice
  & UISlice

// ===== Создание store из слайсов =====
export const useStore = create<AppStore>()((...args) => ({
  ...createProjectSlice(...args),
  ...createSessionSlice(...args),
  ...createChatSlice(...args),
  ...createTaskSlice(...args),
  ...createDocumentSlice(...args),
  ...createLinkSlice(...args),
  ...createWorkerSlice(...args),
  ...createSettingsSlice(...args),
  ...createUISlice(...args),
}))

// ===== Селекторы — useShallow для массивов/объектов (React 19 совместимость) =====
// Экспорты сохранены полностью — компоненты и хуки работают без изменений
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
// Сессии чата
export const useSessions = () => useStore(useShallow((s) => s.sessions))
export const useCurrentSessionId = () => useStore((s) => s.currentSessionId)
export const useSessionsLoading = () => useStore((s) => s.sessionsLoading)
export const useCurrentSession = () =>
  useStore((s) => {
    if (!s.currentSessionId) return null
    return s.sessions.find((sess) => sess.id === s.currentSessionId) ?? null
  })
// Режим задачи (B4)
export const useSelectedMode = () => useStore((s) => s.selectedMode)
