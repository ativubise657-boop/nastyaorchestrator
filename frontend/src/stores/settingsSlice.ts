import { StateCreator } from 'zustand'
import { apiFetch } from './_shared'
import { normalizeChatModel } from './chatSlice'
import type { AppStore } from './index'
import type { ChatModel } from './chatSlice'

// ===== Интерфейс slice =====

export interface SettingsSlice {
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

  // Выбранная модель чата
  selectedModel: ChatModel
  setSelectedModel: (model: ChatModel) => void
}

// ===== Реализация slice =====

export const createSettingsSlice: StateCreator<AppStore, [], [], SettingsSlice> = (set, get) => ({
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

  // Модель — инициализируется из localStorage с нормализацией legacy значений
  selectedModel: normalizeChatModel(localStorage.getItem('selectedModel')),
  setSelectedModel: (model) => {
    localStorage.setItem('selectedModel', model)
    set({ selectedModel: normalizeChatModel(model) })
  },
})
