/**
 * Espejo TypeScript del estado meteorológico táctico.
 *
 * Fuente de verdad: `backend/app/schemas/weather.py` (`TacticalWeatherRead`) y
 * `backend/app/collectors/weather/umbrales.py`, que es donde viven los umbrales.
 *
 * # Qué es esto y qué NO es
 *
 * Es el estado consolidado de las 36 comunas en un objeto: la peor amenaza
 * vigente, la cifra que la disparó y de qué comuna salió, más el ambiente
 * regional para cuando no hay nada que alertar.
 *
 * **No es la capa de lluvia** (`api/rainTypes.ts`), que sigue siendo una
 * colección de puntos por comuna para MapLibre. Y no es `api/weather.ts`, que
 * llama a Open-Meteo directo para el viento del cono de un incendio. Tres datos,
 * tres orígenes, tres cadencias.
 *
 * # Las cuatro cosas del contrato que mandan sobre el diseño del widget
 *
 * 1. **`severidad` no es lo mismo que `riesgo_inundacion`.** Una comuna puede
 *    estar en `critica` por índice UV con 0,0 mm de lluvia. El widget lee
 *    `severidad`; el mapa sigue leyendo el booleano de lluvia.
 *
 * 2. **`temp_c` es una mediana y `temp_max_c` es un máximo.** No son dos formas
 *    de decir lo mismo. La mediana describe el ambiente de la región ahora y es
 *    lo que se muestra en calma; el máximo describe el peor punto de la ventana
 *    y es lo que dispara la alerta de calor. Un día con 38 °C en Petorca y 17 °C
 *    en Valparaíso tiene mediana ~21: mostrar 38 en calma mentiría sobre el
 *    tiempo que hace donde está la gente.
 *
 * 3. **`observado_en: null` no es lo mismo que `severidad: "ninguna"`.** Uno
 *    dice «no sabemos» y el otro «todo tranquilo». Un widget que los pinte igual
 *    mostrará calma cuando en realidad la fuente está caída — que es el modo de
 *    fallo más caro que puede tener una barra de estado.
 *
 * 4. **Sigue siendo un pronóstico.** `es_pronostico` es constante y está por lo
 *    mismo que en la capa de lluvia: ninguna de estas severidades es una alerta
 *    declarada. Las declara SENAPRED y llegan por otra vía.
 */

/**
 * Estado táctico. Tres valores y sólo tres, y de ahí sale el diseño del widget:
 * `ninguna` es gris y silencioso, `aviso` es ámbar, `critica` es rojo.
 */
export type WeatherSeverity = 'ninguna' | 'aviso' | 'critica'

/**
 * La familia de amenaza responsable.
 *
 * `lluvia` y `remocion` son las dos caras del agua y no son sinónimos: la
 * primera es anegamiento urbano por intensidad horaria —el drenaje no da
 * abasto— y la segunda es remoción en masa por saturación del terreno. Dos
 * mecanismos, dos respuestas, dos textos distintos en pantalla.
 */
export type WeatherHazard =
  | 'lluvia'
  | 'remocion'
  | 'incendio'
  | 'viento'
  | 'calor'
  | 'uv'

/**
 * La regla que se cumplió, con la cifra que la cumplió.
 *
 * **Es lo que el widget expande.** El backend manda el número ya resuelto
 * —valor, unidad y contra qué umbral se comparó— justamente para que el
 * navegador no tenga que reimplementar la política para poder explicarla.
 */
export interface WeatherTrigger {
  amenaza: WeatherHazard
  severidad: Exclude<WeatherSeverity, 'ninguna'>
  /**
   * Clave estable de la métrica (`mm_hora_max`, `temp_max_c`, `uv_max`,
   * `rafaga_max_kmh`, `regla_30_30_30`). Sirve para elegir el formato del
   * número, **nunca** para decidir si hay alerta: eso lo dice `severidad`.
   */
  metrica: string
  valor: number
  unidad: string
  umbral: number
  /** Frase ya redactada en español. La escribe el backend. */
  texto: string
  /** ISO-8601 UTC, o `null` si la regla es de acumulado y no de instante. */
  momento: string | null
}

/** Respuesta de `GET /api/v1/events/weather/tactical`. */
export interface TacticalWeather {
  /**
   * Inicio de la ventana del agregado más reciente. **`null` significa que no
   * hay corrida reciente**, no que esté todo tranquilo. Ver la nota 3.
   */
  observado_en: string | null
  inicio: string | null
  fin: string | null

  severidad: WeatherSeverity
  amenaza: WeatherHazard | null
  disparo_principal: WeatherTrigger | null
  comuna_origen: string | null

  /** Mediana regional de la hora en curso. El estado silencioso del widget. */
  temp_c: number | null
  /** Mediana regional del viento MEDIO, no de la ráfaga. Describe la tarde. */
  viento_kmh: number | null

  temp_max_c: number | null
  temp_min_c: number | null
  humedad_min: number | null
  /** Ráfaga máxima. Es la que gobierna las reglas de viento e incendio. */
  rafaga_max_kmh: number | null
  uv_max: number | null

  comunas: number
  con_lluvia: number
  en_aviso: number
  en_critico: number
  comunas_en_alerta: string[]

  modelo: string
  es_pronostico: boolean
}
