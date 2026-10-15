import type { HazardCells, HazardGrid } from '@/api/hazardTypes'
import {
  HAZARD_EDGE_FEATHER_CELLS,
  HAZARD_EDGE_WOBBLE_CELLS,
  HAZARD_MAX_G,
  HAZARD_MIN_G,
  HAZARD_RAMP,
  HAZARD_VARIABLE,
} from '@/domain/hazardSymbology'
import { edgeFade } from './edgeFeather'

/**
 * La amenaza sísmica como imagen: interpolación bilineal entre los nodos del
 * CSN, la rampa violeta en CIELAB y un borde difuminado y ondulado (§I).
 *
 * # Por qué una imagen y no los polígonos
 *
 * Hasta §I la capa eran 1648 rectángulos de ~5 km, cada uno de un solo color.
 * La grilla termina en una caja (−72,0 / −69,8 de longitud, −33,8 / −32,0 de
 * latitud), así que el mapa mostraba un borde recto, y de cerca, escalones de
 * 5 km. Es lo mismo que le pasaba a la lluvia y se resuelve igual (§L): una
 * imagen con el borde que se desvanece. De paso, el teléfono dibuja una imagen
 * en vez de 1648 polígonos.
 *
 * Interpolar entre nodos no inventa nada: cada píxel es el promedio ponderado
 * de los cuatro nodos que lo rodean, y en un nodo vale exactamente su PGA.
 *
 * # La grilla no es del todo regular
 *
 * El CSN la define en una proyección métrica: las filas están a paso fijo en
 * latitud (~0,045°), pero cada fila arranca en una longitud algo distinta y su
 * paso en longitud cambia un poco con la latitud (0,0530° al norte, 0,0541° al
 * sur). Por eso se guarda cada fila con su propio origen y paso, y se interpola
 * dentro de la fila y después entre filas. Tratarla como una grilla regular
 * desplazaría los valores hasta media celda hacia los bordes.
 *
 * # Las filas de píxeles van en Mercator
 *
 * MapLibre estira una fuente `image` entre sus cuatro esquinas en el espacio
 * de Mercator, no en grados. Sobre 1,75° de latitud la diferencia llega a ~1 km
 * a media altura; se corrige calculando la latitud de cada fila de píxeles desde
 * la Y de Mercator.
 */

/** Píxeles por celda en longitud. La resolución del dato es la celda: más no suma. */
const PIXELS_PER_CELL = 8

interface LatticeRow {
  lat: number
  lon0: number
  step: number
  /** Por columna; `NaN` si falta el nodo. */
  values: number[]
}

export interface HazardLattice {
  /** De sur a norte. */
  rows: LatticeRow[]
  lat0: number
  latStep: number
  west: number
  east: number
  south: number
  north: number
}

const median = (xs: number[]): number => {
  const sorted = [...xs].sort((a, b) => a - b)
  return sorted[Math.floor(sorted.length / 2)] ?? 0
}

/** Arma la grilla de nodos desde las celdas. `null` si no alcanza para interpolar. */
export function buildHazardLattice(
  cells: HazardCells,
  variable: string = HAZARD_VARIABLE,
): HazardLattice | null {
  const byLat = new Map<number, { lon: number; value: number }[]>()
  for (const feature of cells.features) {
    const p = feature.properties as unknown as Record<string, unknown> | null
    const lon = p?.['lon']
    const lat = p?.['lat']
    const value = p?.[variable]
    if (typeof lon !== 'number' || typeof lat !== 'number' || typeof value !== 'number') continue
    if (!Number.isFinite(lon) || !Number.isFinite(lat) || !Number.isFinite(value)) continue
    // Las latitudes de una fila coinciden a 5 decimales; 4 absorbe el redondeo.
    const key = Math.round(lat * 1e4) / 1e4
    const row = byLat.get(key)
    if (row) row.push({ lon, value })
    else byLat.set(key, [{ lon, value }])
  }

  const lats = [...byLat.keys()].sort((a, b) => a - b)
  if (lats.length < 2) return null

  const rows: LatticeRow[] = []
  for (const lat of lats) {
    const nodes = byLat.get(lat)!.sort((a, b) => a.lon - b.lon)
    if (nodes.length < 2) return null
    const gaps = nodes.slice(1).map((n, i) => n.lon - nodes[i]!.lon)
    const step = median(gaps)
    if (!(step > 0)) return null
    const lon0 = nodes[0]!.lon
    const count = Math.round((nodes[nodes.length - 1]!.lon - lon0) / step) + 1
    const values = Array.from({ length: count }, () => NaN)
    for (const node of nodes) {
      const i = Math.round((node.lon - lon0) / step)
      if (i >= 0 && i < count) values[i] = node.value
    }
    rows.push({ lat, lon0, step, values })
  }

  const lat0 = lats[0]!
  const latStep = (lats[lats.length - 1]! - lat0) / (lats.length - 1)
  return {
    rows,
    lat0,
    latStep,
    west: Math.min(...rows.map((r) => r.lon0)),
    east: Math.max(...rows.map((r) => r.lon0 + (r.values.length - 1) * r.step)),
    south: lat0,
    north: lats[lats.length - 1]!,
  }
}

/** Valor y distancia al borde (en celdas) a lo largo de una fila. */
function sampleRow(row: LatticeRow, lon: number): { value: number; edge: number } {
  const n = row.values.length
  const c = (lon - row.lon0) / row.step
  const edge = Math.min(c, n - 1 - c)
  const cc = Math.max(0, Math.min(n - 1, c))
  const i0 = Math.min(n - 2, Math.floor(cc))
  const t = cc - i0
  const a = row.values[i0]!
  const b = row.values[i0 + 1]!
  // Un nodo que falta se trata como borde: la imagen se desvanece ahí también.
  if (Number.isNaN(a) && Number.isNaN(b)) return { value: NaN, edge: -1 }
  if (Number.isNaN(a)) return { value: b, edge: Math.min(edge, t - 0.5) }
  if (Number.isNaN(b)) return { value: a, edge: Math.min(edge, 0.5 - t) }
  return { value: a + (b - a) * t, edge }
}

export interface HazardSample {
  /** PGA interpolado, en g. `NaN` fuera de la grilla. */
  value: number
  /** Distancia al borde de la grilla, en celdas. Negativa afuera. */
  edge: number
  /** Posición en la grilla (columna, fila), para la ondulación. */
  gx: number
  gy: number
}

export function sampleHazard(lattice: HazardLattice, lon: number, lat: number): HazardSample {
  const { rows, lat0, latStep } = lattice
  const n = rows.length
  const r = (lat - lat0) / latStep
  const rr = Math.max(0, Math.min(n - 1, r))
  const r0 = Math.min(n - 2, Math.floor(rr))
  const t = rr - r0
  const low = sampleRow(rows[r0]!, lon)
  const high = sampleRow(rows[r0 + 1]!, lon)
  const rowEdge = Math.min(r, n - 1 - r)
  const colEdge = low.edge + (high.edge - low.edge) * t
  let value: number
  if (Number.isNaN(low.value)) value = high.value
  else if (Number.isNaN(high.value)) value = low.value
  else value = low.value + (high.value - low.value) * t
  const gx = (lon - rows[r0]!.lon0) / rows[r0]!.step
  return { value, edge: Math.min(rowEdge, colEdge), gx, gy: r }
}

/* ------------------------------------------------------------------------- */
/* Color: la rampa en CIELAB, como hacía `interpolate-lab` en el relleno       */
/* ------------------------------------------------------------------------- */

type Rgb = [number, number, number]
type Lab = [number, number, number]

const hexToRgb = (hex: string): Rgb => {
  const v = hex.replace('#', '')
  return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16)) as Rgb
}

const toLinear = (c: number) => {
  const x = c / 255
  return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4
}
const fromLinear = (x: number) => {
  const c = x <= 0.0031308 ? 12.92 * x : 1.055 * x ** (1 / 2.4) - 0.055
  return Math.max(0, Math.min(255, Math.round(c * 255)))
}

// Blanco D65, el mismo que usa MapLibre.
const XN = 0.95047
const YN = 1
const ZN = 1.08883
const f = (t: number) => (t > 216 / 24389 ? Math.cbrt(t) : (t * 24389) / 27 / 116 + 16 / 116)
const fInv = (t: number) => (t ** 3 > 216 / 24389 ? t ** 3 : (116 * t - 16) / (24389 / 27))

export function rgbToLab([r, g, b]: Rgb): Lab {
  const [lr, lg, lb] = [toLinear(r), toLinear(g), toLinear(b)]
  const x = (0.4124 * lr + 0.3576 * lg + 0.1805 * lb) / XN
  const y = (0.2126 * lr + 0.7152 * lg + 0.0722 * lb) / YN
  const z = (0.0193 * lr + 0.1192 * lg + 0.9505 * lb) / ZN
  const [fx, fy, fz] = [f(x), f(y), f(z)]
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)]
}

export function labToRgb([l, a, bb]: Lab): Rgb {
  const fy = (l + 16) / 116
  const fx = fy + a / 500
  const fz = fy - bb / 200
  const [x, y, z] = [fInv(fx) * XN, fInv(fy) * YN, fInv(fz) * ZN]
  const lr = 3.2406 * x - 1.5372 * y - 0.4986 * z
  const lg = -0.9689 * x + 1.8758 * y + 0.0415 * z
  const lb = 0.0557 * x - 0.204 * y + 1.057 * z
  return [fromLinear(lr), fromLinear(lg), fromLinear(lb)]
}

/** Color de la rampa para un PGA. Satura fuera de rango, como `interpolate`. */
export function hazardColor(theme: 'light' | 'dark', value: number): Rgb {
  const stops = HAZARD_RAMP[theme].stops
  const first = stops[0]!
  const last = stops[stops.length - 1]!
  if (!(value > first[0])) return hexToRgb(first[1])
  if (value >= last[0]) return hexToRgb(last[1])
  for (let i = 1; i < stops.length; i += 1) {
    const [v1, c1] = stops[i]!
    if (value <= v1) {
      if (value === v1) return hexToRgb(c1)
      const [v0, c0] = stops[i - 1]!
      const t = (value - v0) / (v1 - v0)
      const a = rgbToLab(hexToRgb(c0))
      const b = rgbToLab(hexToRgb(c1))
      return labToRgb([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t])
    }
  }
  return hexToRgb(last[1])
}

/** Tabla de 256 colores sobre [MIN, MAX]: la conversión a Lab por píxel sobra. */
function colorTable(theme: 'light' | 'dark'): Rgb[] {
  return Array.from({ length: 256 }, (_, i) =>
    hazardColor(theme, HAZARD_MIN_G + ((HAZARD_MAX_G - HAZARD_MIN_G) * i) / 255),
  )
}

/* ------------------------------------------------------------------------- */
/* La imagen                                                                  */
/* ------------------------------------------------------------------------- */

const mercY = (lat: number) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360))
const fromMercY = (y: number) => (Math.atan(Math.exp(y)) * 360) / Math.PI - 90

/** Esquinas de la imagen para una fuente `image`: NO, NE, SE, SO. */
type ImageCorners = [[number, number], [number, number], [number, number], [number, number]]

export function hazardCorners(lattice: HazardLattice): ImageCorners {
  const { west, east, south, north } = lattice
  return [
    [west, north],
    [east, north],
    [east, south],
    [west, south],
  ]
}

export function hazardImageSize(lattice: HazardLattice): { width: number; height: number } {
  const step = lattice.rows[0]!.step
  const width = Math.round(((lattice.east - lattice.west) / step) * PIXELS_PER_CELL) + 1
  // Mismo tamaño de píxel en pantalla en las dos direcciones, medido en Mercator.
  const pxPerMerc = width / (((lattice.east - lattice.west) * Math.PI) / 180)
  const height = Math.round((mercY(lattice.north) - mercY(lattice.south)) * pxPerMerc) + 1
  return { width, height }
}

/** Los píxeles RGBA, fila a fila de norte a sur. El alfa es sólo el borde. */
export function hazardPixels(
  lattice: HazardLattice,
  theme: 'light' | 'dark',
  width: number,
  height: number,
  feather: number = HAZARD_EDGE_FEATHER_CELLS,
  wobble: number = HAZARD_EDGE_WOBBLE_CELLS,
): Uint8ClampedArray<ArrayBuffer> {
  const table = colorTable(theme)
  const out = new Uint8ClampedArray(new ArrayBuffer(width * height * 4))
  const yNorth = mercY(lattice.north)
  const ySouth = mercY(lattice.south)
  const span = HAZARD_MAX_G - HAZARD_MIN_G

  for (let py = 0; py < height; py += 1) {
    const lat = fromMercY(yNorth + (ySouth - yNorth) * (height === 1 ? 0 : py / (height - 1)))
    for (let px = 0; px < width; px += 1) {
      const lon =
        lattice.west + (lattice.east - lattice.west) * (width === 1 ? 0 : px / (width - 1))
      const s = sampleHazard(lattice, lon, lat)
      if (Number.isNaN(s.value)) continue
      const fade = edgeFade(s.edge, s.gx, s.gy, feather, wobble)
      if (fade <= 0) continue
      const k = Math.max(0, Math.min(255, Math.round(((s.value - HAZARD_MIN_G) / span) * 255)))
      const [r, g, b] = table[k]!
      const i = (py * width + px) * 4
      out[i] = r
      out[i + 1] = g
      out[i + 2] = b
      out[i + 3] = Math.round(fade * 255)
    }
  }
  return out
}

export interface HazardRaster {
  /** `data:image/png;base64,…`, lista para una fuente `image`. */
  url: string
  coordinates: ImageCorners
}

/**
 * La imagen lista para MapLibre, o `null` si el navegador no puede dibujarla
 * (jsdom en las pruebas) o si la grilla no alcanza.
 */
export function rasterizeHazard(grid: HazardGrid, theme: 'light' | 'dark'): HazardRaster | null {
  if (typeof document === 'undefined' || grid.cells.features.length === 0) return null
  const lattice = buildHazardLattice(grid.cells)
  if (!lattice) return null
  const { width, height } = hazardImageSize(lattice)
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext('2d')
  if (!context) return null
  context.putImageData(
    new ImageData(hazardPixels(lattice, theme, width, height), width, height),
    0,
    0,
  )
  return { url: canvas.toDataURL('image/png'), coordinates: hazardCorners(lattice) }
}
