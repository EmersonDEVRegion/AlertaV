/**
 * Identificador anónimo de este navegador para el reporte ciudadano.
 *
 * Se genera una vez y se guarda en `localStorage`. No dice nada de la persona:
 * es un UUID al azar, y el backend guarda sólo su HMAC. Sirve para una sola
 * cosa, contar vecinos distintos: tres reportes del mismo navegador son un
 * vecino, no tres (backend: `app/services/ciudadanos.py`).
 *
 * Si el almacenamiento no está disponible (modo privado, bloqueado) se usa uno
 * en memoria: el reporte se envía igual y el backend cae a la red para contar.
 */

const STORAGE_KEY = 'alertav:reporter-id'

let enMemoria: string | null = null

function nuevo(): string {
  const crypto = globalThis.crypto
  if (crypto && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  if (crypto && typeof crypto.getRandomValues === 'function') {
    const bytes = crypto.getRandomValues(new Uint8Array(16))
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`
}

/** El mismo valor cada vez: 8 a 64 caracteres `[A-Za-z0-9_-]`, como pide el backend. */
export function reporterId(): string {
  try {
    const guardado = window.localStorage.getItem(STORAGE_KEY)
    if (guardado && /^[A-Za-z0-9_-]{8,64}$/.test(guardado)) return guardado
    const valor = nuevo()
    window.localStorage.setItem(STORAGE_KEY, valor)
    return valor
  } catch {
    enMemoria ??= nuevo()
    return enMemoria
  }
}
