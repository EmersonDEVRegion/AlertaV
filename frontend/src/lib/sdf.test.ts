// @vitest-environment node
/**
 * El campo de distancia contra las constantes reales del shader.
 *
 * `symbolSDF` de MapLibre hace `dist = texture(...).a` y compara contra
 * `inner_edge = (256-64)/256 = 0.75`, con `#define SDF_PX 8.0`. Estos tests
 * fijan que lo que generamos encaje con esos dos números; si no encajan, los
 * iconos se dibujan igual pero con el borde en el sitio equivocado y sin halo.
 */

import { describe, expect, it } from 'vitest'
import { SDF_CUTOFF, SDF_RADIUS, alphaToSdf, sdfToImageData } from './sdf'

const W = 96, H = 96, CX = 47.5, CY = 47.5, R = 24

function disc(): Float64Array {
  const a = new Float64Array(W * H)
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++) a[y * W + x] = Math.hypot(x - CX, y - CY) <= R ? 1 : 0
  return a
}

const field = alphaToSdf(disc(), W, H)
const at = (x: number, y: number) => field[y * W + x]!
const profile = Array.from({ length: W - 48 }, (_, i) => [48 + i - CX, at(48 + i, 48)] as const)

describe('constantes acopladas al shader', () => {
  it('el radio coincide con `SDF_PX` del shader', () => {
    expect(SDF_RADIUS).toBe(8)
  })

  it('el corte pone el borde del glifo en 0,75, que es `inner_edge`', () => {
    // (256 - 64) / 256 = 0.75
    expect(255 - 255 * SDF_CUTOFF).toBeCloseTo(0.75 * 255, 0)
  })
})

describe('el campo de distancia', () => {
  it('cruza el umbral del shader exactamente en el borde de la forma', () => {
    const EDGE = 255 - 255 * SDF_CUTOFF
    let crossing = NaN
    for (let i = 1; i < profile.length; i++) {
      const [d0, v0] = profile[i - 1]!
      const [d1, v1] = profile[i]!
      if (v0 >= EDGE && v1 < EDGE) {
        crossing = d0 + ((v0 - EDGE) / (v0 - v1)) * (d1 - d0)
        break
      }
    }
    expect(Math.abs(crossing - R)).toBeLessThan(1.5)
  })

  it('el signo apunta hacia adentro: centro saturado, exterior en cero', () => {
    // Invertirlo produce iconos en negativo — interior transparente, fondo
    // sólido — y es el error más fácil de cometer al montar las dos pasadas.
    expect(at(48, 48)).toBe(255)
    expect(at(0, 0)).toBe(0)
  })

  it('decrece de forma monótona al alejarse', () => {
    expect(profile.every(([, v], i) => i === 0 || v <= profile[i - 1]![1])).toBe(true)
  })

  it('la pendiente corresponde a un campo de 8 px', () => {
    const band = profile.filter(([, v]) => v > 4 && v < 251)
    const steps = band.slice(1).map(([, v], i) => band[i]![1] - v)
    const median = [...steps].sort((a, b) => a - b)[Math.floor(steps.length / 2)]!
    // 255 niveles repartidos en 8 px ≈ 32 por píxel. La mediana y no el
    // promedio: en el píxel donde la máscara binaria cambia de estado hay un
    // salto doble, artefacto inevitable de umbralizar sobre una grilla.
    expect(Math.abs(median - 255 / SDF_RADIUS)).toBeLessThan(3)
  })

  it('deja rampa hacia afuera, que es de donde sale el halo', () => {
    // El shader dibuja el halo con `halo_edge = (6 - halo_width)/SDF_PX`, o sea
    // por fuera del borde. Sin rampa exterior, `icon-halo-width` no pinta nada.
    const outward = profile.filter(([d, v]) => d > R && v > 4).length
    expect(outward).toBeGreaterThanOrEqual(4)
  })

  it('un raster binario NO serviría: no tiene gradiente', () => {
    // Es la razón de existir de este módulo entero.
    const naive = [...disc()].map((a) => (a > 0.5 ? 255 : 0))
    expect(naive.filter((v) => v > 4 && v < 251).length).toBe(0)
  })
})

describe('empaquetado para addImage', () => {
  it('deja el campo en el canal alfa, que es el único que el shader lee', () => {
    const img = sdfToImageData(field, W, H)
    expect(img.data.length).toBe(W * H * 4)
    const i = (48 * W + 48) * 4
    expect(img.data[i + 3]).toBe(255)
    // Color en blanco: si algún entorno premultiplica, un icono mal configurado
    // se ve ausente en vez de como una mancha negra.
    expect([img.data[i], img.data[i + 1], img.data[i + 2]]).toEqual([255, 255, 255])
  })
})
