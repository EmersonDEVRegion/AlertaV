/**
 * Carga del artefacto de amenaza sísmica.
 *
 * # Por qué se hace `fetch` acá y no se le pasa la URL a MapLibre
 *
 * La versión original montaba `<Source data="/static/…">` con una cadena, para
 * que MapLibre descargara y parseara el archivo en su web worker y el hilo
 * principal no se bloqueara.
 *
 * El motivo de traerlo al hilo principal es **el estado de carga**. Con la
 * fuente por URL, la capa tenía que deducir si el archivo había llegado
 * escuchando `sourcedata` y `error` del mapa y filtrando por identificador de
 * fuente: una máquina de estados alimentada por eventos que MapLibre no promete
 * emitir —con el archivo en caché puede quedar cargado antes de que se enganche
 * el escucha— y de ahí salía el rebote del interruptor descrito en la Fase 1.
 * Una promesa sí resuelve o rechaza exactamente una vez.
 *
 * (Hubo un segundo motivo, ya extinto: el `heatmap` regional necesitaba los
 * centros de nodo como geometrías `Point`, y para derivarlos hacía falta tener
 * el objeto en el hilo principal. Esa capa se retiró — ver
 * `domain/hazardSymbology.ts`. El primer motivo basta y sigue en pie.)
 *
 * El costo es un `JSON.parse` en el hilo principal, una vez por sesión, tras un
 * gesto explícito del usuario que ya muestra un estado de carga. A cambio, la
 * capa deja de tener una clase entera de errores.
 */

import { HAZARD_SOURCE_URL } from '@/config/map'
import type {
  HazardCells,
  HazardGrid,
  HazardMetadata,
} from './hazardTypes'

/**
 * Rejilla vacía compartida.
 *
 * Referencia estable a propósito, igual que `EMPTY_RAIN`: `<Source data={…}>`
 * vuelve a subir los datos al worker cada vez que cambia la identidad del
 * objeto. Un literal nuevo por repintado haría que el estado "todavía no
 * cargado" fuera el único que cuesta trabajo en cada frame.
 */
export const EMPTY_HAZARD: HazardGrid = {
  cells: { type: 'FeatureCollection', features: [] },
  cellSizeDeg: null,
  metadata: null,
}

export class HazardLoadError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'HazardLoadError'
  }
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** Valida la forma del artefacto y lo deja listo para la fuente del mapa. */
export function parseHazardGrid(payload: unknown): HazardGrid {
  if (typeof payload !== 'object' || payload === null) {
    throw new HazardLoadError('El artefacto de amenaza no es un objeto JSON.')
  }

  const raw = payload as Record<string, unknown>
  const features = raw['features']

  if (raw['type'] !== 'FeatureCollection' || !Array.isArray(features)) {
    throw new HazardLoadError(
      'El artefacto de amenaza no es un FeatureCollection. ¿Se generó con scripts/fetch_seismic_hazard.py?',
    )
  }

  /*
   * Una colección vacía SÍ es un error acá, al revés que en la lluvia.
   *
   * En la lluvia, cero comunas significa "no llueve" y es la respuesta normal
   * durante meses. Acá el modelo es estático y cubre la región entera: cero
   * celdas sólo puede significar que el artefacto no se generó o se generó con
   * una caja equivocada. Tratarlo como "sin amenaza" pintaría un mapa limpio
   * sobre una de las zonas sísmicas más activas del planeta.
   */
  if (features.length === 0) {
    throw new HazardLoadError(
      'El artefacto de amenaza está vacío. Genera la capa con `python -m scripts.fetch_seismic_hazard`.',
    )
  }

  const cells: HazardCells = {
    type: 'FeatureCollection',
    features: features as HazardCells['features'],
  }

  const metadata = (raw['metadata'] ?? null) as HazardMetadata | null
  const size = metadata?.cell_size_deg
  const cellSizeDeg =
    size && isFiniteNumber(size.lon) && isFiniteNumber(size.lat)
      ? (size.lon + size.lat) / 2
      : null

  return { cells, cellSizeDeg, metadata }
}

/**
 * Descarga y parsea el artefacto.
 *
 * No pasa por `api/client.ts`: eso habla con `/api/v1` y envuelve errores de la
 * API. Esto es un archivo estático del mismo origen, sin autenticación ni
 * envoltorio de respuesta, y su modo de falla interesante —«no se ha generado»—
 * es un 404 que merece un mensaje propio y accionable.
 *
 * `signal` viene de react-query: si el usuario apaga la capa a mitad de
 * descarga, la petición se aborta de verdad.
 */
export async function fetchHazardGrid(signal?: AbortSignal): Promise<HazardGrid> {
  let response: Response
  try {
    response = await fetch(HAZARD_SOURCE_URL, {
      signal: signal ?? null,
      /*
       * `default`, no `force-cache`.
       *
       * El modelo cambia cada varios años, así que la tentación de forzar la
       * caché es fuerte — pero `force-cache` sirve la copia guardada **sin
       * revalidar**, y entonces una capa regenerada no llegaría nunca al
       * navegador que ya tiene la vieja. El endpoint responde con `ETag` y
       * `must-revalidate`: con la política por defecto, una carga normal cuesta
       * un 304 sin cuerpo y una capa nueva sí se descarga.
       */
      cache: 'default',
    })
  } catch (error) {
    // `AbortError` tiene que propagarse tal cual: react-query lo distingue de
    // un fallo real y no lo cuenta como reintento.
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new HazardLoadError('No se pudo contactar al servidor de la capa de amenaza.')
  }

  if (!response.ok) {
    throw new HazardLoadError(await describeFailure(response))
  }

  /*
   * `response.json()` puede reventar aunque la respuesta sea 200: un proxy o un
   * hosting con reescritura a `index.html` devuelve HTML con código 200, y el
   * `SyntaxError` que sale de ahí no es un `HazardLoadError` — se escaparía sin
   * mensaje útil. Se traduce.
   */
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new HazardLoadError(
      'La respuesta de la capa de amenaza no era JSON. ¿La petición llegó al backend ' +
        'o la interceptó el hosting del frontend?',
    )
  }

  return parseHazardGrid(payload)
}

/**
 * Texto del fallo, aprovechando el sobre de error del backend si viene.
 *
 * El backend responde `{ error: { code, message, detail } }` y su `message` ya
 * dice qué hacer —«genera la capa con `python -m scripts.fetch_seismic_hazard`»—
 * que es infinitamente más útil que traducir un número de estado. Si el cuerpo
 * no trae sobre (un 502 del proxy de Render, por ejemplo, que es HTML), se cae
 * al mensaje genérico.
 */
async function describeFailure(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: { message?: unknown } }
    const message = body?.error?.message
    if (typeof message === 'string' && message.trim() !== '') return message
  } catch {
    // Cuerpo no-JSON: no aporta nada y no es motivo para perder el estado.
  }

  return response.status === 404
    ? 'La capa de amenaza no está publicada en el servidor.'
    : `El servidor respondió ${response.status} al pedir la capa de amenaza.`
}
