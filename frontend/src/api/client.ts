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

  if (!response.ok) {
    const body = await response.text().catch(() => '')
    let parsed: unknown = body
    try {
      parsed = JSON.parse(body)
    } catch {
      /* el cuerpo no era JSON; se conserva el texto crudo */
    }
    throw new ApiError(
      `${response.status} ${response.statusText} en ${path}`,
      response.status,
      url,
      parsed,
    )
  }

  return (await response.json()) as T
}
