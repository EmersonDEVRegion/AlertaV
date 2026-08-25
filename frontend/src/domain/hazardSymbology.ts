/**
 * Simbología de la capa de amenaza sísmica.
 *
 * # Qué representa, y por qué no puede parecerse a una emergencia
 *
 * Es un modelo **probabilístico y estático** del CSN: dice cuánto puede llegar
 * a acelerarse el suelo en cada celda, no que algo esté ocurriendo. Comparte
 * pantalla con incendios activos y cortes en curso, así que la regla que ordena
 * todo este archivo es que **no debe leerse como un evento**.
 *
 * De ahí salen las tres decisiones:
 *
 *   1. **Rampa continua, no bandas.** Las otras capas usan `match` sobre
 *      categorías discretas porque codifican estados. Ésta interpola sobre un
 *      número real. Un degradado suave se lee como "campo de fondo"; escalones
 *      duros se leerían como zonas declaradas por alguien.
 *   2. **Fuera de la familia cálida.** Rojo, naranja y amarillo ya están
 *      tomados por incendios y sismos reales. Esta capa usa violeta, que no
 *      colisiona con ninguna paleta de emergencia.
 *   3. **Opacidad baja y sin trazo fuerte.** Es contexto bajo los datos, no
 *      encima.
 *
 * # La variable
 *
 * Se pinta `pga_475`: aceleración máxima del suelo en g, con 10 % de
 * probabilidad de excedencia en 50 años (período de retorno ≈ 475 años). Es el
 * nivel del diseño sísmico habitual y el que tiene sentido para público
 * general; `pga_2475` es para estructuras críticas y `sa*` para cálculo
 * estructural. El archivo trae las cinco: cambiar de variable es cambiar el
 * nombre en `HAZARD_VARIABLE`.
 */

/** Propiedad del GeoJSON que decide el color. */
export const HAZARD_VARIABLE = 'pga_475'

/**
 * Extremos de la rampa, en g.
 *
 * Son una rampa continua y no cortes de clasificación **a propósito**: el
 * archivo se genera con un script y su distribución real puede cambiar entre
 * versiones del modelo. Una interpolación degrada con elegancia si los valores
 * caen fuera del rango —satura en un extremo— mientras que unos cortes fijos
 * mal calibrados producirían un mapa de un solo color sin avisar.
 */
export const HAZARD_MIN_G = 0.15
export const HAZARD_MAX_G = 0.6

export interface HazardRamp {
  /** Paradas [valor en g, color]. De menor a mayor amenaza. */
  stops: readonly (readonly [number, string])[]
  /** Color del borde de celda. Muy sutil: la grilla no es el dato. */
  line: string
  fillOpacity: number
  lineOpacity: number
}

/**
 * Dos rampas, una por tema.
 *
 * No es la misma paleta con otra opacidad. Sobre un mapa **oscuro** el ojo lee
 * intensidad como "más luz", así que la rampa va de violeta apagado a violeta
 * brillante. Sobre un mapa **claro** la lectura se invierte: más amenaza es más
 * oscuro. Usar una sola rampa dejaría la zona de mayor amenaza casi invisible
 * en uno de los dos temas.
 */
export const HAZARD_RAMP: Record<'light' | 'dark', HazardRamp> = {
  light: {
    stops: [
      [HAZARD_MIN_G, '#ede9fe'],
      [0.3, '#c4b5fd'],
      [0.45, '#8b5cf6'],
      [HAZARD_MAX_G, '#5b21b6'],
    ],
    line: '#6d28d9',
    fillOpacity: 0.35,
    lineOpacity: 0.18,
  },
  dark: {
    stops: [
      [HAZARD_MIN_G, '#3b1d6e'],
      [0.3, '#6d28d9'],
      [0.45, '#a78bfa'],
      [HAZARD_MAX_G, '#ddd6fe'],
    ],
    line: '#a78bfa',
    fillOpacity: 0.28,
    lineOpacity: 0.14,
  },
}

/** Etiquetas de la leyenda: los extremos, en el lenguaje de la variable. */
export const HAZARD_LEGEND = {
  title: 'Amenaza sísmica (PGA)',
  subtitle: '10 % de excedencia en 50 años',
  low: `${HAZARD_MIN_G} g`,
  high: `${HAZARD_MAX_G} g`,
  caveat:
    'Modelo probabilístico del CSN. Describe la amenaza esperada del terreno, no un evento en curso.',
} as const
