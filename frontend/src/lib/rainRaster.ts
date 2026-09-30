import type { RainGrid } from '@/api/rainGrid'
import { rainColorAt } from '@/domain/rainSymbology'

/**
 * La grilla de lluvia como imagen: interpolación bilineal y la escala de
 * colores de `rainSymbology`.
 *
 * # Por qué una imagen y no un `heatmap` de MapLibre
 *
 * Un `heatmap` es un estimador de densidad: sobre una grilla regular la densidad
 * es constante y el kernel sólo suma vecinos, así que el color dejaría de ser
 * un valor de mm/h (la misma lección de `hazardLayers.ts`). Interpolando, cada
 * píxel es un promedio ponderado de las cuatro celdas que lo rodean: el color
 * que se ve es el valor del modelo, y la escala del widget puede llevar números.
 *
 * # Resolución
 *
 * Doce píxeles por celda (~290 × 300 para la grilla de §L, 25 × 26). Más no agrega
 * nada: la celda de la grilla mide ~20 km, y el suavizado lo hace el propio
 * `raster-resampling: linear` al acercarse.
 */

const PIXELS_PER_CELL = 12

/** Esquinas de la imagen para una fuente `image`: NO, NE, SE, SO. */
type ImageCorners = [[number, number], [number, number], [number, number], [number, number]]

export function gridCorners(grid: RainGrid): ImageCorners {
  const east = grid.west + (grid.nx - 1) * grid.step
  const south = grid.north - (grid.ny - 1) * grid.step
  return [
    [grid.west, grid.north],
    [east, grid.north],
    [east, south],
    [grid.west, south],
  ]
}

/** Valor interpolado en una posición fraccionaria de la grilla. */
export function sampleGrid(grid: RainGrid, gx: number, gy: number): number | null {
  const x0 = Math.max(0, Math.min(grid.nx - 1, Math.floor(gx)))
  const y0 = Math.max(0, Math.min(grid.ny - 1, Math.floor(gy)))
  const x1 = Math.min(grid.nx - 1, x0 + 1)
  const y1 = Math.min(grid.ny - 1, y0 + 1)
  const tx = Math.max(0, Math.min(1, gx - x0))
  const ty = Math.max(0, Math.min(1, gy - y0))
  const at = (x: number, y: number) => grid.values[y * grid.nx + x] ?? null
  const corners = [at(x0, y0), at(x1, y0), at(x0, y1), at(x1, y1)]
  // Sin ningún dato alrededor: no se inventa nada.
  if (corners.every((v) => v === null)) return null
  // Una celda sin dato cuenta como seca para no cortar el campo en un agujero.
  const [a, b, c, d] = corners.map((v) => v ?? 0) as [number, number, number, number]
  const top = a + (b - a) * tx
  const bottom = c + (d - c) * tx
  return top + (bottom - top) * ty
}

/**
 * Cuántas celdas se desvanece el borde de la imagen. La grilla termina en una
 * caja y el modelo no: sin esto, un frente que cruza el borde se vería cortado
 * a cuchillo en una línea recta que no existe.
 *
 * Desde §L la caja es la misma que `MAP_MAX_BOUNDS`, así que el borde sólo se
 * ve en el límite del mapa. Con celdas de 0,2°, dos celdas son ~40 km.
 */
const EDGE_FEATHER_CELLS = 2

/**
 * Cuánto se ondula el final del difuminado, en celdas.
 *
 * Un degradado recto sigue leyéndose como una recta cuando llueve fuerte justo
 * en el borde: el ojo ve la línea donde el color empieza a caer (§L, la
 * cordillera del 30-09). Correr ese comienzo con una onda suave lo vuelve un
 * final de nubosidad, no un corte.
 */
const EDGE_WOBBLE_CELLS = 1.4

/**
 * Ondulación determinista en [0, 1]: dos senos de frecuencias que no se
 * sincronizan. Ni `Math.random` ni ruido con semilla: la misma grilla tiene que
 * dar siempre la misma imagen, o el borde "respiraría" en cada refresco.
 * Longitudes de onda de 5 a 8 celdas: ondas amplias, no un borde dentado.
 */
export function edgeWobble(gx: number, gy: number): number {
  const w = 0.6 * Math.sin(gx * 0.9 + gy * 0.35) + 0.4 * Math.sin(gy * 1.25 - gx * 0.55 + 1.7)
  return (w + 1) / 2
}

const smooth = (t: number) => {
  const x = Math.max(0, Math.min(1, t))
  return x * x * (3 - 2 * x)
}

/** Los píxeles RGBA de la imagen, fila a fila de norte a sur. */
export function rainPixels(
  grid: RainGrid,
  width: number,
  height: number,
  feather = EDGE_FEATHER_CELLS,
  wobble = EDGE_WOBBLE_CELLS,
): Uint8ClampedArray<ArrayBuffer> {
  const out = new Uint8ClampedArray(new ArrayBuffer(width * height * 4))
  for (let py = 0; py < height; py += 1) {
    const gy = height === 1 ? 0 : (py / (height - 1)) * (grid.ny - 1)
    for (let px = 0; px < width; px += 1) {
      const gx = width === 1 ? 0 : (px / (width - 1)) * (grid.nx - 1)
      const [r, g, b, a] = rainColorAt(sampleGrid(grid, gx, gy))
      // Distancia al borde más cercano, en celdas.
      const edge = Math.min(gx, gy, grid.nx - 1 - gx, grid.ny - 1 - gy)
      // El comienzo del difuminado se corre hacia adentro entre 0 y `wobble`
      // celdas. En el borde mismo (`edge = 0`) sigue siendo transparente.
      const inset = wobble > 0 ? wobble * edgeWobble(gx, gy) : 0
      const fade = feather > 0 ? smooth((edge - inset) / feather) : 1
      const i = (py * width + px) * 4
      out[i] = r
      out[i + 1] = g
      out[i + 2] = b
      out[i + 3] = Math.round(a * fade * 255)
    }
  }
  return out
}

export interface RainRaster {
  /** `data:image/png;base64,…`, lista para una fuente `image`. */
  url: string
  coordinates: ImageCorners
  /** ¿Hay al menos un píxel con lluvia? Sin lluvia, no se monta la capa. */
  wet: boolean
}

/**
 * La imagen lista para MapLibre, o `null` si el navegador no puede dibujarla
 * (jsdom en las pruebas) o si no hay grilla.
 */
export function rasterizeRainGrid(grid: RainGrid | null): RainRaster | null {
  if (!grid || typeof document === 'undefined') return null
  const width = (grid.nx - 1) * PIXELS_PER_CELL + 1
  const height = (grid.ny - 1) * PIXELS_PER_CELL + 1
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext('2d')
  if (!context) return null
  const pixels = rainPixels(grid, width, height)
  context.putImageData(new ImageData(pixels, width, height), 0, 0)
  let wet = false
  for (let i = 3; i < pixels.length; i += 4) {
    if (pixels[i]! > 0) {
      wet = true
      break
    }
  }
  return { url: canvas.toDataURL('image/png'), coordinates: gridCorners(grid), wet }
}
