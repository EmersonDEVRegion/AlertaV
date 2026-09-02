/**
 * Cliente de la capa de cortes de ruta.
 *
 * `GET /api/v1/events/road-closures/geojson` → `FeatureCollection` de puntos,
 * uno por emergencia de Vialidad (MOP) o aviso operativo del MTT.
 *
 * # Por qué se consume el GeoJSON y no la ruta tipada
 *
 * Igual que la lluvia y al revés que sismos e incidentes. Ahí la ficha necesita
 * el objeto completo y el GeoJSON se arma en el cliente; acá no hay ficha —un
 * corte no se selecciona ni abre panel— así que el formato que el mapa consume
 * directo es también el único que se necesita. Y además: `severidad` vive en
 * `raw_data`, que la ruta genérica `/events` **no expone**. Sin esta ruta
 * dedicada llegaría un punto sin lo único que permite jerarquizarlo.
 *
 * # La línea que importa de este archivo
 *
 * `normaliseSeverity`. Todo lo demás es plomería.
 */

import { apiGet, buildQuery } from './client'
import type {
  RoadClosureCollection,
  RoadClosureFeature,
  RoadClosureProperties,
  RoadClosureQuery,
} from './roadClosureTypes'

/**
 * Colección vacía compartida.
 *
 * Referencia estable a propósito: `<Source data={...}>` vuelve a subir los
 * datos al worker cada vez que cambia la identidad del objeto. Devolver un
 * literal nuevo en cada render haría que el estado más común —ninguna ruta
 * cortada— fuera el único que provoca trabajo en cada repintado.
 */
export const EMPTY_ROAD_CLOSURES: RoadClosureCollection = {
  type: 'FeatureCollection',
  features: [],
}

/** Un solo aviso por sesión: un `console.warn` por feature sería peor que el bug. */
let warnedAboutSeverity = false

/**
 * Normaliza `severidad` **sin inventar un cero**.
 *
 * Es la línea más importante del archivo, y la trampa es el `??`:
 *
 *     const severidad = props.severidad ?? 0        // ← MENTIRA
 *
 * Un aviso del MTT no tiene escala publicada. Pintarlo como severidad 0 lo
 * declara «la emergencia más leve del MOP», que es una afirmación que nadie
 * midió y que empuja en la dirección peligrosa: hacia «no pasa nada». En una
 * capa que alguien mira para decidir si sale de casa, ese error no es cosmético.
 *
 * Lo que sí se hace es recortar al rango del contrato. `severity_rank` no puede
 * devolver nada fuera de 0..5 por construcción, así que un valor fuera de rango
 * significa que el contrato cambió y hay que enterarse — pero enterarse por
 * consola, no por un estilo de MapLibre que satura en silencio.
 */
function normaliseSeverity(value: unknown): number | null {
  if (value === undefined || value === null) return null

  if (typeof value !== 'number' || !Number.isFinite(value)) {
    if (!warnedAboutSeverity) {
      warnedAboutSeverity = true
      console.warn(
        `[AlertaV/cortes] "severidad" llegó como ${typeof value} ` +
          `(${JSON.stringify(value)}) y el contrato la declara entero 0–5. ` +
          'Se descartó. Revisa `_road_closure_feature` en el backend: este campo ' +
          'decide el color y el tamaño de cada corte en el mapa.',
      )
    }
    return null
  }

  return Math.min(5, Math.max(0, Math.round(value)))
}

function toText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function toTextOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

/**
 * Un feature crudo → uno tipado, con la severidad normalizada.
 *
 * **La clave `severidad` se BORRA del objeto cuando no hay valor**, en vez de
 * quedar en `null`. Es deliberado y es la mitad de `normaliseSeverity`: el
 * mismo objeto que sale de acá es el que se le pasa a MapLibre, y la capa
 * distingue las dos fuentes con `["has", "severidad"]`. Una clave presente con
 * valor nulo haría que `has` respondiera `true` para todos los avisos del MTT y
 * los mandaría a la rampa del MOP, pintados como severidad 0 — exactamente la
 * mentira que este módulo existe para evitar.
 *
 * En el lado TypeScript la propiedad sigue declarada como `number | null`
 * porque ahí lo cómodo es leerla siempre; el `delete` sólo afecta al objeto que
 * viaja al estilo.
 */
function parseFeature(feature: RoadClosureFeature): RoadClosureFeature {
  const raw = (feature.properties ?? {}) as Partial<RoadClosureProperties>
  const severidad = normaliseSeverity(raw.severidad)

  const properties: RoadClosureProperties = {
    public_id: toText(raw.public_id),
    timestamp: toText(raw.timestamp),
    source: toText(raw.source),
    type: 'road_closure',
    confidence: typeof raw.confidence === 'number' ? raw.confidence : 0,
    text: toText(raw.text),
    commune: toTextOrNull(raw.commune),
    is_confirmed_incident: false,
    severidad,
    transito: toTextOrNull(raw.transito),
    gravedad: toTextOrNull(raw.gravedad),
    restriccion: toTextOrNull(raw.restriccion),
    rol: toTextOrNull(raw.rol),
    transitable: typeof raw.transitable === 'boolean' ? raw.transitable : null,
  }

  // Ver el docstring. Sin esto, `["has","severidad"]` deja de discriminar.
  if (severidad === null) {
    delete (properties as { severidad?: number | null }).severidad
  }

  return { ...feature, properties }
}

export function parseRoadClosures(payload: RoadClosureCollection): RoadClosureCollection {
  const features = Array.isArray(payload?.features) ? payload.features : []
  return { type: 'FeatureCollection', features: features.map(parseFeature) }
}

export async function fetchRoadClosuresGeojson(
  params: RoadClosureQuery = {},
  signal?: AbortSignal,
): Promise<RoadClosureCollection> {
  const query = buildQuery({
    hours: params.hours,
    source: params.source ? [...params.source] : undefined,
    limit: params.limit,
  })
  const payload = await apiGet<RoadClosureCollection>(
    `/api/v1/events/road-closures/geojson${query}`,
    signal,
  )
  return parseRoadClosures(payload)
}

/**
 * Cuántos cortes son efectivos (`severidad >= 4`, o sea: no se puede pasar).
 *
 * Lo usa el panel para el subtítulo y el mapa para no arrancar la animación del
 * anillo cuando no hay nada que pulsar. El umbral se importa de la simbología
 * en vez de escribirse acá: es el mismo 4 que decide el salto al rojo.
 */
export function countCutRoutes(collection: RoadClosureCollection): number {
  return collection.features.filter((feature) => {
    const severidad = feature.properties?.severidad
    return typeof severidad === 'number' && severidad >= 4
  }).length
}
