// @vitest-environment node
/**
 * Tests de la capa de lluvia.
 *
 * Los tres primeros bloques cubren fallos que NO producen error en consola:
 *
 *   1. **El anclaje.** Si `RAIN_BEFORE_ID` deja de coincidir con una capa real,
 *      MapLibre emite un `error` y descarta la capa entera. Peor aún si el ancla
 *      existe pero está en el sitio equivocado: la lluvia taparía los pines de
 *      emergencia y el mapa invertiría su jerarquía sin decir nada.
 *   2. **El zoom como raíz del radio.** `["zoom"]` sólo puede ser la entrada de
 *      un `interpolate` de nivel superior. Anidarlo tira el estilo completo — es
 *      el error que ya se pagó dos veces en este repositorio.
 *   3. **El booleano estricto.** Las expresiones de MapLibre no comparan entre
 *      tipos: si `riesgo_inundacion` llegara como la cadena `"true"`, el filtro
 *      del anillo no encontraría nada y no habría ni un aviso.
 */

import { describe, expect, it } from 'vitest'
import {
  IS_FLOOD_RISK,
  RAIN_BEFORE_ID,
  RAIN_CORE_LAYER_ID,
  RAIN_HALO_LAYER_ID,
  RAIN_LAYER_IDS,
  RAIN_RISK_RING_LAYER_ID,
  rainCoreLayer,
  rainHaloLayer,
  rainRiskRingLayer,
} from './rainLayers'
import { coneFillLayer } from './overlayLayers'
import { alertHaloLayer, coreLayer } from './incidentLayers'
import { RAIN_MM_MAX, RAIN_MM_MIN, RAIN_PALETTE } from '@/domain/rainSymbology'

const ALL = [rainHaloLayer, rainCoreLayer, rainRiskRingLayer] as const

describe('jerarquía de dibujo', () => {
  it('se ancla a una capa que existe de verdad', () => {
    expect(RAIN_BEFORE_ID).toBe(coneFillLayer.id)
  })

  it('no se ancla a una capa de incidentes: la lluvia va estrictamente por debajo', () => {
    // Anclar ANTES de los incidentes los dejaría por encima, que es lo correcto,
    // pero el ancla real tiene que ser una capa incondicional. Las de incidentes
    // se desmontan al apagar su casilla y el `addLayer` fallaría.
    expect(RAIN_BEFORE_ID).not.toBe(alertHaloLayer.id)
    expect(RAIN_BEFORE_ID).not.toBe(coreLayer.id)
  })

  it('no se ancla a sí misma', () => {
    expect(RAIN_LAYER_IDS).not.toContain(RAIN_BEFORE_ID)
  })

  it('declara sus tres capas en orden de dibujo', () => {
    expect([...RAIN_LAYER_IDS]).toEqual([
      RAIN_HALO_LAYER_ID,
      RAIN_CORE_LAYER_ID,
      RAIN_RISK_RING_LAYER_ID,
    ])
  })
})

describe('radio: el zoom como raíz', () => {
  /** Cuenta apariciones de la expresión `["zoom"]` en cualquier profundidad. */
  const countZoom = (expression: unknown): number => {
    if (!Array.isArray(expression)) return 0
    const self = expression.length === 1 && expression[0] === 'zoom' ? 1 : 0
    return expression.reduce<number>((total, item) => total + countZoom(item), self)
  }

  for (const factory of ALL) {
    const layer = factory('dark', true)

    it(`${layer.id}: el zoom es la entrada del interpolate de nivel superior`, () => {
      const radius = layer.paint?.['circle-radius'] as unknown[]

      expect(radius[0]).toBe('interpolate')
      expect(radius[1]).toEqual(['linear'])
      expect(radius[2]).toEqual(['zoom'])
    })

    it(`${layer.id}: el zoom aparece exactamente UNA vez`, () => {
      // Más de una delataría una capa derivada sumando sobre un radio ya
      // interpolado, que es la forma en que este bug vuelve a aparecer.
      expect(countZoom(layer.paint?.['circle-radius'])).toBe(1)
    })

    it(`${layer.id}: dentro de cada tope interpola por intensidad`, () => {
      const serialized = JSON.stringify(layer.paint?.['circle-radius'])
      expect(serialized).toContain('mm_hora_max')
      expect(serialized).toContain(String(RAIN_MM_MIN))
      expect(serialized).toContain(String(RAIN_MM_MAX))
    })
  }

  it('el radio crece con el zoom y con la intensidad', () => {
    const radius = rainCoreLayer('dark', true).paint?.['circle-radius'] as unknown[]
    // [ 'interpolate', ['linear'], ['zoom'], z1, expr1, z2, expr2, ... ]
    const stops = radius.slice(3)

    let previousZoom = -Infinity
    for (let i = 0; i < stops.length; i += 2) {
      const zoom = stops[i] as number
      const byIntensity = stops[i + 1] as unknown[]

      expect(zoom).toBeGreaterThan(previousZoom)
      previousZoom = zoom

      const atMin = byIntensity[4] as number
      const atMax = byIntensity[6] as number
      expect(atMax).toBeGreaterThan(atMin)
    }
  })
})

describe('el flag de riesgo se lee como booleano estricto', () => {
  it('compara contra `true`, no contra la cadena "true"', () => {
    expect(IS_FLOOD_RISK).toEqual(['==', ['get', 'riesgo_inundacion'], true])
    // El literal booleano, no su serialización.
    expect(IS_FLOOD_RISK[2]).toBe(true)
    expect(IS_FLOOD_RISK[2]).not.toBe('true')
  })

  it('sólo el anillo filtra: las manchas de fondo dibujan toda la lluvia', () => {
    expect(rainRiskRingLayer('dark', true).filter).toEqual(IS_FLOOD_RISK)
    expect(rainHaloLayer('dark', true).filter).toBeUndefined()
    expect(rainCoreLayer('dark', true).filter).toBeUndefined()
  })

  it('el riesgo cambia color Y opacidad, no sólo el matiz', () => {
    for (const theme of ['light', 'dark'] as const) {
      const paint = rainCoreLayer(theme, true).paint
      const color = JSON.stringify(paint?.['circle-color'])
      const opacity = JSON.stringify(paint?.['circle-opacity'])

      expect(color).toContain(RAIN_PALETTE[theme].risk)
      expect(color).toContain(RAIN_PALETTE[theme].rain)
      // Doble codificación: quien no distingue los dos azules sigue leyendo el
      // contraste por densidad.
      expect(opacity).toContain(String(RAIN_PALETTE[theme].coreOpacityRisk))
      expect(RAIN_PALETTE[theme].coreOpacityRisk).toBeGreaterThan(
        RAIN_PALETTE[theme].coreOpacity,
      )
    }
  })
})

describe('visibilidad y coste', () => {
  it('se apaga por `visibility`, nunca desmontando la fuente', () => {
    for (const factory of ALL) {
      expect(factory('dark', false).layout?.visibility).toBe('none')
      expect(factory('dark', true).layout?.visibility).toBe('visible')
    }
  })

  it('el anillo desactiva la transición para que el pulso no repinte a 60 fps', () => {
    // Con los 300 ms por defecto, cada escritura del pulso encolaría su propia
    // interpolación y MapLibre repintaría en cada frame: justo lo que la
    // animación por rAF con escrituras espaciadas evita.
    const paint = rainRiskRingLayer('dark', true).paint
    expect(paint?.['circle-stroke-opacity-transition']).toEqual({ duration: 0, delay: 0 })
  })

  it('anima una propiedad constante, no una expresión por feature', () => {
    // Un valor data-driven obligaría a reconstruir el búfer de vértices de
    // pintura en cada escritura; un escalar viaja como uniform del shader.
    const value = rainRiskRingLayer('dark', true).paint?.['circle-stroke-opacity']
    expect(typeof value).toBe('number')
  })
})

describe('estilo: contexto, no emergencia', () => {
  it('mantiene todo por debajo del umbral en que dejaría de ser fondo', () => {
    for (const theme of ['light', 'dark'] as const) {
      const palette = RAIN_PALETTE[theme]
      for (const opacity of [palette.haloOpacity, palette.coreOpacity, palette.coreOpacityRisk]) {
        expect(opacity).toBeGreaterThan(0)
        expect(opacity).toBeLessThanOrEqual(0.45)
      }
    }
  })

  it('difumina el borde: mancha atmosférica, no pin', () => {
    expect(rainHaloLayer('dark', true).paint?.['circle-blur']).toBeGreaterThan(0.5)
    expect(rainCoreLayer('dark', true).paint?.['circle-blur']).toBeGreaterThan(0)
  })

  it('no invade la paleta cálida de las emergencias ni el violeta de la amenaza', () => {
    const taken = ['#dc2626', '#eab308', '#ea580c', '#f97316', '#8b5cf6', '#a78bfa']
    const mine = (['light', 'dark'] as const).flatMap((theme) => [
      RAIN_PALETTE[theme].rain.toLowerCase(),
      RAIN_PALETTE[theme].risk.toLowerCase(),
      RAIN_PALETTE[theme].ring.toLowerCase(),
    ])
    for (const hex of taken) expect(mine).not.toContain(hex)
  })

  it('invierte la dirección de luminosidad del riesgo según el tema', () => {
    const luminance = (hex: string) => {
      const value = hex.replace('#', '')
      return [0, 2, 4].reduce((sum, i) => sum + parseInt(value.slice(i, i + 2), 16), 0)
    }
    // Claro: más riesgo = más oscuro. Oscuro: más riesgo = más brillante.
    expect(luminance(RAIN_PALETTE.light.risk)).toBeLessThan(luminance(RAIN_PALETTE.light.rain))
    expect(luminance(RAIN_PALETTE.dark.risk)).toBeGreaterThan(luminance(RAIN_PALETTE.dark.rain))
  })

  it('el reposo del pulso es el extremo VISIBLE, no el transparente', () => {
    for (const theme of ['light', 'dark'] as const) {
      const [min, max] = RAIN_PALETTE[theme].ringOpacity
      expect(max).toBeGreaterThan(min)
      // Con `prefers-reduced-motion` el anillo se queda en `max`: una capa que
      // sólo se entiende cuando se mueve está mal diseñada.
      expect(rainRiskRingLayer(theme, true).paint?.['circle-stroke-opacity']).toBe(max)
    }
  })
})
