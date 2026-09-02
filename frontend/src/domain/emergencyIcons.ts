/**
 * Diccionario visual de las emergencias.
 *
 * # Los tipos son los del backend, no los del enunciado
 *
 * No existe un tipo `fire` ni `earthquake` en `IncidentType`: el fuego llega
 * como `wildfire`, `structural_fire` o `possible_fire`, y los sismos ni siquiera
 * viven en esa fuente —vienen de `/events/seismic`, con su propio esquema—. El
 * `match` de MapLibre compara valores literales, así que un tipo inventado no
 * falla: simplemente cae en el respaldo y todos los puntos comparten icono.
 * De ahí que este mapa se declare contra los enum reales.
 *
 * # Los trazos
 *
 * Geometría al estilo de `lucide-react`: lienzo de 24, trazo de 2, extremos y
 * uniones redondeados. Se copian los `d` y no se importa la librería porque
 * sólo hacen falta seis glifos y lo que se necesita es el camino, no un
 * componente de React: estos paths se rasterizan a un canvas para construir el
 * campo de distancia, nunca se montan en el DOM.
 */

/** Identificadores registrados en el estilo con `map.addImage`. */
export const ICON_IDS = [
  'av-flame',
  'av-waves',
  'av-barrier',
  'av-crash',
  'av-alert',
  'av-rescue',
  'av-flood',
] as const
export type IconId = (typeof ICON_IDS)[number]

export interface IconGlyph {
  /** Sub-trazos del glifo, en el lienzo de 24×24 de lucide. */
  paths: readonly string[]
  /** Qué representa. Alimenta la leyenda. */
  label: string
}

export const ICON_GLYPHS: Record<IconId, IconGlyph> = {
  // lucide `flame`
  'av-flame': {
    label: 'Incendio',
    paths: [
      'M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z',
    ],
  },
  // lucide `radio-tower`, reducido a las ondas concéntricas
  'av-waves': {
    label: 'Sismo',
    paths: [
      'M4.9 16.1C1 12.2 1 5.8 4.9 1.9',
      'M7.8 4.7a6.14 6.14 0 0 0-.8 7.5',
      'M16.2 4.7a6.14 6.14 0 0 1 .8 7.5',
      'M19.1 1.9a9.96 9.96 0 0 1 0 14.2',
      'M12 9v13',
      'M12 6.5m-1.5 0a1.5 1.5 0 1 0 3 0a1.5 1.5 0 1 0-3 0',
    ],
  },
  // lucide `construction`, la barrera de obra
  'av-barrier': {
    label: 'Corte de ruta',
    paths: [
      'M2 6h20v6H2z',
      'M17 12v10',
      'M7 12v10',
      'M2 18h20',
      'M6 6 2 12',
      'M12 6l-4 6',
      'M18 6l-4 6',
    ],
  },
  // lucide `car-crash`, simplificado al vehículo con impacto
  'av-crash': {
    label: 'Accidente vial',
    paths: [
      'M14 8h2.5a2 2 0 0 1 1.8 1.1l1.4 2.8a2 2 0 0 1 .3 1V17a1 1 0 0 1-1 1h-1',
      'M9 18H6a1 1 0 0 1-1-1v-4.1a2 2 0 0 1 .3-1l1.4-2.8A2 2 0 0 1 8.5 8H10',
      'M5 15h15',
      'M8 18v2',
      'M18 18v2',
      'M12 2l-1.5 4L14 5l-2 5',
    ],
  },
  // lucide `triangle-alert`
  'av-alert': {
    label: 'Contingencia',
    paths: [
      'm21.7 18-8-14a2 2 0 0 0-3.4 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3',
      'M12 9v4',
      'M12 17h.01',
    ],
  },
  // lucide `life-buoy`
  'av-rescue': {
    label: 'Rescate',
    paths: [
      'M12 2a10 10 0 1 0 0 20a10 10 0 1 0 0-20',
      'M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8',
      'm4.9 4.9 4.2 4.2',
      'm14.9 14.9 4.2 4.2',
      'm14.9 9.1 4.2-4.2',
      'm4.9 19.1 4.2-4.2',
    ],
  },
  // lucide `waves`, para inundación y aluvión
  'av-flood': {
    label: 'Inundación',
    paths: [
      'M2 6c.6.5 1.2 1 2.5 1C7 7 7 5 9.5 5c2.6 0 2.4 2 5 2 1.3 0 1.9-.5 2.5-1',
      'M2 12c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 1.3 0 1.9-.5 2.5-1',
      'M2 18c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 1.3 0 1.9-.5 2.5-1',
    ],
  },
}

/**
 * Tipo de incidente → icono.
 *
 * Los tres tipos de fuego comparten glifo a propósito. La diferencia entre un
 * incendio forestal, uno estructural y un «posible incendio» ya está codificada
 * en el color por tramo de confianza y en la ficha; repetirla en la silueta
 * daría tres llamas casi iguales que nadie distinguiría a 12 px.
 */
export const INCIDENT_TYPE_ICON: Record<string, IconId> = {
  possible_fire: 'av-flame',
  wildfire: 'av-flame',
  structural_fire: 'av-flame',
  accident: 'av-crash',
  rescue: 'av-rescue',
  flood: 'av-flood',
  landslide: 'av-flood',
  // `power_outage` no aparece: los cortes de luz se dibujan como marcadores del
  // DOM con su propio pin, fuera del lienzo. Ver `OutagePinLayer`.
  other: 'av-alert',
}

/** Respaldo cuando el tipo no está en el diccionario. */
export const FALLBACK_ICON: IconId = 'av-alert'

/** Icono de las otras dos fuentes, que tienen un único tipo cada una. */
export const SEISMIC_ICON: IconId = 'av-waves'
export const CLOSURE_ICON: IconId = 'av-barrier'
