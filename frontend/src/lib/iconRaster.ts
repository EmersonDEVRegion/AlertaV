import { ICON_GLYPHS, type IconId } from '@/domain/emergencyIcons'
import { SDF_RADIUS, alphaToSdf, sdfToImageData } from './sdf'

/**
 * Rasterización de los glifos y construcción de sus campos de distancia.
 *
 * # El tamaño y el margen no son arbitrarios
 *
 * El lienzo mide 64 px y el glifo se dibuja dentro de 48, dejando 8 px de
 * margen por lado. Ese margen tiene que ser **al menos `SDF_RADIUS`**: el campo
 * de distancia se extiende 8 px hacia afuera del trazo, y si el borde del
 * lienzo llegara antes, la transformada mediría contra un recorte inexistente y
 * el halo aparecería cortado en seco justo en los bordes de la imagen.
 *
 * # Por qué se traza y no se rellena
 *
 * Los glifos de lucide son contornos abiertos con `stroke`, no siluetas
 * cerradas. Un `fill()` sobre `M12 9v4` no dibuja nada —un segmento no encierra
 * área—, así que el icono saldría vacío. Se trazan con extremos y uniones
 * redondeadas, que es lo que les da su forma característica.
 */

/** Lado del lienzo, en píxeles. */
export const ICON_CANVAS = 64
/** Lienzo de lucide. */
const VIEWBOX = 24
/** Grosor del trazo en unidades de lucide. */
const STROKE = 2

export interface RasterIcon {
  id: IconId
  image: { width: number; height: number; data: Uint8ClampedArray }
}

/**
 * Devuelve la cobertura alfa del glifo, en 0..1.
 *
 * Se separa del cálculo del SDF para poder probar cada mitad por su cuenta: la
 * rasterización necesita un canvas y sólo corre en el navegador, mientras que
 * la transformada de distancia es aritmética pura y se prueba en Node.
 */
function rasterize(paths: readonly string[]): Float64Array | null {
  const canvas = document.createElement('canvas')
  canvas.width = ICON_CANVAS
  canvas.height = ICON_CANVAS
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx) return null

  const inner = ICON_CANVAS - SDF_RADIUS * 2
  const scale = inner / VIEWBOX

  ctx.clearRect(0, 0, ICON_CANVAS, ICON_CANVAS)
  ctx.translate(SDF_RADIUS, SDF_RADIUS)
  ctx.scale(scale, scale)
  ctx.strokeStyle = '#fff'
  ctx.lineWidth = STROKE
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  // El trazo se ensancha al escalar; `miterLimit` alto evita que las uniones
  // agudas del vehículo se recorten en pico.
  ctx.miterLimit = 4

  for (const d of paths) ctx.stroke(new Path2D(d))

  const { data } = ctx.getImageData(0, 0, ICON_CANVAS, ICON_CANVAS)
  const alpha = new Float64Array(ICON_CANVAS * ICON_CANVAS)
  for (let i = 0; i < alpha.length; i++) alpha[i] = data[i * 4 + 3]! / 255
  return alpha
}

/** Construye la imagen SDF de un glifo. `null` si no hay canvas disponible. */
export function buildIcon(id: IconId): RasterIcon | null {
  const glyph = ICON_GLYPHS[id]
  const alpha = rasterize(glyph.paths)
  if (!alpha) return null

  const sdf = alphaToSdf(alpha, ICON_CANVAS, ICON_CANVAS)
  return { id, image: sdfToImageData(sdf, ICON_CANVAS, ICON_CANVAS) }
}

/**
 * Construye todos los glifos, cediendo el hilo entre uno y otro.
 *
 * Son siete transformadas de distancia sobre lienzos de 64×64: unos pocos
 * milisegundos en total, pero se ejecutan justo cuando el mapa está montando su
 * primer cuadro. `await Promise.resolve()` entre iteraciones deja que el
 * navegador intercale el render en vez de bloquear la tarea completa, que es lo
 * que pide «sin bloquear el renderizado inicial».
 */
export async function buildAllIcons(ids: readonly IconId[]): Promise<RasterIcon[]> {
  const out: RasterIcon[] = []
  for (const id of ids) {
    const icon = buildIcon(id)
    if (icon) out.push(icon)
    await Promise.resolve()
  }
  return out
}
