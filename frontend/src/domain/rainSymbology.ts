/**
 * Simbología de la capa de lluvia.
 *
 * # La regla que ordena todo el archivo
 *
 * Es la única capa que habla del **futuro**: un modelo anuncia lluvia para las
 * próximas 24 h. Todas las demás informan de algo ya ocurrido. Y `riesgo_inundacion`
 * **no es una inundación**: es un pronóstico que cruza un umbral configurable
 * por `.env`, no una alerta declarada por SENAPRED.
 *
 * # Un campo, no manchas (30-sep-2026)
 *
 * Hasta el #16 la capa eran 36 círculos celestes, uno por comuna, y un mapa de
 * calor que sólo aparecía muy cerca. Ahora es un **campo continuo**: la grilla
 * de `/events/weather/grid` (~255 puntos del modelo, también sobre el mar)
 * pintada con interpolación bilineal (`lib/rainRaster.ts`). Cada color es un
 * valor de mm/h, así que la escala del widget puede llevar números.
 *
 * # La paleta: la de Windy, con dos cuidados
 *
 * Azul → cian → verde → amarillo → naranja → rojo es lo que la gente ya lee
 * como «cuánto llueve» (Windy, el radar de la DMC). El naranja y el rojo, en
 * este mapa, también son de incendios; para que no se confundan:
 *
 *   1. la capa va **debajo** de todo, semitransparente y sin bordes;
 *   2. el rojo aparece recién sobre ~10 mm/h, que en la región es excepcional.
 *
 * El dominio es `mm_hora_max`: el máximo de las próximas 24 h.
 */

/** Un escalón de la escala: desde `mm` mm/h, este color (RGBA, alfa 0-1). */
interface RainStop {
  mm: number
  rgba: readonly [number, number, number, number]
}

/**
 * La escala, de menos a más. Bajo el primer escalón no se pinta nada: menos de
 * 0,2 mm/h es llovizna que no moja el suelo, y pintarla cubriría media región
 * de azul cualquier día nublado.
 */
export const RAIN_SCALE: readonly RainStop[] = [
  { mm: 0.2, rgba: [59, 130, 246, 0] },
  { mm: 0.5, rgba: [59, 130, 246, 0.55] },
  { mm: 1, rgba: [34, 211, 238, 0.65] },
  { mm: 2, rgba: [74, 222, 128, 0.7] },
  { mm: 4, rgba: [250, 204, 21, 0.75] },
  { mm: 7, rgba: [249, 115, 22, 0.8] },
  { mm: 10, rgba: [220, 38, 38, 0.85] },
  { mm: 20, rgba: [190, 24, 93, 0.9] },
]

/** Las marcas que se escriben bajo la tira de la escala. */
export const RAIN_SCALE_TICKS: readonly number[] = [0.5, 2, 4, 10]

/** Opacidad de la capa entera, por tema: sobre el mapa claro satura antes. */
export const RAIN_FIELD_OPACITY: Record<'light' | 'dark', number> = {
  light: 0.7,
  dark: 0.78,
}

type Rgba = readonly [number, number, number, number]

const lerp = (a: Rgba, b: Rgba, t: number): Rgba => [
  a[0] + (b[0] - a[0]) * t,
  a[1] + (b[1] - a[1]) * t,
  a[2] + (b[2] - a[2]) * t,
  a[3] + (b[3] - a[3]) * t,
]

/** Color de la escala para un valor, interpolado entre escalones. */
export function rainColorAt(mm: number | null): Rgba {
  const first = RAIN_SCALE[0]!
  if (mm === null || !Number.isFinite(mm) || mm < first.mm) return [0, 0, 0, 0]
  for (let i = 1; i < RAIN_SCALE.length; i += 1) {
    const upper = RAIN_SCALE[i]!
    if (mm <= upper.mm) {
      const lower = RAIN_SCALE[i - 1]!
      return lerp(lower.rgba, upper.rgba, (mm - lower.mm) / (upper.mm - lower.mm))
    }
  }
  return RAIN_SCALE[RAIN_SCALE.length - 1]!.rgba
}

/** La tira de la escala como `linear-gradient` de CSS, para el widget. */
export function rainScaleGradient(): string {
  const last = RAIN_SCALE[RAIN_SCALE.length - 1]!.mm
  const first = RAIN_SCALE[1]!.mm
  // Escala logarítmica: con una lineal, 0,5–4 mm/h (casi toda la lluvia de la
  // región) ocuparía un quinto de la tira.
  const at = (mm: number) =>
    Math.round(((Math.log(mm) - Math.log(first)) / (Math.log(last) - Math.log(first))) * 100)
  return `linear-gradient(to right, ${RAIN_SCALE.slice(1)
    .map(({ mm, rgba: [r, g, b] }) => `rgb(${r} ${g} ${b}) ${at(mm)}%`)
    .join(', ')})`
}

/** Posición de una marca en la tira, en %. La misma escala que el degradado. */
export function rainScalePosition(mm: number): number {
  const last = RAIN_SCALE[RAIN_SCALE.length - 1]!.mm
  const first = RAIN_SCALE[1]!.mm
  return ((Math.log(mm) - Math.log(first)) / (Math.log(last) - Math.log(first))) * 100
}

/**
 * # Revelado por zoom del bloque de pronóstico
 *
 * `MIN` es un corte duro y `FADE` la transición. Los dos hacen falta y hacen
 * cosas distintas:
 *
 *   - `minzoom` saca la capa del **cálculo de colisiones**, no sólo del dibujo.
 *     MapLibre recorre las capas de símbolo en cada frame para decidir qué
 *     etiqueta cabe; una capa con `text-opacity: 0` sigue reservando su espacio
 *     y desplazaría los nombres del basemap sin que se vea nada. Por eso el
 *     corte y no sólo la opacidad.
 *   - La interpolación evita el parpadeo: sin ella el bloque aparecería de
 *     golpe al cruzar el umbral.
 *
 * El umbral está en 10,5 porque a escala regional (z7–z9) treinta y seis
 * bloques de tres líneas serían ilegibles y taparían la región entera; el dato
 * a esa distancia es la mancha. A z11 la pantalla cubre una comuna o dos y el
 * texto pasa a ser lo que se quiere leer.
 */
export const RAIN_TEXT_MIN_ZOOM = 10.5
export const RAIN_TEXT_FADE: readonly [number, number] = [10.6, 11.4]

/** Tamaño del texto, en píxeles: `[zoom, tamaño]`. */
export const RAIN_TEXT_SIZE: readonly (readonly [number, number])[] = [
  [11, 11],
  [14, 13.5],
]

export interface RainTextStyle {
  /** Texto sobre comuna con lluvia. */
  color: string
  /** Texto sobre comuna con `riesgo_inundacion`. */
  colorRisk: string
  /**
   * Halo. Es lo único que garantiza la legibilidad.
   *
   * El texto se lee encima de tres discos translúcidos apilados: el fondo real
   * bajo cada letra depende de la intensidad de esa comuna y del basemap que
   * haya debajo, así que **no hay un color de fondo contra el cual elegir el
   * contraste**. El halo lo fabrica: opuesto al texto y opaco, convierte
   * cualquier fondo en uno conocido en el radio de un par de píxeles.
   */
  halo: string
  /**
   * Grosor en píxeles.
   *
   * MapLibre satura el halo a 1/4 del tamaño de fuente —es el margen del atlas
   * SDF—, así que a 11 px el techo real está en ~2,75. Pedir 4 no da más halo,
   * da el mismo halo y una expectativa equivocada.
   */
  haloWidth: number
  haloBlur: number
}

export const RAIN_TEXT: Record<'light' | 'dark', RainTextStyle> = {
  light: {
    color: '#0c4a6e',
    colorRisk: '#082f49',
    halo: '#ffffff',
    haloWidth: 1.8,
    haloBlur: 0.3,
  },
  dark: {
    // Casi blanco con una pizca de cian: pertenece a la capa sin competir con
    // el rojo y el ámbar de las emergencias.
    color: '#e0f2fe',
    colorRisk: '#ffffff',
    // Más oscuro que Dark Matter a propósito: bajo el núcleo de una comuna en
    // riesgo el fondo ya no es negro, es cian claro.
    halo: '#020e16',
    haloWidth: 1.6,
    haloBlur: 0.5,
  },
}

