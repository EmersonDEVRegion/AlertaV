import type { OutageProvider } from '@/api/types'
import { LAYER_LABEL } from './families'
import { FALLBACK_ICON, OUTAGE_ICON, SEISMIC_ICON, WATER_ICON, type IconId } from './emergencyIcons'
import { OTHER_LEVEL } from './otherSymbology'
import { PROVIDER } from './powerSymbology'
import { MAGNITUDE } from './seismicSymbology'
import { LEVEL } from './symbology'
import { TRAFFIC_LEVEL } from './trafficSymbology'
import { WATER } from './waterSymbology'

/**
 * Las capas de emergencia que se encienden y apagan en la pestaña «Capas».
 *
 * Cada una tiene un glifo y un color, y son los mismos del mapa: el azulejo de
 * la pestaña tiene que verse como lo que enciende. Por eso se declaran acá, una
 * sola vez, y no en el componente.
 */

export interface LayerVisibility {
  fire: boolean
  traffic: boolean
  power: boolean
  /** Cortes de agua de Esval. No es una familia de incidentes: no pasa por el motor. */
  water: boolean
  otros: boolean
  seismic: boolean
}

export type EmergencyLayerKey = keyof LayerVisibility

/**
 * Todo encendido, siempre al abrir la app.
 *
 * No se recuerda entre visitas a propósito: quien abre AlertaV porque huele a
 * humo no puede encontrarse con los incendios apagados desde hace una semana y
 * leer el mapa vacío como calma.
 */
export const DEFAULT_LAYER_VISIBILITY: LayerVisibility = {
  fire: true,
  traffic: true,
  power: true,
  water: true,
  otros: true,
  seismic: true,
}

/**
 * Subfiltro de los cortes de luz por empresa. Es un filtro DENTRO de una capa,
 * no una capa: apagar las dos empresas no es lo mismo que apagar los cortes.
 */
export type ProviderVisibility = Record<OutageProvider, boolean>

export const DEFAULT_PROVIDER_VISIBILITY: ProviderVisibility = {
  chilquinta: true,
  cge: true,
}

export interface EmergencyLayer {
  key: EmergencyLayerKey
  label: string
  icon: IconId
  /** Color de la capa en el mapa. Tiñe el azulejo encendido. */
  color: string
}

/** El orden del mosaico: de lo más urgente a lo más frecuente. */
export const EMERGENCY_LAYERS: readonly EmergencyLayer[] = [
  { key: 'fire', label: LAYER_LABEL.fire, icon: 'av-flame', color: LEVEL.confirmed.color },
  { key: 'traffic', label: LAYER_LABEL.traffic, icon: 'av-crash', color: TRAFFIC_LEVEL.confirmed.color },
  { key: 'power', label: LAYER_LABEL.power, icon: OUTAGE_ICON, color: PROVIDER.chilquinta.color },
  { key: 'water', label: WATER.label, icon: WATER_ICON, color: WATER.color },
  { key: 'otros', label: LAYER_LABEL.otros, icon: FALLBACK_ICON, color: OTHER_LEVEL.confirmed.color },
  { key: 'seismic', label: 'Sismos', icon: SEISMIC_ICON, color: MAGNITUDE.moderado.color },
]

/**
 * Cuántas cosas está ocultando el usuario: capas apagadas, empresas apagadas y
 * el filtro de verificados. La pestaña lo avisa aunque esté en otra, porque un
 * filtro olvidado es la forma más fácil de leer mal el mapa.
 */
export function hiddenFilterCount(
  visibility: LayerVisibility,
  providers: ProviderVisibility,
  confirmedOnly: boolean,
): number {
  const layers = Object.values(visibility).filter((on) => !on).length
  const companies = visibility.power ? Object.values(providers).filter((on) => !on).length : 0
  return layers + companies + (confirmedOnly ? 1 : 0)
}
