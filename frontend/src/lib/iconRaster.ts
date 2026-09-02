import { ICON_GLYPHS, type IconGlyph, type IconId } from '@/domain/emergencyIcons'
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
 * # Por qué se rellena y no se traza
 *
 * Antes se trazaba, porque los glifos venían de lucide y son contornos abiertos.
 * A los tamaños reales de este mapa eso no funciona: con `icon-size` entre 0.3 y
 * 0.6 sobre un lienzo de 64, el glifo mide entre 14 y 29 px en pantalla, y un
 * trazo de 2 unidades sobre un lienzo de 24 queda ahí en poco más de un píxel.
 * Todo detalle interior se cierra. La `flame` de lucide lo demostró: su rizo
 * interior colapsaba y el icono se leía como una espiral.
 *
 * Ahora los siete glifos son siluetas cerradas y se rellenan. Si algún día hace
 * falta uno intrínsecamente lineal habrá que reponer el modo de trazo —un
 * `fill()` sobre `M12 9v4` no dibuja nada, un segmento no encierra área—, pero
 * mientras no exista ese glifo la rama no se agrega: sería código que nada
 * ejercita.
 *
 * # `evenodd` y no `nonzero`
 *
 * Los recortes —el signo del triángulo de contingencia, el núcleo de la llama—
 * se declaran como sub-caminos dentro del mismo `d`. Con `nonzero` un sub-camino
 * interior sólo agujerea si va en sentido contrario al exterior, que es una
 * trampa invisible: el icono sale macizo y nadie sabe por qué. Con `evenodd`
 * agujerea siempre, y el sentido en que se escribió el camino deja de importar.
 */

/** Lado del lienzo, en píxeles. */
export const ICON_CANVAS = 64
/** Lienzo en el que están dibujados los glifos. */
const VIEWBOX = 24

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
function rasterize(glyph: IconGlyph): Float64Array | null {
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
  ctx.fillStyle = '#fff'

  for (const d of glyph.paths) ctx.fill(new Path2D(d), 'evenodd')

  const { data } = ctx.getImageData(0, 0, ICON_CANVAS, ICON_CANVAS)
  const alpha = new Float64Array(ICON_CANVAS * ICON_CANVAS)
  for (let i = 0; i < alpha.length; i++) alpha[i] = data[i * 4 + 3]! / 255
  return alpha
}

/** Construye la imagen SDF de un glifo. `null` si no hay canvas disponible. */
export function buildIcon(id: IconId): RasterIcon | null {
  const glyph = ICON_GLYPHS[id]
  const alpha = rasterize(glyph)
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
