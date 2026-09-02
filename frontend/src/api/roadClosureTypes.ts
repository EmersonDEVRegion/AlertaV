/**
 * Espejo TypeScript del contrato de la capa de cortes de ruta.
 *
 * Fuente de verdad: `backend/app/api/v1/endpoints/events.py`
 * (`road_closures_geojson` y `_road_closure_feature`).
 *
 * # Las tres cosas del contrato que mandan sobre todo lo demás
 *
 *   1. **`severidad` está AUSENTE, no en cero, cuando la fuente no la publica.**
 *      El MOP la trae (0 a 5); el MTT no tiene ninguna escala que mapear. El
 *      backend omite la clave entera en ese caso, y la capa distingue los dos
 *      con `["has", "severidad"]`. Rellenarla con 0 en el cliente —que es lo
 *      que hace `?? 0` sin pensarlo— convertiría «no sabemos» en «sabemos que
 *      es leve», que es la dirección peligrosa del error: hacia «no pasa nada».
 *
 *   2. **Un corte NO es un siniestro.** `road_closure` está fuera de
 *      `CORRELATABLE_EVENT_TYPES` en el backend y entra con confianza 0,0: no
 *      crea incidentes, no mueve la confianza de ninguno y no abre ficha. Una
 *      emergencia del MOP sigue vigente durante SEMANAS, y si aportara peso le
 *      regalaría corroboración a cada choque ocurrido en esa cuesta durante
 *      todo ese tiempo.
 *
 *   3. **La ventana es de 30 días, no de 24 horas.** El MOP se actualiza los
 *      lunes. Con la ventana del resto de las capas, esta saldría vacía casi
 *      todos los días del año.
 */

import type { Feature, FeatureCollection, Point } from 'geojson'

/** Fuentes que alimentan la capa. Cualquier otra cadena se trata como MTT. */
export type RoadClosureSource = 'mop' | 'transporte_informa'

/** `properties` de cada feature de `GET /api/v1/events/road-closures/geojson`. */
export interface RoadClosureProperties {
  public_id: string
  /** ISO-8601. Cuándo EMPEZÓ la emergencia, no cuándo se supo. */
  timestamp: string
  source: string
  type: 'road_closure'
  confidence: number
  text: string
  commune: string | null
  /** Siempre `false`: una señal cruda no es un incidente confirmado. */
  is_confirmed_incident: boolean

  /**
   * 0 a 5, mayor es peor. **Sólo del MOP.**
   *
   * `null` significa «esta fuente no publica escala», no «gravedad mínima». Ver
   * la nota 1 de la cabecera. En el GeoJSON que llega por la red la clave
   * directamente no existe; `parseRoadClosures` la normaliza a `null` para el
   * lado TypeScript **y la borra del feature** antes de dárselo a MapLibre, para
   * que `["has","severidad"]` siga respondiendo lo que corresponde.
   */
  severidad: number | null

  /** Contexto operativo del MOP para el popup. Ausente en los avisos del MTT. */
  transito?: string | null
  transitable?: boolean | null
  gravedad?: string | null
  restriccion?: string | null
  rol?: string | null
}

export type RoadClosureFeature = Feature<Point, RoadClosureProperties>
export type RoadClosureCollection = FeatureCollection<Point, RoadClosureProperties>

/** Parámetros de `GET /api/v1/events/road-closures/geojson`. */
export interface RoadClosureQuery {
  /** 1–2160. Por defecto 720 (30 días). Ver la nota 3 de la cabecera. */
  hours?: number
  source?: readonly string[]
  limit?: number
}
