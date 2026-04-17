// Общий API helper — используется во всех slice-файлах
export const API_BASE = ''

// Таймаут по умолчанию: 30 сек. Если бэкенд завис — не держим Promise вечно.
const DEFAULT_TIMEOUT_MS = 30000

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  // Для FormData не ставим Content-Type — браузер сам добавит boundary
  const isFormData = options?.body instanceof FormData
  const headers = isFormData
    ? { ...options?.headers }
    : { 'Content-Type': 'application/json', ...options?.headers }

  // AbortController для таймаута. Если вызывающий код передал свой signal — уважаем его,
  // но добавляем свой таймаут поверх (первый сработавший — побеждает).
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS)

  // Объединяем внешний signal (если есть) с нашим таймаутным
  let signal: AbortSignal = controller.signal
  if (options?.signal) {
    const externalSignal = options.signal
    // При срабатывании внешнего — прерываем наш контроллер тоже
    externalSignal.addEventListener('abort', () => controller.abort(), { once: true })
    // Если внешний уже отменён до вызова — сразу ставим нашу метку
    if (externalSignal.aborted) controller.abort()
    signal = controller.signal
  }

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
      signal,
    })
    clearTimeout(timeoutId)
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(`API error ${res.status}: ${text || res.statusText}`)
    }
    // 204 No Content — нет тела ответа (DELETE и т.п.)
    if (res.status === 204) return undefined as T
    return res.json()
  } catch (err) {
    clearTimeout(timeoutId)
    if (err instanceof Error && err.name === 'AbortError') {
      throw new Error('Превышен таймаут запроса (30 сек). Проверь что backend запущен.')
    }
    throw err
  }
}
