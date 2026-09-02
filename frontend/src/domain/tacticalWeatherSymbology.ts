/**
 * Simbología del widget meteorológico táctico.
 *
 * # La regla que ordena todo el archivo
 *
 * **El estado silencioso no puede competir con el mapa.** El widget vive en la
 * barra superior, siempre en pantalla, y el 95 % de los días del año no tiene
 * nada que decir salvo la temperatura. Un widget con color permanente enseña al
 * ojo a ignorarlo, y el día que se ponga rojo nadie lo verá — que es exactamente
 * el fallo que hace inútil a la mayoría de las barras de estado.
 *
 * De ahí las tres decisiones:
 *
 *   1. **En calma, cero cromatismo.** Hereda `currentColor` de la barra, como
 *      las cápsulas de telemetría. No tiene fondo propio, no tiene borde, no
 *      tiene punto de color. Es tipografía sobre el cromo.
 *   2. **En alerta, el color entra de golpe y con fondo.** Ámbar o rojo, no un
 *      matiz intermedio: es la única forma de que el cambio se note por el rabillo
 *      del ojo en una barra que se mira sin mirar.
 *   3. **El número culpable crece.** No es decoración: en alerta, la métrica que
 *      cruzó el umbral pasa a 15 px y el resto se encoge. La jerarquía tipográfica
 *      hace el trabajo que en el mapa hace el tamaño del pin.
 *
 * # Por qué NO se reutiliza la paleta de la lluvia
 *
 * `rainSymbology` es cian porque describe agua sobre un mapa. Acá el color no
 * describe **qué** amenaza es sino **cuán grave** —el qué lo dice el glifo— y
 * mezclar las dos gramáticas daría un widget cian para lluvia y rojo para calor,
 * que se leería como dos componentes distintos según el día.
 *
 * # Ámbar y rojo, y ninguno es el de las emergencias del mapa
 *
 * Deliberadamente un escalón por debajo del `--urgent` de los incidentes
 * confirmados. Esto es un **pronóstico**: aunque esté en crítico, no describe
 * nada que haya ocurrido. Igualarlo cromáticamente a un incendio activo sería
 * decir con el color lo que todos los textos de esta capa se cuidan de no decir.
 */

import type { WeatherHazard, WeatherSeverity } from '@/api/tacticalWeatherTypes'

/* ------------------------------------------------------------------------- */
/* Glifos                                                                     */
/* ------------------------------------------------------------------------- */

/**
 * Trazos de los glifos, en el lienzo de 24×24 de lucide.
 *
 * # Por qué son `d` sueltos y no componentes
 *
 * Misma decisión y mismo formato que `domain/emergencyIcons.ts`: **el camino es
 * el dato, no el componente.** Ahí los `d` se rasterizan a un canvas para
 * construir el campo de distancia con signo que MapLibre necesita
 * (`lib/sdf.ts`); acá se montan en un `<svg>` del DOM. Que la geometría viva en
 * un objeto plano es lo que permite que la misma silueta sirva para las dos
 * cosas — y que el día que la amenaza de calor necesite un pin en el mapa, el
 * termómetro sea literalmente el mismo termómetro y no un primo parecido.
 *
 * Trazo de 2, extremos y uniones redondeados, sin relleno. Heredan
 * `currentColor`, que es lo que permite el estado silencioso.
 */
export interface WeatherGlyph {
  paths: readonly string[]
  /** Qué representa. Alimenta el `aria-label` y el detalle. */
  label: string
}

/** Clave del glifo. `calma` no es una amenaza: es la ausencia de todas. */
export type WeatherGlyphKey = WeatherHazard | 'calma'

export const WEATHER_GLYPHS: Record<WeatherGlyphKey, WeatherGlyph> = {
  // lucide `cloud-sun`: el estado silencioso. Ni sol pleno ni nube de tormenta
  // — el widget en calma no está afirmando que haga buen tiempo, sólo que no
  // hay nada que alertar.
  calma: {
    label: 'Sin alertas meteorológicas',
    paths: [
      'M12 2v2',
      'm4.93 4.93 1.41 1.41',
      'M20 12h2',
      'm19.07 4.93-1.41 1.41',
      'M15.947 12.65a4 4 0 0 0-5.925-4.128',
      'M13 22H7a5 5 0 1 1 4.9-6H13a3 3 0 0 1 0 6Z',
    ],
  },
  // lucide `cloud-rain`: anegamiento urbano. La misma silueta que el icono de
  // la capa de lluvia en el riel de referencia, para que se lean como lo mismo.
  lluvia: {
    label: 'Anegamiento',
    paths: [
      'M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242',
      'M16 14v6',
      'M8 14v6',
      'M12 16v6',
    ],
  },
  // lucide `mountain-snow` sin la nieve: el cerro. Remoción en masa.
  remocion: {
    label: 'Remoción en masa',
    paths: [
      'm8 3 4 8 5-5 5 15H2L8 3z',
      'M4.14 15.08c2.62-1.57 5.24-1.43 7.86.42 2.74 1.94 5.49 2 8.23.19',
    ],
  },
  // lucide `flame`. El MISMO path que `av-flame` en `emergencyIcons`: es la
  // misma familia de incendios, y dos llamas distintas en la misma pantalla
  // serían dos cosas distintas para el ojo.
  incendio: {
    label: 'Propagación de incendios',
    paths: [
      'M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z',
    ],
  },
  // lucide `wind`
  viento: {
    label: 'Viento fuerte',
    paths: [
      'M12.8 19.6A2 2 0 1 0 14 16H2',
      'M17.5 8a2.5 2.5 0 1 1 2 4H2',
      'M9.8 4.4A2 2 0 1 1 11 8H2',
    ],
  },
  // lucide `thermometer-sun`, reducido al termómetro lleno.
  calor: {
    label: 'Calor extremo',
    paths: [
      'M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z',
      'M11.5 15.5v-9',
    ],
  },
  // lucide `sun` con los rayos completos: el índice UV.
  uv: {
    label: 'Índice UV',
    paths: [
      'M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10z',
      'M12 1v2',
      'M12 21v2',
      'M4.22 4.22l1.42 1.42',
      'M18.36 18.36l1.42 1.42',
      'M1 12h2',
      'M21 12h2',
      'M4.22 19.78l1.42-1.42',
      'M18.36 5.64l1.42-1.42',
    ],
  },
}

/* ------------------------------------------------------------------------- */
/* Color                                                                      */
/* ------------------------------------------------------------------------- */

export interface SeverityStyle {
  /** Fondo de la cápsula. `null` en calma: hereda el cromo de la barra. */
  background: string | null
  /** Color del texto y del glifo. */
  ink: string
  /** Borde. Sólo en alerta, y de la misma familia que el fondo. */
  border: string | null
  /** Rótulo corto para el lector de pantalla y el detalle. */
  label: string
}

/**
 * Un solo juego de colores para los dos temas, y esta vez es correcto.
 *
 * `hazardSymbology` y `rainSymbology` tienen una rampa por tema porque se
 * dibujan **sobre el mapa**, cuyo fondo cambia por completo entre Positron y
 * Dark Matter. Este widget vive sobre `--surface-chrome`, que es casi negro en
 * los dos temas por decisión explícita de `AppHeader` — la barra es el cromo de
 * la aplicación, no una superficie de contenido. Duplicar la paleta acá crearía
 * dos verdades para un solo fondo.
 */
export const SEVERITY_STYLE: Record<WeatherSeverity, SeverityStyle> = {
  ninguna: {
    background: null,
    // El mismo blanco al 70 % que usan las etiquetas de la telemetría: el
    // widget pertenece a la barra, no se posa encima de ella.
    ink: 'rgb(255 255 255 / 0.72)',
    border: null,
    label: 'Sin alertas',
  },
  aviso: {
    // Ámbar 500 al 16 %: suficiente para separarse del cromo, lejos del bloque
    // sólido que reserva el rojo.
    background: 'rgb(245 158 11 / 0.16)',
    ink: '#fcd34d',
    border: 'rgb(245 158 11 / 0.35)',
    label: 'Aviso',
  },
  critica: {
    background: 'rgb(239 68 68 / 0.2)',
    ink: '#fca5a5',
    border: 'rgb(239 68 68 / 0.42)',
    label: 'Condición crítica',
  },
}

/* ------------------------------------------------------------------------- */
/* Texto                                                                      */
/* ------------------------------------------------------------------------- */

/**
 * Rótulo de cada amenaza para la interfaz.
 *
 * **Ninguno afirma que algo esté ocurriendo**, y ahí está toda la cautela de
 * esta capa condensada en seis palabras. «Condición de propagación» no dice que
 * haya un incendio, dice que si lo hubiera correría. «Calor extremo» no dice
 * «ola de calor», que es un término con una definición oficial de la DMC
 * —percentil 90 diario durante tres días— que este pronóstico no puede cumplir.
 */
export const HAZARD_LABEL: Record<WeatherHazard, string> = {
  lluvia: 'Riesgo de anegamiento',
  remocion: 'Riesgo de remoción en masa',
  incendio: 'Condición de propagación',
  viento: 'Viento fuerte',
  calor: 'Calor extremo',
  uv: 'Índice UV peligroso',
}

/** Versión corta, para la cápsula cuando el ancho aprieta. */
export const HAZARD_SHORT: Record<WeatherHazard, string> = {
  lluvia: 'Anegamiento',
  remocion: 'Remoción',
  incendio: 'Propagación',
  viento: 'Viento',
  calor: 'Calor',
  uv: 'UV',
}

export const WEATHER_TEXT = {
  title: 'Estado meteorológico',
  /** El estado más frecuente del año. No es un error de carga. */
  calm: 'Sin umbrales cruzados',
  /** `observado_en: null`. Distinto de la calma, y tiene que decirlo. */
  unknown: 'Sin dato meteorológico',
  unknownDetail:
    'No hay ninguna corrida reciente del collector. No significa que esté todo tranquilo: significa que no se sabe.',
  loading: 'Consultando el pronóstico…',
  layerToggle: 'Lluvia en el mapa',
  layerHint: 'Manchas de precipitación pronosticada por comuna',
  /** La aclaración que esta capa arrastra en todos sus textos. */
  caveat:
    'Pronóstico a escala comunal (celdas de 9-11 km). No es una alerta oficial: esas las declara SENAPRED.',
  source: 'Open-Meteo',
} as const

/* ------------------------------------------------------------------------- */
/* Formato de las cifras                                                      */
/* ------------------------------------------------------------------------- */

/**
 * Decimales por métrica.
 *
 * La lluvia se mide con un decimal porque 0,4 y 0,9 mm/h son cosas distintas;
 * la temperatura, el viento y el UV no. «31,7 °C» en una cápsula de 15 px es
 * ruido tipográfico que no cambia ninguna decisión, y además desplaza el ancho
 * del widget cada vez que cambia el decimal — un elemento del cromo que se mueve
 * solo es un elemento que molesta.
 */
const DECIMALES: Record<string, number> = {
  mm_hora_max: 1,
  mm_3h_max: 1,
  mm_total: 1,
}

export function formatMetric(valor: number, metrica: string): string {
  const decimales = DECIMALES[metrica] ?? 0
  return valor.toFixed(decimales)
}

/**
 * `"32"` + `"°C"` por separado, para poder darles tamaños distintos.
 *
 * La unidad va más pequeña que el número, que es la convención de cualquier
 * tablero: lo que se lee de un vistazo es la cifra.
 */
export function splitMetric(
  valor: number,
  unidad: string,
  metrica: string,
): { value: string; unit: string } {
  // El índice UV no tiene unidad física: se antepone la sigla en vez de
  // posponerla, que es como lo publica cualquier pronóstico ("UV 11").
  if (metrica === 'uv_max') return { value: formatMetric(valor, metrica), unit: '' }
  return { value: formatMetric(valor, metrica), unit: unidad }
}
