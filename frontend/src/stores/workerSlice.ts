import { StateCreator } from 'zustand'
import type { AppStore } from './index'

// ===== Типы worker =====

export interface WorkerStatus {
  online: boolean
  last_heartbeat: string | null
  queue_size: number
}

// ===== Интерфейс slice =====

export interface WorkerSlice {
  // Worker / runtime статус
  workerOnline: boolean
  queueSize: number
  taskPhase: string | null
  appVersion: string | null
  setWorkerStatus: (online: boolean, queueSize?: number) => void
  setTaskPhase: (phase: string | null) => void
  setAppVersion: (version: string | null) => void
}

// ===== Реализация slice =====

export const createWorkerSlice: StateCreator<AppStore, [], [], WorkerSlice> = (set) => ({
  workerOnline: false,
  queueSize: 0,
  taskPhase: null,
  appVersion: null,

  setWorkerStatus: (online, queueSize = 0) => {
    set({ workerOnline: online, queueSize })
  },

  setTaskPhase: (phase) => set({ taskPhase: phase }),
  setAppVersion: (version) => set({ appVersion: version }),
})
