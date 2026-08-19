/**
 * Paleta de la capa de accidentes viales.
 *
 * Misma **estructura** que la de incendios, distinta **paleta**: los tres
 * tramos son los de `confidence_level` (política v2.0.0, cortes 0.30 y 0.60),
 * porque un accidente se corrobora igual que un incendio —varias fuentes
 * independientes, o una que fue al lugar—. Lo que cambia es el color.
 *
 * Tonos fríos, y por un motivo operativo, no estético: en una emergencia real
 * los dos tipos conviven en pantalla, y un choque en la Ruta 68 no puede
 * competir visualmente con un incendio forestal. La separación cálido/frío deja
 * leer de un vistazo qué clase de emergencia es cada punto, antes de distinguir
 * el tramo de confianza.
 *
 *   unsafe    < 30 %        cian    #22d3ee
 *   possible  30 % – 60 %   índigo  #4338ca
 *   confirmed > 60 %        violeta #6b21a8
 *
 * Se conservan del módulo de incendios las dos cosas que no son color:
 *
 *   - la proporción de tamaños (`unsafe` 0.58× → `confirmed` 1.0×);
 *   - el aro blanco exterior y el punto central hueco de los `confirmed` que
 *     ninguna fuente verificó en terreno.
 *
 * Las etiquetas SÍ cambian, y es el punto: el backend declara
 * `LEVEL_STYLES[confirmed].label = "Incendio confirmado"`, que es correcto para
 * fuego y falso para un choque. Mientras el backend no tenga etiquetas por
 * familia, las de tráfico se definen acá.
 */

import type { ConfidenceLevel } from '@/api/types'
import { mute } from './symbology'

export interface TrafficLevelStyle {
  color: string
  label: string
  meaning: string
  range: string
  chip: string
}

export const TRAFFIC_LEVEL: Record<ConfidenceLevel, TrafficLevelStyle> = {
  unsafe: {
    color: '#22d3ee',
    label: 'Baja confianza',
    range: 'menos de 30 %',
    meaning: 'Reporte aislado sin corroborar. Puede ser ruido o un evento ya resuelto.',
    chip: 'bg-cyan-400 text-cyan-950',
  },
  possible: {
    color: '#4338ca',
    label: 'Posible accidente',
    range: '30 % a 60 %',
    meaning: 'Hay evidencia de un evento vial, no alcanza para darlo por cierto.',
    chip: 'bg-indigo-700 text-white',
  },
  confirmed: {
    color: '#6b21a8',
    label: 'Accidente vehicular',
    range: 'más de 60 %',
    meaning: 'Evidencia acumulada por sobre el 60 %.',
    chip: 'bg-purple-800 text-white',
  },
}

/** Mismo orden que la leyenda de incendios: de menor a mayor evidencia. */
export const TRAFFIC_LEVEL_ORDER: readonly ConfidenceLevel[] = [
  'unsafe',
  'possible',
  'confirmed',
]

/**
 * Versión apagada para los incidentes cerrados. Se deriva con la misma función
 * que la paleta de incendios: si un color base cambia, el atenuado lo sigue solo.
 */
export const MUTED_TRAFFIC_LEVEL: Record<ConfidenceLevel, string> = {
  unsafe: mute(TRAFFIC_LEVEL.unsafe.color),
  possible: mute(TRAFFIC_LEVEL.possible.color),
  confirmed: mute(TRAFFIC_LEVEL.confirmed.color),
}
