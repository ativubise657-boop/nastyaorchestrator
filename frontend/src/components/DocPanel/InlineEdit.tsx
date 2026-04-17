// Inline-редактирование имени файла или папки
import { useState, useRef } from 'react'

interface InlineEditProps {
  value: string
  onSave: (v: string) => void
  onCancel: () => void
}

export function InlineEdit({ value, onSave, onCancel }: InlineEditProps) {
  const [text, setText] = useState(value)
  const inputRef = useRef<HTMLInputElement>(null)

  return (
    <input
      ref={inputRef}
      className="doc-panel__inline-edit"
      value={text}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && text.trim()) onSave(text.trim())
        if (e.key === 'Escape') onCancel()
      }}
      onBlur={() => { if (text.trim() && text.trim() !== value) onSave(text.trim()); else onCancel() }}
      autoFocus
    />
  )
}
