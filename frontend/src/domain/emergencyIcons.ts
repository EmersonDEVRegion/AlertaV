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
 * # Material Symbols Rounded, relleno (§L, 30-09-2026)
 *
 * Los glifos salen de **Material Symbols Rounded, variante rellena, peso 600**
 * (`@material-symbols/svg-600`, versión 0.47.5), © Google LLC, bajo licencia
 * **Apache-2.0** — el texto está en `frontend/licenses/material-symbols.txt`.
 * Antes eran siluetas dibujadas a mano para este mapa; se veían bien a 40 px
 * pero no del todo a 14, sobre todo el auto. Material se diseñó para leerse a
 * 20–24 px con relleno, que es justo el rango de un símbolo de mapa.
 *
 * Los `paths` están **convertidos** del lienzo original (`0 -960 960 960`) al de
 * 24 que asume el rasterizador (`lib/iconRaster.ts`): `translate(0 960)` y
 * `scale(0.025)`, en coordenadas absolutas y redondeadas a dos decimales. Para
 * cambiar uno: tomar `rounded/<nombre>-fill.svg` del mismo paquete y aplicar la
 * misma transformación (con `svgpath`: `.translate(0, 960).scale(0.025).abs()
 * .round(2)`). Se dibujan con `evenodd`, y los de Material se ven igual así.
 *
 * Reglas del set, que siguen valiendo al elegir un glifo nuevo:
 *
 * * **Relleno, nunca contorno.** Con `icon-size` entre 0.3 y 0.6 sobre un lienzo
 *   de 64, el glifo mide de 14 a 29 px: un trazo de 2 unidades sobre 24 queda
 *   en poco más de un píxel y el detalle interior se cierra.
 * * **Cada silueta distinta de las demás EN NEGRO**, sin color que ayude: en el
 *   mapa el color codifica el nivel de confianza, no el tipo.
 * * **Una sola familia.** Mezclar sets se nota aunque cada ícono sea bueno.
 * * **Nada de emojis**: cada sistema los dibuja distinto, traen color propio (se
 *   perdería el código de confianza) y MapLibre no los rasteriza como SDF.
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
  'av-bolt',
  'av-drop',
] as const
export type IconId = (typeof ICON_IDS)[number]

export interface IconGlyph {
  /** Sub-trazos del glifo, en un lienzo de 24×24 (ver la conversión arriba). */
  paths: readonly string[]
  /** Qué representa. Alimenta la leyenda. */
  label: string
}

export const ICON_GLYPHS: Record<IconId, IconGlyph> = {
  /*
   * Llama con núcleo recortado (`local_fire_department`). Los tres tipos de
   * fuego la comparten; ver `INCIDENT_TYPE_ICON`.
   */
  'av-flame': {
    label: 'Incendio',
    paths: [
      'M3.79 14Q3.79 11.12 5.58 8.36T10.5 3.63Q11.05 3.27 11.63 3.6 12.21 3.94 12.21 4.64V6.29Q12.21 7.06 12.74 7.57 13.26 8.09 14.03 8.09 14.43 8.09 14.78 7.92 15.14 7.74 15.41 7.39 15.64 7.12 15.95 7.02 16.26 6.92 16.55 7.1 18.25 8.3 19.23 10.12 20.21 11.93 20.21 13.99 20.21 16.41 18.99 18.35 17.76 20.29 15.75 21.3 16.37 20.63 16.7 19.77 17.03 18.91 17.03 17.99 17.03 16.97 16.66 16.08 16.29 15.2 15.58 14.48L12 11 8.47 14.48Q7.72 15.2 7.35 16.09 6.98 16.98 6.98 17.99 6.98 18.91 7.31 19.77 7.63 20.63 8.25 21.3 6.24 20.29 5.01 18.35 3.79 16.41 3.79 14ZM12 13.78L14.16 15.9Q14.58 16.32 14.81 16.86 15.04 17.39 15.04 17.98 15.04 19.22 14.15 20.09 13.26 20.97 12 20.97 10.73 20.97 9.85 20.09 8.96 19.22 8.96 17.98 8.96 17.39 9.19 16.85 9.41 16.32 9.86 15.9L12 13.78Z',
    ],
  },
  /*
   * Sismograma (`earthquake`). El id sigue siendo `av-waves` porque lo usan
   * el estilo y los tests; lo que cambió es el dibujo.
   */
  'av-waves': {
    label: 'Sismo',
    paths: [
      'M8.93 22.35Q8.61 22.35 8.36 22.16 8.11 21.97 8.01 21.66L5.47 12.99H2.64Q2.22 12.99 1.93 12.7T1.65 12Q1.65 11.58 1.93 11.29 2.22 11.01 2.64 11.01H6.19Q6.53 11.01 6.78 11.2 7.04 11.39 7.14 11.7L8.75 17.16 11.95 2.42Q12.01 2.07 12.28 1.85 12.54 1.63 12.87 1.63 13.21 1.63 13.47 1.84 13.73 2.05 13.8 2.39L15.98 12.27 17.51 7.41Q17.61 7.1 17.86 6.91 18.11 6.72 18.45 6.72 18.75 6.72 19 6.88 19.25 7.05 19.36 7.35L20.69 11.01H21.38Q21.79 11.01 22.08 11.3 22.37 11.58 22.37 12 22.37 12.42 22.08 12.71 21.79 12.99 21.38 12.99H20.08Q19.77 12.99 19.52 12.81 19.26 12.63 19.16 12.33L18.55 10.63 16.81 16.57Q16.71 16.9 16.46 17.1 16.2 17.29 15.88 17.27 15.53 17.26 15.26 17.06 14.99 16.86 14.92 16.52L12.89 7.48 9.82 21.61Q9.74 21.94 9.5 22.14 9.26 22.35 8.93 22.35Z',
    ],
  },
  /*
   * Señal de paso prohibido (`do_not_disturb_on`): el disco con la barra es
   * la señal vial de «no entrar», y a 14 px se lee mejor que una barrera de obra.
   */
  'av-barrier': {
    label: 'Corte de ruta',
    paths: [
      'M7.99 12.92H16.01Q16.43 12.92 16.71 12.63T17 11.92Q17 11.5 16.71 11.22 16.43 10.93 16.01 10.93H7.99Q7.57 10.93 7.29 11.22T7 11.93Q7 12.35 7.29 12.63 7.57 12.92 7.99 12.92ZM12.01 22.35Q9.85 22.35 7.97 21.54 6.09 20.73 4.68 19.32 3.27 17.91 2.46 16.03 1.65 14.15 1.65 11.99T2.46 7.95Q3.27 6.06 4.68 4.66T7.97 2.45Q9.85 1.63 12.01 1.63 14.17 1.63 16.05 2.45 17.94 3.26 19.34 4.66 20.74 6.06 21.55 7.95 22.37 9.84 22.37 11.99 22.37 14.15 21.55 16.03T19.34 19.32Q17.94 20.72 16.05 21.54 14.16 22.35 12.01 22.35Z',
    ],
  },
  /*
   * Auto con el signo de alerta (`car_crash`). Reemplaza al vehículo dibujado
   * a mano, que a 14 px se leía como un borrón (§L).
   */
  'av-crash': {
    label: 'Accidente vial',
    paths: [
      'M18.04 6.88Q18.19 6.72 18.19 6.48V3.99Q18.19 3.75 18.03 3.6T17.64 3.46Q17.4 3.46 17.25 3.62 17.1 3.78 17.1 4.02V6.51Q17.1 6.75 17.26 6.89 17.42 7.03 17.66 7.03 17.9 7.03 18.04 6.88ZM17.66 9.42Q17.93 9.42 18.13 9.25 18.32 9.08 18.32 8.77 18.32 8.48 18.12 8.3T17.67 8.11Q17.35 8.14 17.18 8.32 17 8.51 17 8.79 17 9.08 17.18 9.25 17.36 9.42 17.66 9.42ZM15.86 17.08Q16.43 17.08 16.82 16.69 17.22 16.29 17.22 15.73 17.22 15.15 16.82 14.74 16.42 14.33 15.87 14.33 15.27 14.33 14.87 14.74 14.47 15.14 14.47 15.72 14.47 16.3 14.87 16.69 15.27 17.08 15.86 17.08ZM6.21 17.08Q6.78 17.08 7.18 16.69 7.57 16.29 7.57 15.73 7.57 15.15 7.17 14.74 6.77 14.33 6.22 14.33 5.62 14.33 5.22 14.74 4.82 15.14 4.82 15.72 4.82 16.3 5.22 16.69 5.62 17.08 6.21 17.08ZM17.6 11.18Q15.6 11.18 14.21 9.79 12.82 8.4 12.82 6.4 12.82 4.43 14.22 3.03 15.62 1.63 17.59 1.63 19.58 1.63 20.97 3.03 22.37 4.42 22.37 6.41 22.37 8.41 20.97 9.79 19.58 11.18 17.6 11.18ZM2.84 22.35Q2.33 22.35 1.99 22 1.65 21.66 1.65 21.16V13.41Q1.65 13.26 1.67 13.1 1.69 12.95 1.75 12.78L3.79 6.64Q3.94 6.17 4.33 5.9 4.72 5.63 5.2 5.63H10.17Q10.59 5.63 10.88 5.93 11.16 6.22 11.16 6.64T10.88 7.34Q10.59 7.63 10.17 7.63H5.55L4.23 11.65H13.46Q13.6 11.65 13.76 11.7T14.06 11.84Q14.9 12.33 15.82 12.56 16.74 12.79 17.71 12.79 18.09 12.79 18.46 12.77 18.84 12.75 19.2 12.65 19.65 12.56 20.01 12.81 20.37 13.06 20.37 13.49V21.17Q20.37 21.66 20.01 22.01 19.65 22.35 19.16 22.35 18.67 22.35 18.32 22 17.97 21.66 17.97 21.16V20.25H4.03V21.17Q4.03 21.66 3.68 22.01 3.34 22.35 2.84 22.35Z',
    ],
  },
  /*
   * Triángulo de aviso (`warning`). El respaldo de los tipos sin ícono propio.
   */
  'av-alert': {
    label: 'Contingencia',
    paths: [
      'M2.41 21.18Q2.12 21.18 1.9 21.04 1.68 20.9 1.55 20.68 1.42 20.46 1.41 20.21T1.55 19.68L11.14 3.13Q11.29 2.87 11.52 2.75 11.74 2.64 12 2.64 12.26 2.64 12.48 2.75 12.71 2.87 12.86 3.13L22.45 19.68Q22.6 19.95 22.59 20.21 22.58 20.46 22.45 20.68 22.32 20.9 22.1 21.04 21.88 21.18 21.59 21.18H2.41ZM12.67 17.81Q12.91 17.58 12.91 17.24 12.91 16.9 12.67 16.67 12.44 16.45 12.1 16.45T11.53 16.67Q11.29 16.9 11.29 17.24 11.29 17.58 11.53 17.81 11.76 18.05 12.1 18.05T12.67 17.81ZM12.64 15.09Q12.85 14.88 12.85 14.55V10.56Q12.85 10.24 12.64 10.03T12.1 9.81Q11.78 9.81 11.56 10.03T11.35 10.56V14.55Q11.35 14.88 11.56 15.09T12.1 15.3Q12.43 15.3 12.64 15.09Z',
    ],
  },
  /*
   * Salvavidas (`support`), el mismo concepto que el dibujo anterior.
   */
  'av-rescue': {
    label: 'Rescate',
    paths: [
      'M7.98 21.54Q6.09 20.72 4.68 19.32 3.28 17.92 2.46 16.03T1.65 12Q1.65 9.85 2.46 7.97 3.28 6.08 4.68 4.68 6.09 3.28 7.98 2.46 9.87 1.63 12.02 1.63T16.05 2.46Q17.93 3.28 19.33 4.68 20.74 6.08 21.55 7.96 22.37 9.85 22.37 12 22.37 14.15 21.55 16.03 20.74 17.92 19.33 19.32 17.93 20.73 16.04 21.54 14.16 22.35 12.01 22.35 9.86 22.35 7.98 21.54ZM9.03 19.89L10.49 16.36Q9.53 16.04 8.77 15.3 8.01 14.57 7.64 13.53L4.1 14.92Q4.87 16.67 6.13 17.95 7.4 19.24 9.03 19.89ZM7.61 10.48Q7.98 9.43 8.74 8.69 9.49 7.96 10.47 7.62L9.05 4.1Q7.19 4.87 5.92 6.17 4.65 7.47 4.1 9.11L7.61 10.48ZM13.88 13.87Q14.65 13.11 14.65 12T13.88 10.12Q13.1 9.35 12 9.35T10.13 10.12Q9.37 10.9 9.37 12T10.13 13.87Q10.89 14.63 12 14.63T13.88 13.87ZM14.97 19.89Q16.68 19.2 17.95 17.92 19.22 16.64 19.9 14.95L16.36 13.53Q15.99 14.61 15.24 15.33 14.48 16.05 13.51 16.36L14.97 19.89ZM16.36 10.47L19.9 9.03Q19.21 7.34 17.93 6.07 16.64 4.8 14.97 4.1L13.56 7.62Q14.54 7.94 15.25 8.68 15.97 9.41 16.36 10.47Z',
    ],
  },
  /*
   * Casa con agua (`flood`). También lo usa `landslide`.
   */
  'av-flood': {
    label: 'Inundación',
    paths: [
      'M8.6 17.41Q7.35 17.41 6.68 16.91 6.01 16.41 5.2 16.41 4.56 16.41 4.01 16.74 3.45 17.08 2.76 17.29 2.4 17.38 2.12 17.13 1.84 16.88 1.84 16.5 1.84 16.12 2.1 15.82 2.35 15.52 2.73 15.38 3.25 15.17 3.75 14.88 4.25 14.59 5.19 14.59 5.4 14.59 5.64 14.63 5.88 14.66 6.06 14.72L4.93 10.6 4.04 11.71Q3.8 12 3.43 12.03 3.07 12.07 2.76 11.82 2.47 11.58 2.41 11.21 2.35 10.83 2.61 10.52L8.85 2.92Q9.24 2.43 9.86 2.27 10.48 2.1 11.08 2.33L20.27 5.79Q20.64 5.93 20.78 6.28 20.92 6.62 20.78 6.99 20.64 7.34 20.3 7.5 19.95 7.66 19.61 7.52L18.29 7.04 20.36 14.72Q20.57 14.84 20.82 15.02 21.07 15.19 21.3 15.32 21.66 15.51 21.91 15.8 22.16 16.1 22.16 16.5T21.86 17.13Q21.56 17.37 21.21 17.25 20.54 17 19.98 16.58 19.42 16.16 18.8 16.16 17.99 16.16 17.32 16.78 16.65 17.41 15.4 17.41T13.48 16.91Q12.81 16.41 12 16.41 11.19 16.41 10.52 16.91 9.85 17.41 8.6 17.41ZM10.11 20Q10.75 19.5 12 19.5T13.9 20Q14.55 20.5 15.4 20.5 16.28 20.5 16.91 19.88T18.8 19.25Q19.74 19.25 20.26 19.62 20.77 19.99 21.3 20.26 21.65 20.43 21.9 20.73 22.16 21.04 22.16 21.42 22.16 21.82 21.88 22.05 21.61 22.29 21.26 22.17 20.56 21.93 20 21.5 19.43 21.07 18.8 21.07 17.99 21.07 17.32 21.7 16.65 22.32 15.4 22.32 14.16 22.32 13.49 21.82 12.81 21.32 12 21.32 11.19 21.32 10.52 21.82 9.85 22.32 8.6 22.32T6.68 21.82Q6.01 21.32 5.2 21.32 4.58 21.32 4.01 21.65T2.78 22.2Q2.42 22.3 2.13 22.06 1.84 21.82 1.84 21.42 1.84 21.02 2.11 20.72T2.75 20.27Q3.27 20.05 3.77 19.77 4.28 19.5 5.2 19.5 6.45 19.5 7.09 20T8.6 20.5Q9.48 20.5 10.11 20ZM15.24 15.59L13.89 10.5Q13.83 10.3 13.65 10.2 13.47 10.09 13.27 10.14L10.86 10.78Q10.66 10.83 10.55 11.02 10.44 11.2 10.49 11.4L11.36 14.62Q11.5 14.57 11.65 14.58 11.81 14.59 12 14.59 13.21 14.59 13.75 15.02T15.24 15.59Z',
    ],
  },
  /*
   * Rayo (`bolt`).
   */
  'av-bolt': {
    label: 'Corte de luz',
    paths: [
      'M8.69 15.4H5.37Q4.76 15.4 4.48 14.87 4.2 14.34 4.56 13.84L13.54 0.88Q13.77 0.56 14.11 0.45 14.46 0.34 14.8 0.48 15.14 0.61 15.34 0.93 15.55 1.26 15.5 1.62L14.49 9.59H18.65Q19.28 9.59 19.55 10.16 19.81 10.72 19.43 11.21L9.6 23.03Q9.37 23.32 9.02 23.4 8.66 23.48 8.34 23.34 8.01 23.19 7.84 22.88 7.66 22.57 7.71 22.19L8.69 15.4Z',
    ],
  },
  /*
   * Gota (`water_drop`).
   */
  'av-drop': {
    label: 'Corte de agua',
    paths: [
      'M6.03 19.9Q3.65 17.46 3.65 13.8 3.65 12.34 4.27 10.86 4.88 9.38 5.92 7.92 6.95 6.46 8.27 5.06 9.6 3.67 11.01 2.4 11.24 2.2 11.49 2.12 11.73 2.04 12 2.04T12.51 2.12Q12.76 2.2 12.99 2.4 14.4 3.67 15.73 5.06 17.05 6.46 18.08 7.92 19.12 9.38 19.74 10.86 20.37 12.34 20.37 13.8 20.37 17.46 17.97 19.9 15.58 22.35 12 22.35 8.42 22.35 6.03 19.9ZM12 18.74Q12.45 18.74 12.7 18.57 12.96 18.4 12.96 18.09 12.96 17.78 12.71 17.59 12.46 17.41 11.98 17.41 10.86 17.41 9.91 16.76 8.97 16.11 8.68 14.6 8.63 14.37 8.43 14.19 8.24 14.02 7.98 14.02 7.63 14.02 7.46 14.26 7.29 14.5 7.34 14.77 7.72 16.88 9.12 17.81T12 18.74Z',
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
  // `power_outage` no aparece: los cortes de luz tienen su propia fuente, con
  // agrupación y color por empresa. Ver `components/map/outageLayers.ts`.
  other: 'av-alert',
}

/** Respaldo cuando el tipo no está en el diccionario. */
export const FALLBACK_ICON: IconId = 'av-alert'

/** Icono de las otras dos fuentes, que tienen un único tipo cada una. */
export const SEISMIC_ICON: IconId = 'av-waves'
export const CLOSURE_ICON: IconId = 'av-barrier'
export const OUTAGE_ICON: IconId = 'av-bolt'
export const WATER_ICON: IconId = 'av-drop'

/** Glifo de un incidente fuera del mapa (listas, leyenda). */
export function iconFor(type: string): IconId {
  if (type === 'power_outage') return OUTAGE_ICON
  return INCIDENT_TYPE_ICON[type] ?? FALLBACK_ICON
}
