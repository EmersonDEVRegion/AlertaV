/**
 * Contrato de `GET /api/v1/feed/noticias`.
 *
 * Espejo de `backend/app/schemas/news_feed.py`. Si cambia uno, cambia el otro.
 *
 * # Lo que NO es
 *
 * No es un incidente ni una señal del mapa. Desde el 2026-10-06 la prensa no
 * entra al motor: una nota llega con horas de atraso y abría pines que ya no
 * describían el presente. Se lee como información, con la hora de publicación
 * a la vista.
 */

import type { HealthStatus } from './health'

export interface NewsFeedItem {
  id: string
  titular: string
  bajada: string | null
  medio: string | null
  url: string | null
  comuna: string | null
  /** Lo que el clasificador leyó (`structural_fire`, `accident`…). */
  tipo: string
  /** Cuándo se publicó, o la cota superior conocida si `hora_aproximada`. */
  publicada_en: string
  hora_aproximada: boolean
  detectada_en: string
}

export interface NewsFeedSource {
  collector: string
  estado: HealthStatus | undefined
  ultima_corrida: string | null
  detalle: string | null
}

export interface NewsFeedResponse {
  generado_en: string
  horas: number
  total: number
  items: NewsFeedItem[]
  fuente: NewsFeedSource
}

export interface NewsFeedQuery {
  horas?: number
  limit?: number
}
