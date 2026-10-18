/**
 * Conversión de incidentes tipados a GeoJSON para MapLibre.
 *
 * Dos decisiones que importan:
 *
 * 1. La fuente de datos del mapa se construye desde `/incidents/active`, no
 *    desde `/incidents/geojson`. El backend ofrece ambos —y desde la v2.0.0 los
 *    dos traen `confidence_level`—, pero la ficha necesita el objeto tipado
 *    completo igual; usar una sola llamada evita dos vistas del mundo que
 *    pueden desincronizarse.
 *
 * 2. Sólo viajan propiedades escalares. MapLibre serializa los arreglos y
 *    objetos anidados de las propiedades de un feature, así que `sources` no se
 *    incluye: el mapa sólo necesita lo justo para colorear, y `code` para
 *    volver al objeto real.
 */

import type { FeatureCollection, Point } from 'geojson'
import type { ConfidenceLevel, Incident, IncidentType } from '@/api/types'
import type { WaterCut } from '@/api/waterCutTypes'
import { type IncidentLayerKey, layerOf } from '@/domain/families'
import { providerOf } from '@/domain/powerSymbology'
import { incertidumbreDe } from '@/domain/locationPrecision'
import {
  type AreaBounds,
  type AreaGeometry,
  areaBounds,
  asAreaGeometry,
} from '@/domain/serviceArea'
import { isClosed, levelOf, needsVerificationCaveat } from '@/domain/symbology'

interface IncidentFeatureProps {
  code: string
  /**
   * Tipo del incidente, tal cual lo entrega el backend.
   *
   * Se agrega para que la capa `symbol` pueda elegir icono con un `match`. Es
   * más fino que `layer`: dentro de la familia `other` conviven un rescate y
   * una contingencia sin clasificar, y merecen glifos distintos.
   */
  type: IncidentType
  /**
   * Capa a la que pertenece el incidente: decide con qué paleta se pinta y qué
   * casilla lo enciende. Se precalcula acá porque la API no manda `family` y
   * las expresiones de estilo no pueden derivarla.
   */
  layer: IncidentLayerKey
  /** Tramo de la política v2.0.0. Decide el color del relleno. */
  confidence_level: ConfidenceLevel
  /** Estado cerrado → mismo color atenuado y anillo punteado. */
  is_closed: boolean
  /** Tramo `confirmed` sin que nadie haya ido al lugar → punto central hueco. */
  unverified_confirmed: boolean
  /** '' en vez de null: simplifica el filtro de la capa de anillo. */
  alert_level: string
  confidence: number
  is_official_confirmed: boolean
  /** Radio del círculo de incertidumbre, en metros. 0 = sin círculo. */
  incertidumbre_m: number
}

export type IncidentFeatureCollection = FeatureCollection<Point, IncidentFeatureProps>

export function toFeatureCollection(
  incidents: readonly Incident[],
): IncidentFeatureCollection {
  return {
    type: 'FeatureCollection',
    features: incidents.map((incident) => ({
      type: 'Feature',
      // Sin `id` a nivel de feature: MapLibre exige enteros y `code` es texto.
      // La fuente declara `promoteId: 'code'`, que sí acepta identificadores de
      // texto, y el resalte del seleccionado se hace con un filtro sobre `code`.
      geometry: { type: 'Point', coordinates: [incident.lon, incident.lat] },
      properties: {
        code: incident.code,
        type: incident.type,
        layer: layerOf(incident.type),
        confidence_level: levelOf(incident),
        is_closed: isClosed(incident.status),
        unverified_confirmed: needsVerificationCaveat(incident),
        alert_level: incident.alert_level ?? '',
        confidence: incident.confidence,
        is_official_confirmed: incident.is_official_confirmed,
        incertidumbre_m: incertidumbreDe(incident.ubicacion_precision),
      },
    })),
  }
}

/**
 * Propiedades de un corte de luz en su fuente propia.
 *
 * `provider` va resuelto (declarado o inferido de las fuentes) porque la capa
 * lo usa para el color y una expresión de estilo no puede mirar `sources`.
 * `affected_clients` va en 0 cuando no se informó: la agrupación los SUMA, y
 * un `null` en la suma envenenaría el total del racimo. La ficha, que sí
 * distingue «sin dato» de «cero», lee el objeto tipado, no esto.
 */
interface OutageFeatureProps {
  code: string
  provider: 'chilquinta' | 'cge' | 'unknown'
  affected_clients: number
  is_closed: boolean
}

export type OutageFeatureCollection = FeatureCollection<Point, OutageFeatureProps>

export function toOutageFeatureCollection(
  outages: readonly Incident[],
): OutageFeatureCollection {
  return {
    type: 'FeatureCollection',
    features: outages.map((incident) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [incident.lon, incident.lat] },
      properties: {
        code: incident.code,
        provider: providerOf(incident) ?? 'unknown',
        affected_clients: incident.outage?.affected_clients ?? 0,
        is_closed: isClosed(incident.status),
      },
    })),
  }
}

/**
 * Propiedades mínimas de un corte de agua en el lienzo.
 *
 * `water_id` y no `code` ni `public_id`: el clic distingue qué tocó por el
 * nombre de la propiedad, y un nombre propio no puede confundirse con un
 * incidente. `emergencia` es 1 o 0 para ordenar el dibujo (encima, los de
 * emergencia); un programado o sin dato va debajo.
 */
interface WaterCutFeatureProps {
  water_id: string
  emergencia: 0 | 1
}

export type WaterCutFeatureCollection = FeatureCollection<Point, WaterCutFeatureProps>

/** Sólo los que tienen punto: los demás cuentan en el panel pero no se dibujan. */
export function toWaterCutFeatureCollection(
  cuts: readonly WaterCut[],
): WaterCutFeatureCollection {
  const features: WaterCutFeatureCollection['features'] = []
  for (const cut of cuts) {
    if (!cut.coordinates) continue
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: cut.coordinates },
      properties: { water_id: cut.id, emergencia: cut.programado === false ? 1 : 0 },
    })
  }
  return { type: 'FeatureCollection', features }
}

/**
 * Áreas afectadas de los cortes de luz y de agua, en una sola fuente.
 *
 * `area_id` es el mismo identificador que usa cada capa para seleccionar: el
 * `code` del incidente para la luz y `water:<id>` para el agua (los dos
 * espacios no se pisan). `kind` decide el color: la empresa eléctrica o el
 * agua. Se revalida cada geometría: un área rota se omite y el corte queda
 * como pin.
 */
interface ServiceAreaFeatureProps {
  area_id: string
  kind: 'chilquinta' | 'cge' | 'unknown' | 'water'
  is_closed: boolean
}

export type ServiceAreaFeatureCollection = FeatureCollection<AreaGeometry, ServiceAreaFeatureProps>

export function waterAreaId(id: string): string {
  return `water:${id}`
}

export function toServiceAreaFeatureCollection(
  outages: readonly Incident[],
  waterCuts: readonly WaterCut[],
): ServiceAreaFeatureCollection {
  const features: ServiceAreaFeatureCollection['features'] = []
  for (const incident of outages) {
    const area = asAreaGeometry(incident.outage?.area)
    if (!area) continue
    features.push({
      type: 'Feature',
      geometry: area,
      properties: {
        area_id: incident.code,
        kind: providerOf(incident) ?? 'unknown',
        is_closed: isClosed(incident.status),
      },
    })
  }
  for (const cut of waterCuts) {
    if (!cut.area) continue
    features.push({
      type: 'Feature',
      geometry: cut.area,
      properties: { area_id: waterAreaId(cut.id), kind: 'water', is_closed: false },
    })
  }
  return { type: 'FeatureCollection', features }
}

/**
 * Caja de cada área (con su pin), con la misma clave que `area_id`: el `code`
 * del incidente o `water:<id>`. La usa la cámara al abrir un corte.
 */
export function serviceAreaBounds(
  outages: readonly Incident[],
  waterCuts: readonly WaterCut[],
): Map<string, AreaBounds> {
  const index = new Map<string, AreaBounds>()
  for (const incident of outages) {
    const bounds = areaBounds(asAreaGeometry(incident.outage?.area), [incident.lon, incident.lat])
    if (bounds) index.set(incident.code, bounds)
  }
  for (const cut of waterCuts) {
    const bounds = areaBounds(cut.area, cut.coordinates)
    if (bounds) index.set(waterAreaId(cut.id), bounds)
  }
  return index
}
