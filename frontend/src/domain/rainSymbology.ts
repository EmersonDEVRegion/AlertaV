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
  /** Lluvia sin riesgo. Halo y cuerpo. */
  rain: string
  /** Lluvia con `riesgo_inundacion === true`. Halo y cuerpo. */
  risk: string
  /**
   * Disco interior, sin riesgo. Un escalón MÁS CALIENTE que `rain`.
   *
   * Es lo que separa esto de una mancha azul plana. Un radar real no pinta la
   * celda de un color: el centro corre hacia el extremo caliente de la escala.
   * Como `circle` no tiene degradados, el salto de matiz entre `rain` y
   * `nucleus` —dos pasos de la misma rampa cian, no dos colores distintos— hace
   * ese trabajo con una propiedad estática que no cuesta nada.
   */
  nucleus: string
  /** Disco interior con riesgo. El punto más caliente de la rampa. */
  nucleusRisk: string
  /** Trazo del anillo de riesgo. */
  ring: string
  /** Halo exterior: la mancha difusa que da la sensación de nube. */
  haloOpacity: number
  coreOpacity: number
  coreOpacityRisk: number
  /** Disco interior. Tercer escalón del degradado simulado. */
  nucleusOpacity: number
  nucleusOpacityRisk: number
  /**
   * Extremos del pulso del anillo, `[mínimo, máximo]`.
   *
   * El máximo es además el valor en reposo: con `prefers-reduced-motion`, con la
   * capa apagada o sin ninguna comuna en riesgo, el anillo se queda ahí. Nunca
   * en el mínimo: si la animación no corre, el anillo tiene que verse igual.
   */
  ringOpacity: readonly [number, number]
}

/**
 * # La rampa de radar
 *
 * Una sola familia —sky/cyan— recorrida en cuatro pasos, de frío a caliente:
 * halo y cuerpo en el paso frío, núcleo un paso más arriba, y el riesgo desplaza
 * los dos pares hacia el extremo caliente. Es la gramática de un radar
 * meteorológico moderno traducida a lo que `circle` sabe hacer.
 *
 * **Los hexadecimales subieron de luminosidad y las opacidades bajaron.** No es
 * casualidad ni son dos cambios: es el mismo cambio. Sobre Dark Matter un azul
 * profundo (`#2563eb`) a 0,2 de opacidad componía a un rgb(7,20,47) que se
 * confundía con el propio fondo — la mancha se veía sucia, no luminosa. Un cian
 * claro a 0,18 compone a un teal apagado y legible: el color llega del matiz,
 * no de la densidad, y por eso la capa puede seguir siendo translúcida sin
 * desaparecer. Bajar la opacidad al subir el brillo es lo que impide que el
 * conjunto sature la vista.
 */
export const RAIN_PALETTE: Record<'light' | 'dark', RainPalette> = {
  light: {
    // Sobre Positron (casi blanco): más riesgo = más oscuro. La rampa se
    // recorre al revés, pero es la misma familia cian.
    rain: '#38bdf8',
    nucleus: '#0ea5e9',
    risk: '#0369a1',
    nucleusRisk: '#0c4a6e',
    ring: '#0c4a6e',
    haloOpacity: 0.1,
    coreOpacity: 0.18,
    coreOpacityRisk: 0.26,
    nucleusOpacity: 0.22,
    nucleusOpacityRisk: 0.34,
    ringOpacity: [0.3, 0.8],
  },
  dark: {
    // Sobre Dark Matter (casi negro): más riesgo = más brillante.
    rain: '#38bdf8',
    nucleus: '#7dd3fc',
    risk: '#67e8f9',
    nucleusRisk: '#cffafe',
    ring: '#a5f3fc',
    haloOpacity: 0.12,
    coreOpacity: 0.18,
    coreOpacityRisk: 0.26,
    nucleusOpacity: 0.24,
    nucleusOpacityRisk: 0.36,
    ringOpacity: [0.28, 0.75],
  },
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
