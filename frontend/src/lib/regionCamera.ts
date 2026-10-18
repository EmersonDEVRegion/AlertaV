/**
 * Encuadre del mapa sobre la región: zoom mínimo, límites y vista inicial.
 *
 * # Por qué se calcula y no es una caja fija
 *
 * MapLibre obliga a que toda la pantalla quede dentro de `maxBounds`. Con una
 * caja fija, un teléfono vertical (proporción ~0,46) y un monitor ancho (~1,8)
 * necesitan cajas opuestas para ver la región entera: la que sirve a uno le
 * muestra al otro Illapel y San Fernando, o le corta la región. Por eso:
 *
 * - **El zoom mínimo** es el que deja la región entera, con un margen chico,
 *   en la parte del mapa que no tapan la barra, la hoja o la columna. Alejarse
 *   más sólo mostraría lo que la máscara tapa.
 * - **Los límites** son esa misma vista más la caja de sismos (`SEISMIC_BBOX`):
 *   desde un sismo en el mar se puede llegar a verlo, pero no hay nada que
 *   recorrer fuera de la región (queda bajo la máscara).
 *
 * Se recalcula al cambiar el tamaño de la ventana (girar el teléfono).
 *
 * Todo en Web Mercator normalizado (0–1), el espacio en que MapLibre mide:
 * un píxel vale `1 / (512 · 2^zoom)`.
 */

import { REGION_BBOX, SEISMIC_BBOX, type Bbox } from '@/domain/region'
import { headerHeight, mapInsets, type Insets } from './cameraOffset'

/** Margen alrededor de la región en el zoom mínimo, en grados (~12 km). */
const REGION_MARGIN_DEG = 0.12
/**
 * Al oeste, más (~30 km): el mapa base escribe el nombre de las ciudades de la
 * costa (Valparaíso, San Antonio) a la izquierda del punto, sobre el mar, y
 * con el margen parejo quedaba cortado contra el borde de la pantalla.
 */
const WEST_MARGIN_DEG = 0.32
/** Tamaño del mundo en píxeles a zoom 0 (teselas de 512 de MapLibre). */
const WORLD_PX = 512

export interface Viewport {
  width: number
  height: number
}

export interface RegionCamera {
  minZoom: number
  maxBounds: [number, number, number, number]
  /** Centro de la vista que muestra la región entera al zoom mínimo. */
  center: { longitude: number; latitude: number }
}

const mercX = (lon: number) => (lon + 180) / 360
const mercY = (lat: number) => {
  const phi = (lat * Math.PI) / 180
  return (1 - Math.log(Math.tan(Math.PI / 4 + phi / 2)) / Math.PI) / 2
}
const lonOf = (x: number) => x * 360 - 180
const latOf = (y: number) =>
  (Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180) / Math.PI

function union(a: Bbox, b: Bbox): [number, number, number, number] {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])]
}

/**
 * Cámara para un lienzo de `viewport` píxeles con `insets` tapados.
 *
 * Si lo tapado deja menos de la mitad del lienzo (una ventana diminuta), se
 * cuenta la mitad: mejor una región un poco tapada que un zoom mínimo absurdo.
 */
export function regionCamera(
  viewport: Viewport,
  insets: Insets,
  region: Bbox = REGION_BBOX,
  reach: Bbox = SEISMIC_BBOX,
): RegionCamera {
  const width = Math.max(viewport.width, 1)
  const height = Math.max(viewport.height, 1)
  const usableW = Math.max(width - insets.left - insets.right, width / 2)
  const usableH = Math.max(height - insets.top - insets.bottom, height / 2)
  const left = Math.min(insets.left, width - usableW)
  const top = Math.min(insets.top, height - usableH)

  const [west, south, east, north] = region
  const x0 = mercX(west - WEST_MARGIN_DEG)
  const x1 = mercX(east + REGION_MARGIN_DEG)
  const y0 = mercY(north + REGION_MARGIN_DEG)
  const y1 = mercY(south - REGION_MARGIN_DEG)

  // Unidades de Mercator por píxel para que la región quepa en lo visible.
  const perPx = Math.max((x1 - x0) / usableW, (y1 - y0) / usableH)
  const minZoom = Math.log2(1 / (WORLD_PX * perPx))

  // La vista al zoom mínimo: la región al centro de lo visible.
  const cx = (x0 + x1) / 2
  const cy = (y0 + y1) / 2
  const viewX0 = cx - perPx * (left + usableW / 2)
  const viewY0 = cy - perPx * (top + usableH / 2)
  const viewX1 = viewX0 + perPx * width
  const viewY1 = viewY0 + perPx * height
  const view: Bbox = [lonOf(viewX0), latOf(viewY1), lonOf(viewX1), latOf(viewY0)]

  return {
    minZoom,
    maxBounds: union(view, reach),
    center: {
      longitude: lonOf((viewX0 + viewX1) / 2),
      latitude: latOf((viewY0 + viewY1) / 2),
    },
  }
}

/**
 * La cámara para el lienzo actual. Antes de que exista el mapa (la vista
 * inicial se decide antes de montarlo), el lienzo es la ventana menos la barra.
 */
export function currentRegionCamera(): RegionCamera {
  if (typeof window === 'undefined') {
    return regionCamera({ width: 430, height: 932 }, { top: 0, right: 0, bottom: 0, left: 0 })
  }
  const canvas = document.querySelector<HTMLElement>('.maplibregl-map')
  const viewport =
    canvas && canvas.clientWidth > 0 && canvas.clientHeight > 0
      ? { width: canvas.clientWidth, height: canvas.clientHeight }
      : { width: window.innerWidth, height: window.innerHeight - headerHeight() }
  return regionCamera(viewport, mapInsets())
}
