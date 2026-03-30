/**
 * Звуковое оповещение о новом сообщении.
 * Web Audio API — два тона в стиле ICQ "uh-oh". Без внешних файлов.
 */

let audioCtx: AudioContext | null = null

function getAudioContext(): AudioContext {
  if (!audioCtx) {
    audioCtx = new AudioContext()
  }
  // Браузер может заморозить контекст до первого клика
  if (audioCtx.state === 'suspended') {
    audioCtx.resume()
  }
  return audioCtx
}

/** Воспроизвести один тон */
function playTone(
  ctx: AudioContext,
  frequency: number,
  startTime: number,
  duration: number,
  volume: number = 0.3,
) {
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()

  osc.type = 'sine'
  osc.frequency.value = frequency
  gain.gain.setValueAtTime(volume, startTime)
  gain.gain.exponentialRampToValueAtTime(0.001, startTime + duration)

  osc.connect(gain)
  gain.connect(ctx.destination)

  osc.start(startTime)
  osc.stop(startTime + duration)
}

/** Звук "ICQ-style" — два коротких тона вверх-вниз */
export function playNotificationSound() {
  try {
    const ctx = getAudioContext()
    const now = ctx.currentTime

    // Первый тон — высокий
    playTone(ctx, 880, now, 0.12, 0.25)
    // Второй тон — ниже, с паузой
    playTone(ctx, 660, now + 0.15, 0.15, 0.2)
  } catch {
    // Если AudioContext недоступен — молча пропускаем
  }
}
