/**
 * Cliente de la capa meteorológica de NUESTRO backend.
 *
 * `GET /api/v1/events/weather/geojson` → `FeatureCollection` de puntos, uno por
 * comuna con lluvia pronosticada en las próximas 24 h.
 *
 * No confundir con `api/weather.ts`, que llama a Open-Meteo directamente para
 * el viento del cono de propagación. Esta capa pasa por el backend porque el
 * flag de riesgo lo calcula él, con umbrales configurables por `.env`:
 * recalcularlo en el navegador crearía dos implementaciones de la misma regla y
 * el día que se muevan los umbrales el mapa y la base dirían cosas distintas.
 *
 * # Por qué se consume el GeoJSON y no la ruta tipada
 *
 * Al revés que con sismos e incidentes. Ahí la ficha necesita el objeto completo
 * y el GeoJSON se arma en el cliente. Acá no hay ficha: la lluvia no se
 * selecciona, no abre panel y no tiene detalle propio. Es una capa de fondo y
 * nada más, así que el formato que el mapa consume directo es también el único
 * que se necesita. Pedir la lista tipada para volver a convertirla sería trabajo
 * en el hilo principal a cambio de nada.
 */

import { apiGet, buildQuery } from './client'
import type {
  RainCollection,
  RainFeature,
  RainLevel,
  RainProperties,
  RainQuery,
} from './rainTypes'

/**
 * Colección vacía compartida.
 *
 * Referencia estable a propósito: `<Source data={...}>` vuelve a subir los datos
 * al worker cada vez que cambia la identidad del objeto. Devolver un literal
 * nuevo en cada render haría que el estado "soleado" —el más común del año—
 * fuera el único que provoca trabajo en cada repintado.
 */
export const EMPTY_RAIN: RainCollection = { type: 'FeatureCollection', features: [] }

const LEVELS: readonly RainLevel[] = ['seco', 'lluvia', 'riesgo', 'riesgo_alto']

/** Un solo aviso por sesión: un `console.warn` por feature sería peor que el bug. */
let warnedAboutBoolean = false

/**
 * Normaliza `riesgo_inundacion` a un booleano real.
 *
 * Es la línea más importante del archivo. Las expresiones de MapLibre no
 * comparan entre tipos: `["==", ["get","riesgo_inundacion"], true]` devuelve
 * `false` para la cadena `"true"`, **sin error y sin aviso**. El anillo de
 * riesgo simplemente no aparecería y nadie se enteraría hasta el próximo
 * temporal.
 *
 * `Boolean(valor)` no sirve como respaldo: `Boolean("false")` es `true`, que es
 * exactamente el error contrario y el que más caro sale en una app de
 * emergencias. Por eso se enumeran las formas aceptadas.
 */
function toStrictBoolean(value: unknown, field: string): boolean {
  if (typeof value === 'boolean') return value

  if (!warnedAboutBoolean) {
    warnedAboutBoolean = true
    console.warn(
      `[AlertaV/lluvia] "${field}" llegó como ${typeof value} (${JSON.stringify(value)}) ` +
        'y el contrato lo declara booleano. Se normalizó, pero revisa el backend: ' +
        'las expresiones de MapLibre no comparan entre tipos y este campo decide ' +
        'si se dibuja el anillo de riesgo de inundación.',
    )
  }

  if (value === 'true' || value === 1) return true
  return false
}

function toNumber(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function toText(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

/**
 * `motivos` viaja concatenado con `; ` en el GeoJSON y como lista en la ruta
 * tipada. Si algún día el backend unificara los dos formatos, esto absorbe el
 * cambio en vez de dejar `"[\"intensidad…\"]"` en pantalla.
 */
function toReasons(value: unknown): string {
  if (Array.isArray(value)) return value.filter((item) => typeof item === 'string').join('; ')
  return toText(value)
}

function toLevel(value: unknown): RainLevel {
  return LEVELS.includes(value as RainLevel) ? (value as RainLevel) : 'lluvia'
}

/**
 * Zona horaria de la V Región. Ver la nota de `RainProperties.ventana`.
 *
 * Fija y no `undefined` (la del navegador): el usuario que mire esto desde
 * fuera de Chile tiene que ver la hora del lugar donde va a llover, no la suya.
 */
const CHILE_TZ = 'America/Santiago'

/**
 * Los formateadores se construyen UNA vez.
 *
 * `Intl.DateTimeFormat` es caro de instanciar —carga los datos de la zona— y
 * barato de reutilizar. Crearlo dentro del bucle costaría 72 construcciones por
 * respuesta para producir siempre el mismo objeto.
 */
const CLOCK = new Intl.DateTimeFormat('es-CL', {
  timeZone: CHILE_TZ,
  hour: '2-digit',
  minute: '2-digit',
  /*
   * `hourCycle: 'h23'` explícito, no `hour12: false` a secas.
   *
   * Varias locales resuelven `hour12: false` como el ciclo **h24**, que numera
   * las horas de 1 a 24 y escribe la medianoche como `24:00`. Una ventana que
   * empiece a medianoche diría "24:00 → 07:00", que se lee como el final del
   * día en vez del principio. `h23` es el 00–23 de siempre.
   */
  hourCycle: 'h23',
})

/**
 * Fecha civil chilena en `AAAA-MM-DD`, para contar los días que cruza la ventana.
 *
 * `en-CA` y no `es-CL`: es el atajo estándar para que `Intl` emita ISO. Con
 * `es-CL` saldría `26-08-2026`, que `Date.parse` no acepta.
 */
const DAY = new Intl.DateTimeFormat('en-CA', {
  timeZone: CHILE_TZ,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

const DAY_MS = 86_400_000

/**
 * Días civiles chilenos entre las dos marcas.
 *
 * Se cuenta sobre la fecha ya convertida, no restando los `Date`: una ventana
 * de 23 h que empiece a las 22:00 cruza la medianoche igual, y una de 25 h que
 * empiece a las 02:00 también — la duración no dice nada sobre cuántas veces
 * cambió el día. Ambas cadenas se parsean como medianoche UTC, así que la resta
 * es exacta y el horario de verano no la desvía.
 */
function daysApart(start: Date, end: Date): number {
  return Math.round((Date.parse(DAY.format(end)) - Date.parse(DAY.format(start))) / DAY_MS)
}

function toDate(value: unknown): Date | null {
  if (typeof value !== 'string' || value === '') return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/**
 * `"14:00 → 21:00"`, o `"14:00 → 09:00 +1 d"` si la ventana cruza la medianoche.
 *
 * El sufijo no es un adorno. La ventana por defecto son 24 h, así que **casi
 * siempre termina al día siguiente**: sin la marca, `"21:00 → 09:00"` se lee
 * como una ventana de doce horas hacia atrás. Se compara el día ya convertido a
 * hora de Chile, no el del `Date` en UTC, porque el cambio de fecha ocurre a
 * medianoche local.
 */
function toWindow(startRaw: unknown, endRaw: unknown): string {
  const start = toDate(startRaw)
  const end = toDate(endRaw)
  if (!start || !end) return ''

  const days = daysApart(start, end)
  const suffix = days > 0 ? ` +${days} d` : ''
  return `${CLOCK.format(start)} → ${CLOCK.format(end)}${suffix}`
}

/** Un punto con coordenadas finitas. Un `NaN` haría que MapLibre lo dibuje en cualquier parte. */
function toPoint(geometry: unknown): [number, number] | null {
  if (typeof geometry !== 'object' || geometry === null) return null
  const shape = geometry as Record<string, unknown>
  if (shape['type'] !== 'Point') return null

  const coords = shape['coordinates']
  if (!Array.isArray(coords) || coords.length < 2) return null

  const [lon, lat] = coords
  if (typeof lon !== 'number' || typeof lat !== 'number') return null
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return null
  if (lon < -180 || lon > 180 || lat < -90 || lat > 90) return null

  return [lon, lat]
}

function toFeature(raw: unknown): RainFeature | null {
  if (typeof raw !== 'object' || raw === null) return null

  const shape = raw as Record<string, unknown>
  const coordinates = toPoint(shape['geometry'])
  if (!coordinates) return null

  const source = (
    typeof shape['properties'] === 'object' && shape['properties'] !== null
      ? shape['properties']
      : {}
  ) as Record<string, unknown>

  const properties: RainProperties = {
    public_id: toText(source['public_id']),
    comuna: toText(source['comuna'], 'sin comuna'),
    inicio: toText(source['inicio']),
    fin: toText(source['fin']),
    ventana_horas: toNumber(source['ventana_horas'], 24),
    mm_total: toNumber(source['mm_total'], 0),
    mm_hora_max: toNumber(source['mm_hora_max'], 0),
    mm_3h_max: toNumber(source['mm_3h_max'], 0),
    hora_pico: typeof source['hora_pico'] === 'string' ? source['hora_pico'] : null,
    // `null` es legítimo: no todos los modelos publican la variable. Y NO
    // filtra nada: 20 mm/h con 30 % de probabilidad es justo el escenario que
    // una app de emergencias tiene que mostrar.
    probabilidad_max:
      typeof source['probabilidad_max'] === 'number' ? source['probabilidad_max'] : null,
    horas_con_lluvia: toNumber(source['horas_con_lluvia'], 0),
    riesgo_inundacion: toStrictBoolean(source['riesgo_inundacion'], 'riesgo_inundacion'),
    nivel: toLevel(source['nivel']),
    motivos: toReasons(source['motivos']),
    modelo: toText(source['modelo'], 'desconocido'),
    es_pronostico: toStrictBoolean(source['es_pronostico'] ?? true, 'es_pronostico'),
    is_confirmed_incident: toStrictBoolean(
      source['is_confirmed_incident'] ?? false,
      'is_confirmed_incident',
    ),
    // Derivada, no del contrato. Se calcula acá y no en el estilo porque
    // MapLibre no sabe de zonas horarias. Ver `RainProperties.ventana`.
    ventana: toWindow(source['inicio'], source['fin']),
  }

  return { type: 'Feature', geometry: { type: 'Point', coordinates }, properties }
}

/**
 * Valida y normaliza la respuesta.
 *
 * Exportada para poder probarla sin red: es donde vive el riesgo de que el
 * contrato se mueva. Una colección vacía se devuelve tal cual —**es una
 * respuesta correcta**, significa "ninguna comuna con lluvia pronosticada"— y
 * una respuesta con forma inesperada también cae en vacío, porque un mapa sin
 * lluvia es un modo de falla mucho más benigno que un estilo que no compila.
 */
export function parseRainCollection(payload: unknown): RainCollection {
  if (typeof payload !== 'object' || payload === null) return EMPTY_RAIN

  const shape = payload as Record<string, unknown>
  const raw = shape['features']
  if (!Array.isArray(raw)) return EMPTY_RAIN
  if (raw.length === 0) return EMPTY_RAIN

  const features: RainFeature[] = []
  for (const item of raw) {
    const feature = toFeature(item)
    if (feature) features.push(feature)
  }

  if (features.length === 0) return EMPTY_RAIN
  return { type: 'FeatureCollection', features }
}

/** Cuántas comunas traen el flag de riesgo. Alimenta el subtítulo del panel. */
export function countFloodRisk(collection: RainCollection): number {
  let total = 0
  for (const feature of collection.features) {
    if (feature.properties.riesgo_inundacion) total += 1
  }
  return total
}

/** `GET /api/v1/events/weather/geojson` */
export async function fetchRainGeojson(
  params: RainQuery = {},
  signal?: AbortSignal,
): Promise<RainCollection> {
  const payload = await apiGet<unknown>(
    `/events/weather/geojson${buildQuery({ ...params })}`,
    signal,
  )
  return parseRainCollection(payload)
}
