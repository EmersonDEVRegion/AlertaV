/**
 * Viñeta de la región: lo de afuera se apaga de a poco, y el borde se marca con
 * una línea tenue.
 *
 * AlertaV es de la Región de Valparaíso: lo lejano (Illapel, San Fernando,
 * Mendoza) no tiene datos y sus rótulos sólo distraen. Una máscara pareja lo
 * resolvía, pero el salto de golpe en el borde se leía como la «zona por
 * desbloquear» de un videojuego. La viñeta es transparente en el borde, deja
 * lo vecino (Los Vilos, Tiltil, Colina, la costa) como contexto apagado y
 * llega al tope a ~40 km.
 *
 * # Cómo se arma el degradado
 *
 * `REGION_VIGNETTE` son rectángulos apilados, cada uno con la región engordada
 * `km` kilómetros como agujero. Un punto a 20 km del borde queda bajo todos los
 * de agujero más chico. Cada capa pinta `α = 1 − (1 − T·f) / (1 − T·fp)`: así
 * el apilado de todas las que lo cubren da exactamente `T·f`, la oscuridad de
 * su tramo (`T`, el tope del tema). Sin antialias: dos capas que comparten
 * borde lo pintarían dos veces y dejarían un hilo más claro.
 *
 * # Dónde va en la pila
 *
 * Se monta justo después del cono de viento, sin `beforeId`: queda **encima**
 * de todo lo que se ancla a `wind-cone-fill` (amenaza sísmica, lluvia, zonas
 * de la DMC, cuarteles, cortes de ruta, tránsito) y del alcance de los sismos,
 * que así se apagan con lo demás; y **debajo** de los marcadores (sismos, áreas
 * de corte, cortes, incidentes), así que un sismo en el mar se sigue viendo.
 * react-map-gl vuelve a agregar las capas en orden de montaje cuando el tema
 * reconstruye el estilo, y el orden se mantiene.
 */

import type { ExpressionSpecification } from 'maplibre-gl'
import type { LayerProps } from 'react-map-gl/maplibre'
import type { Theme } from '@/hooks/useTheme'

export const REGION_MASK_SOURCE_ID = 'region-mask'
/** El borde va en su fuente: es el contorno, no un agujero de la viñeta. */
export const REGION_EDGE_SOURCE_ID = 'region-edge'
const REGION_MASK_FILL_ID = 'region-mask-fill'
const REGION_MASK_LINE_ID = 'region-mask-line'

/**
 * Color de la viñeta: un tono más hundido que el fondo del mapa base (Positron
 * `#fafaf8`, Dark Matter `#0e0e0e`), para que la región se lea como lo
 * iluminado.
 */
const VIGNETTE_COLOR: Record<Theme, string> = { light: '#d9ddd8', dark: '#000000' }
/**
 * Tope de la viñeta, a ~40 km. Alto a propósito: lo que molestaba de la máscara
 * pareja era el salto en el borde, no lo tapado. Con 40 km de rampa el borde ya
 * no se nota, y lo lejano (Illapel, San Fernando) apenas se adivina. En oscuro
 * hace falta más: los rótulos claros sobre fondo negro se leen aun muy
 * atenuados.
 */
export const VIGNETTE_TOP: Record<Theme, number> = { light: 0.9, dark: 0.93 }
const EDGE_COLOR: Record<Theme, string> = { light: '#64748b', dark: '#94a3b8' }
/** Tenue: la viñeta ya dice dónde termina la región; la línea sólo lo precisa. */
const EDGE_OPACITY = 0.4

/** Lo que pinta cada capa para que el apilado dé `top · f` (ver arriba). */
export function vignetteOpacity(top: number): ExpressionSpecification {
  return [
    '-',
    1,
    [
      '/',
      ['-', 1, ['*', top, ['get', 'f']]],
      ['-', 1, ['*', top, ['get', 'fp']]],
    ],
  ]
}

export function regionMaskLayers(theme: Theme): { fill: LayerProps; line: LayerProps } {
  return {
    fill: {
      id: REGION_MASK_FILL_ID,
      type: 'fill',
      source: REGION_MASK_SOURCE_ID,
      paint: {
        'fill-color': VIGNETTE_COLOR[theme],
        'fill-opacity': vignetteOpacity(VIGNETTE_TOP[theme]),
        'fill-antialias': false,
      },
    },
    line: {
      id: REGION_MASK_LINE_ID,
      type: 'line',
      source: REGION_EDGE_SOURCE_ID,
      layout: { 'line-join': 'round' },
      paint: {
        'line-color': EDGE_COLOR[theme],
        'line-opacity': EDGE_OPACITY,
        'line-width': ['interpolate', ['linear'], ['zoom'], 7, 0.75, 12, 1, 16, 1.5],
      },
    },
  }
}
