import { useCallback, useRef } from 'react'
import { useStore } from '../stores'

// Хук инкапсулирует логику отправки сообщения и состояние input
export function useChat() {
  const sendMessage = useStore((s) => s.sendMessage)
  const sendingMessage = useStore((s) => s.sendingMessage)
  const selectedProjectId = useStore((s) => s.selectedProjectId)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSend = useCallback(
    async (text: string, modelOverride?: string) => {
      const trimmed = text.trim()
      if (!trimmed || sendingMessage || !selectedProjectId) return
      await sendMessage(trimmed, modelOverride)
    },
    [sendMessage, sendingMessage, selectedProjectId],
  )

  // Авторесайз textarea по контенту
  const autoResize = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    // Максимум 40% высоты окна — textarea растёт вверх по мере ввода
    const maxH = Math.floor(window.innerHeight * 0.4)
    el.style.height = Math.min(el.scrollHeight, maxH) + 'px'
  }, [])

  return {
    handleSend,
    sendingMessage,
    selectedProjectId,
    textareaRef,
    autoResize,
  }
}

// Markdown рендерер на базе marked (GFM-таблицы из коробки)
import { marked } from 'marked'

export function renderMarkdown(text: string): string {
  if (!text) return ''
  return marked(text) as string
}
