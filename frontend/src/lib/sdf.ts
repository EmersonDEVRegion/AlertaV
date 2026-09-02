/**
 * Generación de campos de distancia con signo (SDF) para iconos de MapLibre.
 *
 * # Por qué esto existe y no basta con rasterizar el SVG
 *
 * La receta que circula para `map.addImage(id, img, { sdf: true })` es dibujar
 * el SVG en un canvas y pasar el `ImageData`. Funciona a medias y falla en
 * cuanto se escala o se pide halo, porque **el canal alfa no se interpreta como
 * opacidad sino como distancia**.
 *
 * El fragment shader de MapLibre (`symbolSDF`) hace, literalmente:
 *
 *     float dist = texture(u_texture, tex).a;
 *     lowp float inner_edge = (256.0 - 64.0) / 256.0;   // 0.75
 *     alpha = smoothstep(inner_edge - g, inner_edge + g, dist);
 *
 * De ahí salen las dos constantes que hay que respetar, y que casi ninguna
 * implementación casera acierta:
 *
 *   * **El borde del glifo está en alfa 0,75**, no en 0,5. Es
 *     `(256-64)/256`, la convención heredada de Mapbox.
 *   * **El campo se extiende 8 px** (`#define SDF_PX 8.0`). El halo se calcula
 *     como `(6.0 - halo_width/fontScale) / SDF_PX`, así que un campo con otro
 *     radio da halos del grosor equivocado.
 *
 * Un alfa binario —0 fuera, 255 dentro— cruza el umbral en un solo píxel: el
 * `smoothstep` no tiene pendiente sobre la que trabajar, el borde queda duro y
 * dentado al ampliar, y `icon-halo-width` no dibuja nada porque no hay gradiente
 * hacia el que expandirse. Por eso acá se calcula la distancia de verdad.
 *
 * # El algoritmo
 *
 * Transformada de distancia euclidiana exacta de Felzenszwalb y Huttenlocher:
 * dos pasadas separables —una por columnas, otra por filas— sobre la envolvente
 * inferior de parábolas. Es O(n) por dimensión y da distancia exacta, no la
 * aproximación por chamfer que usan los métodos de dos barridos.
 *
 * Se corre dos veces, sobre la máscara y sobre su complemento, y se restan: eso
 * da el signo. Dentro del glifo la distancia es negativa, fuera positiva.
 */

/** Radio del campo, en píxeles. Debe coincidir con `SDF_PX` del shader. */
export const SDF_RADIUS = 8

/**
 * Desplazamiento del cero. Con 0,25 el borde del glifo cae en
 * `255 - 255*0.25 = 191`, o sea 0,749 — el `inner_edge` que espera el shader.
 */
export const SDF_CUTOFF = 0.25

const INF = 1e20

/**
 * Envolvente inferior de parábolas sobre una sola dimensión.
 *
 * `f` entra con los valores y sale con la distancia al cuadrado. Los arreglos
 * `v`, `z` y `d` se reciben ya asignados para no reservar memoria por columna:
 * esto corre una vez por fila y una vez por columna de cada icono.
 */
function edt1d(
  f: Float64Array,
  d: Float64Array,
  v: Int32Array,
  z: Float64Array,
  n: number,
): void {
  v[0] = 0
  z[0] = -INF
  z[1] = INF
  d[0] = f[0]!

  for (let q = 1, k = 0, s = 0; q < n; q++) {
    do {
      const r = v[k]!
      s = (f[q]! - f[r]! + q * q - r * r) / (2 * q - 2 * r)
    } while (s <= z[k]! && --k > -1)

    k++
    v[k] = q
    z[k] = s
    z[k + 1] = INF
  }

  for (let q = 0, k = 0; q < n; q++) {
    while (z[k + 1]! < q) k++
    const r = v[k]!
    d[q] = (q - r) * (q - r) + f[r]!
  }
}

/** Transformada 2D: separable, columnas y luego filas. */
function edt(data: Float64Array, width: number, height: number): void {
  const size = Math.max(width, height)
  const f = new Float64Array(size)
  const d = new Float64Array(size)
  const v = new Int32Array(size)
  const z = new Float64Array(size + 1)

  for (let x = 0; x < width; x++) {
    for (let y = 0; y < height; y++) f[y] = data[y * width + x]!
    edt1d(f, d, v, z, height)
    for (let y = 0; y < height; y++) data[y * width + x] = d[y]!
  }

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) f[x] = data[y * width + x]!
    edt1d(f, d, v, z, width)
    for (let x = 0; x < width; x++) data[y * width + x] = Math.sqrt(d[x]!)
  }
}

/**
 * Máscara de cobertura → campo de distancia listo para `addImage`.
 *
 * `alpha` es la cobertura por píxel en 0..1 (lo que da un canvas al rasterizar).
 * Se umbraliza en 0,5 para obtener la forma; el antialiasing del rasterizador se
 * descarta a propósito, porque el suavizado del borde lo va a producir el
 * `smoothstep` del shader a partir de la distancia, con mucha mejor calidad y a
 * cualquier escala.
 */
export function alphaToSdf(
  alpha: Float64Array | number[],
  width: number,
  height: number,
  radius = SDF_RADIUS,
  cutoff = SDF_CUTOFF,
): Uint8ClampedArray {
  /*
   * `edt` mide la distancia a la semilla más cercana, y las semillas son los
   * ceros del arreglo. De ahí los dos campos, nombrados por lo que MIDEN y no
   * por dónde viven — confundirlos invierte el signo y produce iconos en
   * negativo, con el interior transparente y el fondo sólido.
   */
  const toUncovered = new Float64Array(width * height) // grande DENTRO del glifo
  const toCovered = new Float64Array(width * height) // grande FUERA del glifo

  for (let i = 0; i < width * height; i++) {
    const covered = (alpha[i] ?? 0) > 0.5
    toUncovered[i] = covered ? INF : 0
    toCovered[i] = covered ? 0 : INF
  }

  edt(toUncovered, width, height)
  edt(toCovered, width, height)

  const out = new Uint8ClampedArray(width * height)
  for (let i = 0; i < width * height; i++) {
    /*
     * Distancia con signo, POSITIVA FUERA del glifo:
     *
     *   fuera  -> toCovered = d,  toUncovered = 0  ->  +d
     *   dentro -> toCovered = 0,  toUncovered = d  ->  -d
     *
     * Y la conversión invierte: dentro (negativo) sube hacia 255, fuera baja
     * hacia 0, con el borde en 255·(1−cutoff) = 191.
     */
    const signed = toCovered[i]! - toUncovered[i]!
    out[i] = Math.round(255 - 255 * (signed / radius + cutoff))
  }
  return out
}

/**
 * Empaqueta el campo como RGBA.
 *
 * El shader sólo lee `.a`, así que los tres canales de color son irrelevantes
 * —el color final lo pone `icon-color`—. Se dejan en blanco y no en negro
 * porque algunos entornos multiplican por alfa al subir la textura, y con negro
 * un icono mal configurado se vería como una mancha oscura en vez de no verse.
 */
export function sdfToImageData(
  sdf: Uint8ClampedArray,
  width: number,
  height: number,
): { width: number; height: number; data: Uint8ClampedArray } {
  const rgba = new Uint8ClampedArray(width * height * 4)
  for (let i = 0; i < width * height; i++) {
    rgba[i * 4] = 255
    rgba[i * 4 + 1] = 255
    rgba[i * 4 + 2] = 255
    rgba[i * 4 + 3] = sdf[i]!
  }
  return { width, height, data: rgba }
}
