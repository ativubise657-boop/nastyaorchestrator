/**
 * useTauriUpdater — хук для автообновления через Tauri Updater.
 *
 * Архитектура:
 *   1. При монтировании приложение зовёт check() из @tauri-apps/plugin-updater
 *   2. Плагин (Rust side) стучится на endpoints из tauri.conf.json.updater.endpoints
 *      (у нас: https://github.com/.../releases/latest/download/latest.json)
 *   3. Получает latest.json → сверяет версию → проверяет подпись через pubkey
 *   4. Если update.available === true → возвращает update-object с version/notes
 *   5. При вызове install():
 *      - update.downloadAndInstall(progress) — качает .nsis.zip, применяет
 *      - relaunch() — перезапускает приложение в новой версии
 *
 * В vite dev / обычном браузере (window.__TAURI_INTERNALS__ отсутствует)
 * check() бросит exception — ловим и молча пропускаем, фича работает
 * только в bundled Tauri-окне.
 *
 * Периодический re-check раз в час — на случай если приложение держат
 * открытым сутками.
 */
import { useCallback, useEffect, useState } from 'react'

const RECHECK_INTERVAL_MS = 60 * 60 * 1000  // 1 час

export interface TauriUpdateInfo {
  version: string
  notes: string | null
  date: string | null
}

type DownloadPhase = 'idle' | 'downloading' | 'installing' | 'ready-to-restart' | 'error'

export interface TauriUpdaterState {
  available: TauriUpdateInfo | null
  phase: DownloadPhase
  progress: number       // 0..100
  downloaded: number     // bytes
  total: number          // bytes (может быть 0 если неизвестен)
  error: string | null
}

function isTauriContext(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

export function useTauriUpdater() {
  const [state, setState] = useState<TauriUpdaterState>({
    available: null,
    phase: 'idle',
    progress: 0,
    downloaded: 0,
    total: 0,
    error: null,
  })

  // Храним сам update-object чтобы downloadAndInstall вызвать позже при клике
  const [updateHandle, setUpdateHandle] = useState<any>(null)

  // Проверка обновлений — тихая, не бросает в UI если не Tauri / нет интернета
  const checkForUpdates = useCallback(async () => {
    if (!isTauriContext()) {
      return
    }
    try {
      // Динамический импорт — чтобы в браузере без Tauri не падать на уровне бандла
      const { check } = await import('@tauri-apps/plugin-updater')
      const update = await check()
      if (update && update.available) {
        setUpdateHandle(update)
        setState((prev) => ({
          ...prev,
          available: {
            version: update.version,
            notes: update.body ?? null,
            date: update.date ?? null,
          },
          error: null,
        }))
      } else {
        // Нет обновлений — сбрасываем (но не трогаем error)
        setUpdateHandle(null)
        setState((prev) => ({ ...prev, available: null }))
      }
    } catch (exc) {
      const msg = exc instanceof Error ? exc.message : String(exc)
      // ConnectionError / опера-прокси не готов — не считаем критикой, логируем в консоль
      console.warn('[tauri-updater] check failed:', msg)
      setState((prev) => ({ ...prev, error: msg }))
    }
  }, [])

  // Скачать и установить + перезапустить
  const install = useCallback(async () => {
    if (!updateHandle) {
      return
    }
    setState((prev) => ({ ...prev, phase: 'downloading', progress: 0, downloaded: 0, total: 0, error: null }))

    try {
      let totalBytes = 0
      await updateHandle.downloadAndInstall((event: any) => {
        if (event.event === 'Started') {
          totalBytes = event.data?.contentLength ?? 0
          setState((prev) => ({ ...prev, total: totalBytes }))
        } else if (event.event === 'Progress') {
          const chunkLen = event.data?.chunkLength ?? 0
          setState((prev) => {
            const downloaded = prev.downloaded + chunkLen
            const progress = totalBytes > 0 ? Math.round((downloaded / totalBytes) * 100) : 0
            return { ...prev, downloaded, progress }
          })
        } else if (event.event === 'Finished') {
          setState((prev) => ({ ...prev, phase: 'installing', progress: 100 }))
        }
      })

      // downloadAndInstall применил апдейт синхронно — можно перезапускать
      setState((prev) => ({ ...prev, phase: 'ready-to-restart' }))

      const { relaunch } = await import('@tauri-apps/plugin-process')
      await relaunch()
    } catch (exc) {
      const msg = exc instanceof Error ? exc.message : String(exc)
      console.error('[tauri-updater] install failed:', msg)
      setState((prev) => ({ ...prev, phase: 'error', error: msg }))
    }
  }, [updateHandle])

  // Сбросить notification (Настя нажала "позже")
  const dismiss = useCallback(() => {
    setState((prev) => ({ ...prev, available: null }))
    setUpdateHandle(null)
  }, [])

  // Первый check при монтировании + периодический каждый час
  useEffect(() => {
    checkForUpdates()
    const timer = setInterval(checkForUpdates, RECHECK_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [checkForUpdates])

  return {
    ...state,
    checkForUpdates,
    install,
    dismiss,
  }
}
