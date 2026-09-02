/**
 * Simbología de la capa de cortes e intervenciones de la vía.
 *
 * # La regla que ordena el archivo: dos fuentes que NO saben lo mismo
 *
 * Esta capa mezcla dos orígenes con forma distinta, y toda la simbología existe
 * para no borrar esa diferencia:
 *
 *   * **MOP / Vialidad** — trae `severidad`, un entero de 0 a 5 que el backend
 *     calculó combinando transitabilidad y gravedad, con la transitabilidad
 *     mandando. Es una escala real, publicada por quien mantiene la ruta.
 *   * **MTT / Transporte Informa** — **no trae severidad ninguna**, y no porque
 *     falte mapearla: el portal no publica ninguna escala. Son desvíos, faenas
 *     y cortes programados.
 *
 * La tentación obvia era darle 0 al MTT y tener una sola rampa de seis pasos.
 * Sería una mentira barata: un aviso sin escala pintado como «el menos grave»
 * afirma algo que nadie midió, y en la dirección peligrosa —hacia «no pasa
 * nada»—. El MTT tiene su propio tono y su propio lugar en la jerarquía.
 *
 * # La jerarquía cromática, y por qué el rojo aparece tan tarde
 *
 *   MTT (sin severidad)   ámbar apagado    aviso operativo, casi siempre programado
 *   MOP 0–2               ámbar → naranja  la ruta se puede transitar
 *   MOP 3                 naranja intenso  tránsito restringido
 *   MOP 4–5               rojo             RUTA CORTADA
 *
 * El salto de color cae exactamente donde cae el salto del dominio: en
 * `severity_rank` del backend, `transito` aporta 0, 2 o 4 y la gravedad suma 1
 * como desempate, así que 4 es el primer valor que significa «no se puede
 * pasar». Que el color salte ahí y no en el punto medio de la escala es lo que
 * hace que el mapa responda la pregunta que se le hace —¿puedo pasar?— sin
 * leer ninguna etiqueta.
 *
 * # Sobre el rojo, que ya lo usan los incendios
 *
 * Es un conflicto real y está resuelto por matiz, no ignorado. Los incendios
 * usan el rojo cálido de `symbology.ts` (`#dc2626`, red-600); acá se usa **rosa
 * profundo** (`#e11d48`, rose-600), el mismo tono que ya identifica la Alerta
 * Roja de SENAPRED en este proyecto. Son distinguibles lado a lado y comparten
 * el significado que importa: máxima urgencia.
 *
 * La otra mitad de la separación no es el color sino la **forma**: los cortes
 * se dibujan como rombos con trazo discontinuo, nunca como los discos de las
 * emergencias. Un corte de ruta no es un siniestro —`road_closure` está fuera
 * de `CORRELATABLE_EVENT_TYPES` y entra con confianza 0,0— y darle la forma de
 * uno lo ascendería de categoría en la única capa donde eso importa: la que
 * mira alguien decidiendo si sale de casa.
 *
 * # Dos rampas, una por tema
 *
 * Igual que en `rainSymbology` y `hazardSymbology`. Sobre Dark Matter el ámbar
 * profundo se hunde en el fondo, así que la rampa oscura sube de luminosidad;
 * sobre Positron pasa lo contrario y el ámbar claro desaparece contra el blanco.
 * Una sola paleta dejaría un extremo invisible en uno de los dos temas.
 */

export type Theme = 'light' | 'dark'

/**
 * Extremos del dominio de `severidad`, tal como lo emite el backend.
 *
 * No es una cota de presentación como la de la lluvia: `severity_rank` no puede
 * devolver nada fuera de este rango por construcción (`transito * 2 + 0|1`, con
 * `transito` en 0..2). Si algún día apareciera un 6, la expresión de MapLibre
 * satura en el extremo alto y el corte se pinta rojo — que es el fallo correcto
 * para un valor desconocido en una escala de gravedad.
 */
export const SEVERITY_MIN = 0
export const SEVERITY_MAX = 5

/**
 * El escalón donde la ruta deja de ser transitable.
 *
 * Sale de `severity_rank`: `transito` aporta 0, 2 o 4 según los tres estados
 * que publica Vialidad, y 4 es el primero que significa «cortada». Vive como
 * constante porque lo usan la rampa de color, el filtro del anillo pulsante y
 * la leyenda, y tenerlo escrito tres veces es cómo se desincronizan.
 */
export const SEVERITY_CUT = 4

export interface RoadClosurePalette {
  /** MTT: aviso sin escala. El tono más apagado de la capa. */
  mtt: string
  /** MOP 0: transitable, daño menor. */
  low: string
  /** MOP 2–3: tránsito restringido. */
  mid: string
  /** MOP 4–5: ruta cortada. */
  high: string
  /** Trazo de todos los rombos. Un solo color: la forma no codifica nada más. */
  stroke: string
  /** Trazo del anillo que rodea a los cortes efectivos (`>= SEVERITY_CUT`). */
  cutRing: string
  fillOpacity: number
  /** El MTT va más translúcido: es contexto operativo, no una vía rota. */
  fillOpacityMtt: number
  strokeOpacity: number
  /**
   * Rango de opacidad del anillo de corte, `[mínimo, máximo]`.
   *
   * **Se usa el máximo y sólo el máximo.** El anillo es estático: la capa de
   * lluvia ya tuvo un pulso por `requestAnimationFrame` y se quitó para no
   * dejar el mapa repintando por un adorno (ver `rainRiskRingLayer`). El par se
   * conserva con la misma forma que el de la lluvia por una razón concreta: si
   * alguien reconsidera la decisión, el extremo bajo ya está calibrado contra
   * este fondo y no hay que volver a elegirlo a ojo.
   */
  ringOpacity: readonly [number, number]
}

export const ROAD_CLOSURE_PALETTE: Record<Theme, RoadClosurePalette> = {
  light: {
    // Sobre Positron (casi blanco): la urgencia se lee como MÁS OSCURO.
    mtt: '#d97706', // amber-600
    low: '#f59e0b', // amber-500
    mid: '#ea580c', // orange-600
    high: '#e11d48', // rose-600 — el rojo de la Alerta Roja, no el del fuego
    stroke: '#7c2d12', // orange-900
    cutRing: '#9f1239', // rose-800
    fillOpacity: 0.85,
    fillOpacityMtt: 0.62,
    strokeOpacity: 0.9,
    ringOpacity: [0.25, 0.75],
  },
  dark: {
    // Sobre Dark Matter (casi negro): la urgencia se lee como MÁS LUMINOSO.
    //
    // Los hexadecimales suben un escalón entero respecto del tema claro. Un
    // ámbar-600 sobre negro compone a un marrón sucio que no se distingue del
    // relieve del mapa base; el ámbar-400 llega como luz. Es el mismo ajuste
    // que documenta `rainSymbology` para el cian.
    mtt: '#b45309', // amber-700 — apagado a propósito: el MTT es el fondo
    low: '#fbbf24', // amber-400
    mid: '#fb923c', // orange-400
    high: '#fb7185', // rose-400
    // El trazo NO es un naranja oscuro como en el tema claro: sobre negro un
    // borde oscuro no delimita nada, sólo engorda la mancha. Un ámbar muy
    // claro y translúcido es lo que dibuja el canto del rombo.
    stroke: '#fed7aa', // orange-200
    cutRing: '#fda4af', // rose-300
    fillOpacity: 0.8,
    fillOpacityMtt: 0.55,
    strokeOpacity: 0.75,
    ringOpacity: [0.22, 0.7],
  },
}

/**
 * Radios por zoom, en píxeles: `[zoom, radio del MTT, radio de un corte 5]`.
 *
 * **El zoom es la RAÍZ de la interpolación y estos son sus topes.** Dentro de
 * cada uno se interpola por severidad. La estructura no es un detalle de
 * estilo: MapLibre sólo acepta `["zoom"]` como entrada de un `interpolate` de
 * nivel superior, y una capa derivada que sumara sobre un radio ya interpolado
 * dejaría el zoom anidado y **tiraría el estilo entero** — no la capa, el
 * estilo. Es el mismo problema ya resuelto en incidentes, sismos y lluvia, y la
 * razón de que acá haya fábricas y no constantes.
 *
 * Deliberadamente más pequeños que los de incidentes: un corte es contexto y no
 * puede competir con el disco de una emergencia que esté a la misma altura.
 */
export const ROAD_CLOSURE_ZOOM_STOPS: readonly (readonly [number, number, number])[] = [
  [7, 3, 5.5],
  [11, 5, 9],
  [14, 7.5, 13],
  [16, 9, 16],
]

/** Cuánto se agranda el anillo de corte respecto del rombo que rodea. */
export const CUT_RING_GROWTH = 1.9

export interface RoadClosureLegendRow {
  color: (theme: Theme) => string
  label: string
  meaning: string
}

/**
 * Las cuatro filas de la leyenda, en el mismo orden que la rampa.
 *
 * Viven acá y no en el componente por el motivo de siempre en este proyecto: un
 * color escrito dos veces se desincroniza. La leyenda pide el color a la misma
 * paleta que pinta el mapa.
 */
export const ROAD_CLOSURE_LEGEND: readonly RoadClosureLegendRow[] = [
  {
    color: (theme) => ROAD_CLOSURE_PALETTE[theme].mtt,
    label: 'Aviso del MTT',
    meaning: 'Desvío, faena o corte programado. El portal no publica gravedad.',
  },
  {
    color: (theme) => ROAD_CLOSURE_PALETTE[theme].low,
    label: 'Transitable',
    meaning: 'Emergencia de Vialidad con la ruta abierta (severidad 0 a 1).',
  },
  {
    color: (theme) => ROAD_CLOSURE_PALETTE[theme].mid,
    label: 'Tránsito restringido',
    meaning: 'Paso parcial o con control (severidad 2 a 3).',
  },
  {
    color: (theme) => ROAD_CLOSURE_PALETTE[theme].high,
    label: 'Ruta cortada',
    meaning: 'No se puede pasar (severidad 4 a 5).',
  },
]

/**
 * Textos de la tarjeta del riel de referencia.
 *
 * Viven acá y no en el componente por lo mismo que la leyenda: son afirmaciones
 * sobre el dato, no decoración, y la que más importa es `caveat`. Un corte no
 * es un siniestro y la interfaz no puede sugerir que lo sea.
 */
export const ROAD_CLOSURE_LEGEND_TEXT = {
  title: 'Cortes de ruta',
  subtitle: 'Vialidad (MOP) y Transporte Informa',
  /** Estado frecuente y correcto: no es un error de carga. */
  empty: 'Sin cortes informados',
  caveat:
    'Intervenciones de la vía, no siniestros. Una emergencia del MOP puede ' +
    'seguir vigente durante semanas.',
  /** Por qué algunos puntos no tienen color de gravedad. */
  noScale: 'El portal del MTT no publica gravedad: esos avisos van en ámbar.',
} as const

/**
 * Etiqueta de un corte concreto. La usan el popup y la lista lateral.
 *
 * `null` y `undefined` NO caen en el tramo bajo: caen en la etiqueta del MTT,
 * que es la que dice la verdad —«sin escala publicada»— en vez de afirmar que
 * el caso es leve.
 */
export function severityLabel(severidad: number | null | undefined): string {
  if (severidad === null || severidad === undefined) return 'Sin gravedad informada'
  if (severidad >= SEVERITY_CUT) return 'Ruta cortada'
  if (severidad >= 2) return 'Tránsito restringido'
  return 'Transitable'
}
