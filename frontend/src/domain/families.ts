/**
 * Familia de fenómeno de un incidente.
 *
 * **La API no manda `family`.** `IncidentRead` expone `type` pero no la familia,
 * así que la tabla se replica acá. Es un espejo literal de `INCIDENT_FAMILY` en
 * `backend/app/models/enums.py`, y hay un chequeo automático que compara ambas:
 * si el backend agrega un `IncidentType` o le cambia la familia a uno existente,
 * la comprobación falla en vez de que el mapa deje de pintar algo en silencio.
 *
 * La alternativa buena sería que el backend expusiera `family` como
 * `computed_field` —una línea en `IncidentRead`— y esta tabla desaparecería.
 * Vale la pena hacerlo cuando se toque el schema.
 *
 * Para qué sirve la familia: decide **con qué paleta** se pinta el incidente y
 * **qué casilla** lo enciende. El backend la usa para otra cosa (impedir que
 * una alerta por crecida se adose a un incendio), pero el corte es el mismo y
 * por eso conviene que no se separen.
 */

import type { IncidentType } from '@/api/types'

export const FAMILIES = ['fire', 'traffic', 'power', 'hydro', 'other'] as const
export type IncidentFamily = (typeof FAMILIES)[number]

/** Espejo de `INCIDENT_FAMILY`. Mantener sincronizado. */
export const INCIDENT_FAMILY: Record<IncidentType, IncidentFamily> = {
  possible_fire: 'fire',
  wildfire: 'fire',
  structural_fire: 'fire',
  // `traffic` es familia propia desde que existen los collectors de accidentes.
  // Antes compartía `other` con rescates y despachos genéricos.
  accident: 'traffic',
  // Familia propia desde que existen los collectors de Chilquinta y CGE. Antes
  // habría caído en `other` y un corte se habría podido fundir con un rescate.
  power_outage: 'power',
  flood: 'hydro',
  landslide: 'hydro',
  rescue: 'other',
  other: 'other',
}

export function familyOf(type: IncidentType): IncidentFamily {
  return INCIDENT_FAMILY[type] ?? 'other'
}

// ---------------------------------------------------------------------------
// Agrupación para el control de capas
// ---------------------------------------------------------------------------

/**
 * Las capas que el panel enciende y apaga. No hay una por familia: `hydro` y
 * `other` comparten casilla porque ninguna tiene todavía una fuente propia y
 * separarlas daría un panel con cuatro casillas casi siempre vacías.
 */
export const INCIDENT_LAYERS = ['fire', 'traffic', 'power', 'otros'] as const
export type IncidentLayerKey = (typeof INCIDENT_LAYERS)[number]

export function layerOf(type: IncidentType): IncidentLayerKey {
  const family = familyOf(type)
  if (family === 'fire') return 'fire'
  if (family === 'traffic') return 'traffic'
  if (family === 'power') return 'power'
  return 'otros'
}

/** Etiquetas del panel y de la leyenda. */
export const LAYER_LABEL: Record<IncidentLayerKey, string> = {
  fire: 'Incendios',
  traffic: 'Accidentes viales',
  power: 'Cortes de suministro',
  otros: 'Otras emergencias',
}

// ---------------------------------------------------------------------------
// Textos que dependen de la familia
// ---------------------------------------------------------------------------

/**
 * Quién verifica en terreno, por familia.
 *
 * Decir «ni CONAF ni Bomberos» sobre un choque en la Ruta 68 es incorrecto:
 * CONAF no atiende accidentes viales. Cada familia tiene sus organismos, y las
 * dos frases se declaran enteras —afirmativa y negativa— en vez de derivar una
 * de la otra con un reemplazo de texto que produce castellano roto.
 */
export interface VerifyingSources {
  /** Para el caso confirmado: «CONAF o Bomberos confirmaron…». */
  affirmative: string
  /** Para el caso sin verificar: «ni CONAF ni Bomberos han reportado…». */
  negative: string
}

export const VERIFYING_SOURCES: Record<IncidentLayerKey, VerifyingSources> = {
  fire: {
    affirmative: 'CONAF o Bomberos confirmaron el hecho en terreno.',
    negative: 'ni CONAF ni Bomberos han reportado haber llegado al lugar',
  },
  traffic: {
    affirmative: 'Carabineros o Bomberos confirmaron el hecho en terreno.',
    negative: 'ni Carabineros ni Bomberos han reportado haber llegado al lugar',
  },
  power: {
    // Un corte lo reporta la propia distribuidora: la verificación en terreno
    // no es la pregunta pertinente, la empresa ya sabe qué circuito tiene caído.
    affirmative: 'Informado por la propia empresa distribuidora.',
    negative: 'la distribuidora aún no confirma el corte',
  },
  otros: {
    affirmative: 'Un organismo oficial confirmó el hecho en terreno.',
    negative: 'ningún organismo ha reportado haber llegado al lugar',
  },
}

/**
 * Número de emergencia por familia.
 *
 * En Chile: 131 SAMU (ambulancia), 132 Bomberos, 133 Carabineros. Mandar a
 * llamar a Bomberos por un choque sin incendio retrasa la respuesta correcta,
 * así que el número sigue al tipo de emergencia y no está escrito a mano en la
 * ficha.
 */
export interface EmergencyContact {
  number: string
  service: string
}

/**
 * `null` cuando no corresponde un número de emergencia.
 *
 * Un corte de suministro no se reporta al 132 ni al 133: llamar a Bomberos por
 * falta de luz ocupa una línea que otro necesita. Se deriva a la distribuidora,
 * y deliberadamente NO se escribe acá un número de atención comercial que no se
 * pudo verificar: un teléfono equivocado en una emergencia es peor que ninguno.
 */
export const EMERGENCY_CONTACT: Record<IncidentLayerKey, EmergencyContact | null> = {
  fire: { number: '132', service: 'Bomberos' },
  traffic: { number: '133', service: 'Carabineros' },
  power: null,
  otros: { number: '133', service: 'Carabineros' },
}

/**
 * Qué NO se puede dar por confirmado, por familia. Reemplaza al texto fijo que
 * hablaba siempre de un incendio.
 */
export const UNCONFIRMED_NOUN: Record<IncidentLayerKey, string> = {
  fire: 'un incendio confirmado',
  traffic: 'un accidente confirmado',
  power: 'un corte confirmado',
  otros: 'una emergencia confirmada',
}
