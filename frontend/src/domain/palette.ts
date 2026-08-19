/**
 * Resolución de paleta por familia.
 *
 * Tres paletas conviven en el mismo mapa y todas usan los mismos tres tramos de
 * `confidence_level`. Lo que cambia entre ellas es el color y las etiquetas:
 *
 *   fire     cálido  (rojo / amarillo / naranja)   ← la declara el backend
 *   traffic  frío    (cian / índigo / violeta)
 *   otros    teal    (provisional, hasta que `hydro` tenga capa propia)
 *
 * Este módulo es el único lugar que decide cuál aplica. Los componentes piden
 * `styleFor(incident)` y no saben de familias; las capas de MapLibre usan
 * `LEVEL_COLOR_EXPRESSION`, generada desde las mismas tablas. Un color nuevo se
 * agrega en su paleta y aparece en el mapa, la leyenda y la ficha a la vez.
 */

import type { ConfidenceLevel, Incident } from '@/api/types'
import { type IncidentLayerKey, layerOf } from './families'
import { MUTED_OTHER_LEVEL, OTHER_LEVEL } from './otherSymbology'
import { MUTED_TRAFFIC_LEVEL, TRAFFIC_LEVEL } from './trafficSymbology'
import { LEVEL, MUTED_LEVEL, isClosed, levelOf } from './symbology'

export interface ResolvedStyle {
  color: string
  label: string
  meaning: string
  range: string
  chip: string
}

type PaletteTable = Record<ConfidenceLevel, ResolvedStyle>

const PALETTES: Record<IncidentLayerKey, PaletteTable> = {
  fire: LEVEL,
  traffic: TRAFFIC_LEVEL,
  otros: OTHER_LEVEL,
}

const MUTED: Record<IncidentLayerKey, Record<ConfidenceLevel, string>> = {
  fire: MUTED_LEVEL,
  traffic: MUTED_TRAFFIC_LEVEL,
  otros: MUTED_OTHER_LEVEL,
}

/** Paleta de un incidente según su familia. */
export function paletteFor(incident: Incident): PaletteTable {
  return PALETTES[layerOf(incident.type)]
}

/**
 * Estilo final de un incidente: paleta por familia, tramo por confianza y color
 * atenuado si ya cerró. Es lo que consumen la ficha y los chips.
 */
export function styleFor(incident: Incident): ResolvedStyle {
  const layer = layerOf(incident.type)
  const level = levelOf(incident)
  const base = PALETTES[layer][level]
  return isClosed(incident.status)
    ? { ...base, color: MUTED[layer][level] }
    : base
}

// ---------------------------------------------------------------------------
// Expresión de color para MapLibre
// ---------------------------------------------------------------------------

const LAYER_KEYS: readonly IncidentLayerKey[] = ['fire', 'traffic', 'otros']
const LEVEL_KEYS: readonly ConfidenceLevel[] = ['unsafe', 'possible', 'confirmed']

/** `['match', ['get','confidence_level'], 'unsafe', <color>, …]` */
function levelMatch(colors: Record<ConfidenceLevel, string>) {
  return [
    'match',
    ['get', 'confidence_level'],
    ...LEVEL_KEYS.flatMap((level) => [level, colors[level]]),
    // Tramo desconocido → el intermedio, nunca el más alarmante.
    colors.possible,
  ]
}

/** `['match', ['get','layer'], 'fire', <match de tramo>, …]` */
function layerMatch(pick: (layer: IncidentLayerKey) => Record<ConfidenceLevel, string>) {
  return [
    'match',
    ['get', 'layer'],
    ...LAYER_KEYS.flatMap((layer) => [layer, levelMatch(pick(layer))]),
    levelMatch(pick('otros')),
  ]
}

const liveColors = (layer: IncidentLayerKey): Record<ConfidenceLevel, string> => ({
  unsafe: PALETTES[layer].unsafe.color,
  possible: PALETTES[layer].possible.color,
  confirmed: PALETTES[layer].confirmed.color,
})

/**
 * Color del relleno: familia → tramo → atenuado si cerró.
 *
 * Anidada pero generada: ningún hex se escribe dos veces, y agregar una familia
 * es agregar una entrada en `PALETTES`.
 */
export const LEVEL_COLOR_EXPRESSION = [
  'case',
  ['get', 'is_closed'],
  layerMatch((layer) => MUTED[layer]),
  layerMatch(liveColors),
] as const
