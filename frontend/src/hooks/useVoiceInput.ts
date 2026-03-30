import { useState, useRef, useCallback, useEffect } from 'react'

// Типы для Web Speech API (Chrome)
interface SpeechRecognitionEvent extends Event {
  results: SpeechRecognitionResultList
  resultIndex: number
}

interface SpeechRecognitionInstance extends EventTarget {
  lang: string
  continuous: boolean
  interimResults: boolean
  start(): void
  stop(): void
  abort(): void
  onresult: ((ev: SpeechRecognitionEvent) => void) | null
  onend: (() => void) | null
  onerror: ((ev: Event & { error: string }) => void) | null
  onstart: (() => void) | null
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionInstance

// Проверка поддержки браузером
const getSpeechRecognition = (): SpeechRecognitionConstructor | null => {
  const w = window as unknown as Record<string, unknown>
  return (w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null) as SpeechRecognitionConstructor | null
}

export function useVoiceInput(lang = 'ru-RU') {
  const [isListening, setIsListening] = useState(false)
  const [interimText, setInterimText] = useState('')  // промежуточный текст (серый)
  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null)
  const onTranscriptRef = useRef<((text: string, isFinal: boolean) => void) | null>(null)
  const manualStopRef = useRef(false)

  const isSupported = !!getSpeechRecognition()

  const stop = useCallback(() => {
    manualStopRef.current = true
    recognitionRef.current?.stop()
    setIsListening(false)
    setInterimText('')
  }, [])

  const start = useCallback((onTranscript: (text: string, isFinal: boolean) => void) => {
    const SR = getSpeechRecognition()
    if (!SR) return

    // Остановить предыдущую сессию
    if (recognitionRef.current) {
      recognitionRef.current.abort()
    }

    const recognition = new SR()
    recognition.lang = lang
    recognition.continuous = true
    recognition.interimResults = true
    manualStopRef.current = false
    onTranscriptRef.current = onTranscript

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let finalText = ''
      let interim = ''

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i]
        const transcript = result[0].transcript
        if (result.isFinal) {
          finalText += transcript
        } else {
          interim += transcript
        }
      }

      setInterimText(interim)

      if (finalText) {
        onTranscriptRef.current?.(finalText, true)
      }
      if (interim) {
        onTranscriptRef.current?.(interim, false)
      }
    }

    recognition.onend = () => {
      // Chrome автоматически останавливает при молчании ~5с
      // Перезапускаем если пользователь не нажал стоп
      if (!manualStopRef.current) {
        try {
          recognition.start()
        } catch {
          setIsListening(false)
          setInterimText('')
        }
        return
      }
      setIsListening(false)
      setInterimText('')
    }

    recognition.onerror = (ev) => {
      // 'no-speech' — нормально, Chrome перезапустит через onend
      // 'not-allowed' — пользователь запретил микрофон
      if (ev.error === 'not-allowed' || ev.error === 'service-not-available') {
        manualStopRef.current = true
        setIsListening(false)
        setInterimText('')
      }
    }

    recognition.start()
    recognitionRef.current = recognition
    setIsListening(true)
  }, [lang])

  // Cleanup при размонтировании
  useEffect(() => {
    return () => {
      manualStopRef.current = true
      recognitionRef.current?.abort()
    }
  }, [])

  return { isListening, interimText, isSupported, start, stop }
}
