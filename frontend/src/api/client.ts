/** Cliente HTTP mínimo. Sin dependencias: fetch, tipos y errores explicitos. */

import { env } from '@/config/env'

export class ApiError extends Error {
  readonly status: number
  readonly url: string
  readonly body: unknown

  constructor(message: string, status: number, url: string, body: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.url = url
    this.body = body
  }

  /** Reintentar un 4xx no sirve de nada; un 5xx o un corte de red si. */
  get isRetryable(): boolean {
    return this.status === 0 || this.status === 429 || this.status >= 500
  }
}

type QueryValue = string | number | boolean | readonly (string | number)[] | undefined | null

/**
 * FastAPI espera los parametros repetidos para las listas
 * (`?type=wildfire&type=possible_fire`), no separados por coma.
 */
export function buildQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) {
      for (const item of value) search.append(key, String(item))
    } else {
      search.append(key, String(value))
    }
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

/** Convierte una respuesta no-2xx en `ApiError`, preservando el cuerpo. */
async function toApiError(
  response: Response,
  path: string,
  url: string,
): Promise<ApiError> {
  const body = await response.text().catch(() => '')
  let parsed: unknown = body
  try {
    parsed = JSON.parse(body)
  } catch {
    /* el cuerpo no era JSON; se conserva el texto crudo */
  }
  return new ApiError(
    `${response.status} ${response.statusText} en ${path}`,
    response.status,
    url,
    parsed,
  )
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const url = `${env.apiBaseUrl}${path}`

  let response: Response
  try {
    response = await fetch(url, {
      signal: signal ?? null,
      headers: { Accept: 'application/json' },
    })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    // status 0 = no hubo respuesta. Sin señal, DNS caido, servidor apagado.
    throw new ApiError('No se pudo contactar al servidor', 0, url, cause)
  }

  if (!response.ok) throw await toApiError(response, path, url)

  return (await response.json()) as T
}

export async function apiPost<T>(
  path: string,
  payload: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const url = `${env.apiBaseUrl}${path}`

  let response: Response
  try {
    response = await fetch(url, {
      method: 'POST',
      signal: signal ?? null,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(payload),
    })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new ApiError('No se pudo contactar al servidor', 0, url, cause)
  }

  if (!response.ok) throw await toApiError(response, path, url)

  // 204 no trae cuerpo. Hoy ningún POST devuelve 204, pero asumir que siempre
  // hay JSON es la clase de supuesto que revienta en producción.
  if (response.status === 204) return undefined as T

  return (await response.json()) as T
}
