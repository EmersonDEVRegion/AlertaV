/**
 * Cortes de agua de Esval: el contrato de `GET /events/water-cuts/geojson`.
 *
 * Espejo de `backend/app/schemas/water_cut.py` y de `feature_de_corte` en
 * `backend/app/services/water_cut_service.py`. La respuesta es un GeoJSON; acá
 * se aplana a objetos tipados, porque el panel, la tarjeta y el mapa leen
 * propiedades, no geometrías.
 *
 * No son incidentes: `water_cut` está fuera del motor de correlación, no tiene
 * confianza, ni `code`, ni estado. Esval tampoco publica cuántos clientes
 * afecta cada corte, así que ese campo no existe (y no se rellena con cero).
 */

import type { HealthStatus } from './health'

export interface WaterCut {
  /** `public_id` de la fila en el backend. Estable entre sondeos. */
  id: string
  /** Folio de Esval. */
  sisda: string | null
  comuna: string | null
  /** «emergencia», «programado» o lo que diga Esval, ya sin «Corte …». */
  tipo: string | null
  /** `false` = de emergencia. `null` = Esval no lo dijo de forma reconocible. */
  programado: boolean | null
  motivo: string | null
  calles: string | null
  sector: string | null
  /** ISO 8601. Hora referencial de Esval. */
  inicio: string | null
  /** ISO 8601. Esval la marca como «valor referencial estimado». */
  fin: string | null
  suministro_alternativo: boolean | null
  /** Visor oficial del corte, siempre en https y siempre de Esval. */
  url_mapa: string | null
  /** Cuándo lo vio AlertaV por última vez. */
  visto_en: string | null
  /** `[lon, lat]`, o `null` si el visor de Esval no respondió al leerlo. */
  coordinates: [number, number] | null
}

export interface WaterCutSource {
  collector: string
  /** Mismas reglas que `/collectors/health`. `undefined` si llegó algo desconocido. */
  estado: HealthStatus | undefined
  ultima_corrida: string | null
  /**
   * Inicio de la última corrida que leyó la API de Esval. `null` = nunca se
   * pudo leer: la capa todavía no tiene datos y no se muestra.
   */
  ultima_lectura: string | null
  detalle: string | null
}

export interface WaterCutsResponse {
  generado_en: string
  cuts: WaterCut[]
  fuente: WaterCutSource
}
