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
 * De ahí salen las tres decisiones visuales:
 *
 *   1. **Mancha difusa, nunca un pin.** `circle-blur` alto y sin trazo en las
 *      dos capas de fondo. Un pin con ícono diría "hay un evento acá", que es
 *      exactamente lo que no hay. Además el dato es de escala comunal —celdas
 *      de 9 a 11 km— y un borde nítido insinuaría una precisión que no tiene.
 *   2. **Azul, fuera de la familia cálida.** Rojo, naranja y amarillo son de
 *      incendios, tráfico y sismos; el violeta ya lo tomó la amenaza sísmica.
 *      El azul translúcido se lee como condición ambiental.
 *   3. **El riesgo cambia el color y suma un anillo, no cambia de forma.** Sigue
 *      siendo la misma mancha: no asciende a la categoría de emergencia sólo
 *      por cruzar un umbral.
 *
 * # Dos rampas, una por tema
 *
 * Igual que en `hazardSymbology`: sobre un mapa **oscuro** el ojo lee intensidad
 * como más luz, así que el riesgo va hacia el azul claro; sobre uno **claro**, la
 * lectura se invierte y el riesgo va hacia el azul profundo. Una sola paleta
 * dejaría el caso más grave casi invisible en uno de los dos temas.
 */

/**
 * Dominio de la intensidad, en mm/h sobre `mm_hora_max`.
 *
 * El mínimo es el piso de emisión del collector: por debajo de 0,2 mm en 24 h no
 * emite evento. El máximo es una cota de presentación, no un umbral: MapLibre
 * satura fuera de rango, así que 30 mm/h se dibuja igual que 12 y nada
 * desaparece del mapa por caer fuera de la rampa.
 *
 * Ojo con el umbral real de riesgo (`OPENMETEO_INTENSITY_MM_H`, 5 mm/h por
 * defecto): queda a mitad de la rampa a propósito, para que una comuna en riesgo
 * ya se vea grande antes de que el anillo lo confirme. Pero **el tamaño no
 * decide el riesgo** — eso lo dice el booleano y sólo el booleano.
 */
export const RAIN_MM_MIN = 0.2
export const RAIN_MM_MAX = 12

/**
 * Radios por zoom, en píxeles: `[zoom, radio con RAIN_MM_MIN, radio con RAIN_MM_MAX]`.
 *
 * El zoom es la RAÍZ de la interpolación y estos son sus topes; dentro de cada
 * uno se interpola por intensidad. La estructura importa: MapLibre sólo acepta
 * `["zoom"]` como entrada de un `interpolate` de nivel superior, y una capa
 * derivada que sumara sobre un radio ya interpolado dejaría el zoom anidado y
 * tiraría el estilo entero. Es el mismo problema que ya se resolvió en las capas
 * de incidentes y de sismos.
 *
 * Por encima de z14 satura, y está bien: a esa distancia una comuna llena la
 * pantalla y una mancha que siguiera creciendo taparía las calles.
 */
export const RAIN_ZOOM_STOPS: readonly (readonly [number, number, number])[] = [
  [7, 6, 15],
  [11, 14, 34],
  [14, 30, 70],
]

export interface RainPalette {
  /** Lluvia sin riesgo. */
  rain: string
  /** Lluvia con `riesgo_inundacion === true`. */
  risk: string
  /** Trazo del anillo de riesgo. Es lo único que pulsa. */
  ring: string
  /** Halo exterior: la mancha difusa que da la sensación de nube. */
  haloOpacity: number
  coreOpacity: number
  coreOpacityRisk: number
  /**
   * Extremos del pulso del anillo, `[mínimo, máximo]`.
   *
   * El máximo es además el valor en reposo: con `prefers-reduced-motion`, con la
   * capa apagada o sin ninguna comuna en riesgo, el anillo se queda ahí. Nunca
   * en el mínimo: si la animación no corre, el anillo tiene que verse igual.
   */
  ringOpacity: readonly [number, number]
}

export const RAIN_PALETTE: Record<'light' | 'dark', RainPalette> = {
  light: {
    // Sobre Positron (casi blanco): más riesgo = más oscuro.
    rain: '#60a5fa',
    risk: '#1d4ed8',
    ring: '#1e3a8a',
    haloOpacity: 0.12,
    coreOpacity: 0.22,
    coreOpacityRisk: 0.34,
    ringOpacity: [0.3, 0.8],
  },
  dark: {
    // Sobre Dark Matter (casi negro): más riesgo = más brillante.
    rain: '#2563eb',
    risk: '#93c5fd',
    ring: '#bfdbfe',
    haloOpacity: 0.16,
    coreOpacity: 0.26,
    coreOpacityRisk: 0.4,
    ringOpacity: [0.28, 0.75],
  },
}

/**
 * Textos del panel.
 *
 * `caveat` no es decoración legal: el hand-off del backend pide explícitamente
 * que la UI diga "riesgo pronosticado" y nunca "inundación", y que quede claro
 * que las alertas oficiales las declara SENAPRED y llegan por otra vía.
 */
export const RAIN_LEGEND = {
  title: 'Lluvia pronosticada',
  subtitle: 'Pronóstico 24 h · Open-Meteo',
  rain: 'Lluvia',
  risk: 'Riesgo de inundación pronosticado',
  /** El estado más frecuente del año. No es un error de carga. */
  empty: 'Sin lluvia pronosticada',
  caveat: 'Pronóstico a escala comunal. No es una alerta oficial: esas las declara SENAPRED.',
} as const
