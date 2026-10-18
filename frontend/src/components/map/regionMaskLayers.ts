/**
 * Máscara de la región: todo lo que queda fuera se tapa casi entero, y el
 * borde se marca con una línea fina.
 *
 * AlertaV es de la Región de Valparaíso: lo de afuera (Illapel, San Fernando,
 * Mendoza) no tiene datos y sus rótulos sólo distraen. La máscara deja ver la
 * forma de la costa y la cordillera, apenas, para orientarse; los nombres de
 * afuera ya no se leen.
 *
 * # Dónde va en la pila
 *
 * Se monta justo después del cono de viento, sin `beforeId`: queda **encima**
 * de todo lo que se ancla a `wind-cone-fill` (amenaza sísmica, lluvia, zonas
 * de la DMC, cuarteles, cortes de ruta, tránsito) y del alcance de los sismos,
 * así que esos campos terminan en el borde de la región; y **debajo** de los
 * marcadores (sismos, áreas de corte, cortes, incidentes), así que un sismo en
 * el mar se sigue viendo. react-map-gl vuelve a agregar las capas en orden de
 * montaje cuando el tema reconstruye el estilo, y el orden se mantiene.
 */

import type { LayerProps } from 'react-map-gl/maplibre'
import type { Theme } from '@/hooks/useTheme'

export const REGION_MASK_SOURCE_ID = 'region-mask'
/** El borde va en su fuente: el agujero de la máscara incluye el mar. */
export const REGION_EDGE_SOURCE_ID = 'region-edge'
const REGION_MASK_FILL_ID = 'region-mask-fill'
const REGION_MASK_LINE_ID = 'region-mask-line'

/**
 * Color de la máscara: un tono más hundido que el fondo del mapa base (Positron
 * `#fafaf8`, Dark Matter `#0e0e0e`), para que la región se lea como lo
 * iluminado.
 */
const MASK_COLOR: Record<Theme, string> = { light: '#e6e8e5', dark: '#000000' }
/** Casi opaca: se intuye la costa, no se leen los rótulos. */
export const MASK_OPACITY: Record<Theme, number> = { light: 0.94, dark: 0.95 }
const EDGE_COLOR: Record<Theme, string> = { light: '#64748b', dark: '#94a3b8' }

export function regionMaskLayers(theme: Theme): { fill: LayerProps; line: LayerProps } {
  return {
    fill: {
      id: REGION_MASK_FILL_ID,
      type: 'fill',
      source: REGION_MASK_SOURCE_ID,
      paint: {
        'fill-color': MASK_COLOR[theme],
        'fill-opacity': MASK_OPACITY[theme],
        // Sin antialias el borde del agujero queda dentado al alejarse.
        'fill-antialias': true,
      },
    },
    line: {
      id: REGION_MASK_LINE_ID,
      type: 'line',
      source: REGION_EDGE_SOURCE_ID,
      layout: { 'line-join': 'round' },
      paint: {
        'line-color': EDGE_COLOR[theme],
        'line-opacity': 0.75,
        'line-width': ['interpolate', ['linear'], ['zoom'], 7, 1, 12, 1.5, 16, 2],
      },
    },
  }
}
