/**
 * Paleta de «Otras emergencias»: familias `hydro` (inundación, derrumbe) y
 * `other` (rescate, sin clasificar).
 *
 * Verde azulado, un tercer territorio que no se confunde ni con el cálido de
 * incendios ni con el frío-azul de tráfico. Existe para que un reporte
 * ciudadano de inundación no tenga que pintarse con la paleta de fuego ni
 * desaparecer del mapa por falta de casilla.
 *
 * Provisional a propósito: cuando `hydro` tenga fuente propia —SENAPRED publica
 * alertas por crecida— merecerá su capa y su paleta, y esto se parte en dos.
 */

import type { ConfidenceLevel } from '@/api/types'
import { mute } from './symbology'

export interface OtherLevelStyle {
  color: string
  label: string
  meaning: string
  range: string
  chip: string
}

export const OTHER_LEVEL: Record<ConfidenceLevel, OtherLevelStyle> = {
  unsafe: {
    color: '#5eead4',
    label: 'Baja confianza',
    range: 'menos de 30 %',
    meaning: 'Reporte aislado sin corroborar.',
    chip: 'bg-teal-300 text-teal-950',
  },
  possible: {
    color: '#0d9488',
    label: 'Posible emergencia',
    range: '30 % a 60 %',
    meaning: 'Hay evidencia, no alcanza para darla por cierta.',
    chip: 'bg-teal-600 text-white',
  },
  confirmed: {
    color: '#115e59',
    label: 'Emergencia confirmada',
    range: 'más de 60 %',
    meaning: 'Evidencia acumulada por sobre el 60 %.',
    chip: 'bg-teal-800 text-white',
  },
}

export const MUTED_OTHER_LEVEL: Record<ConfidenceLevel, string> = {
  unsafe: mute(OTHER_LEVEL.unsafe.color),
  possible: mute(OTHER_LEVEL.possible.color),
  confirmed: mute(OTHER_LEVEL.confirmed.color),
}
