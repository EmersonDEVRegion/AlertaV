/**
 * Guardas para leer respuestas de la API sin confiar en su forma. Cada cliente
 * de `api/` valida lo que recibe antes de entregarlo a la interfaz.
 */

export type Raw = Record<string, unknown>

/** Un objeto JSON (no `null` ni un arreglo). */
export function isRecord(value: unknown): value is Raw {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** Un número utilizable: ni `NaN` ni infinito. */
export function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** El texto tal cual, o `fallback` si no es un string. */
export function toText(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

/** El texto sin espacios en los bordes, o `null` si no hay texto. */
export function trimmedText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : null
}
