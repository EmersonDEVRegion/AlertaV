// @vitest-environment node
/**
 * Tests de estilo y jerarquía de la capa de amenaza.
 *
 * El más importante sigue siendo el del `beforeId`, y ahora fija además el
 * arreglo de un bug latente: el ancla apuntaba a `seismic-reach-fill`, que es
 * **condicional** —sólo existe con la casilla de sismos encendida—. MapLibre no
 * ignora un `beforeId` inexistente: emite un `error` y **descarta la capa
 * entera**. Encender la amenaza con los sismos apagados daba una capa que no
 * aparecía nunca, sin nada en pantalla que lo explicara.
 *
 * El segundo bloque en importancia es el del relevo por zoom. Dos capas que se
 * cruzan mal producen un rango de zoom en el que no se ve ninguna de las dos, y
 * eso se lee como «la capa se apagó sola».
 */

import { describe, expect, it } from 'vitest'
import {
  HAZARD_BEFORE_ID,
  HAZARD_CELL_SOURCE_ID,
  HAZARD_FILL_LAYER_ID,
  HAZARD_HEAT_LAYER_ID,
  HAZARD_LAYER_IDS,
  HAZARD_LINE_LAYER_ID,
  HAZARD_NODE_SOURCE_ID,
  hazardFillLayer,
  hazardHeatLayer,
  hazardLineLayer,
} from './hazardLayers'
import { coneFillLayer, reachFillLayer } from './overlayLayers'
import { alertHaloLayer } from './incidentLayers'
import { RAIN_HEAT_LAYER_ID, RAIN_LAYER_IDS } from './rainLayers'
import {
  HAZARD_CROSSFADE,
  HAZARD_HEAT,
  HAZARD_MAX_G,
  HAZARD_MIN_G,
  HAZARD_RAMP,
  HAZARD_VARIABLE,
} from '@/domain/hazardSymbology'

/** Cuenta apariciones de la expresión `["zoom"]` en cualquier profundidad. */
const countZoom = (expression: unknown): number => {
  if (!Array.isArray(expression)) return 0
  const self = expression.length === 1 && expression[0] === 'zoom' ? 1 : 0
  return expression.reduce<number>((total, item) => total + countZoom(item), self)
}

/** Evalúa una interpolación lineal de topes escalares en un zoom dado. */
function atZoom(expression: unknown, zoom: number): number {
  const expr = expression as unknown[]
  const stops = expr.slice(3)
  let result = stops[1] as number

  for (let i = 0; i < stops.length; i += 2) {
    const z = stops[i] as number
    const value = stops[i + 1] as number
    if (zoom >= z) result = value
    else {
      const prevZ = stops[i - 2] as number
      const prevValue = stops[i - 1] as number
      const t = (zoom - prevZ) / (z - prevZ)
      return prevValue + (value - prevValue) * t
    }
  }
  return result
}

describe('jerarquía de dibujo', () => {
  it('se ancla a una capa INCONDICIONAL', () => {
    // El cono de viento es la única capa propia montada siempre. `IncidentMap`
    // lo monta sin condición; el radio sísmico y las de incidentes viven bajo
    // casillas que el usuario puede apagar.
    expect(HAZARD_BEFORE_ID).toBe(coneFillLayer.id)
  })

  it('ya NO se ancla al radio sísmico, que es condicional', () => {
    // El bug latente que había quedado sin arreglar. Si alguien lo revierte,
    // apagar los sismos volvería a hacer desaparecer la amenaza en silencio.
    expect(HAZARD_BEFORE_ID).not.toBe(reachFillLayer.id)
  })

  it('no se ancla a una capa de incidentes: la amenaza va estrictamente debajo', () => {
    expect(HAZARD_BEFORE_ID).not.toBe(alertHaloLayer.id)
  })

  it('no se ancla a sí misma', () => {
    expect(HAZARD_LAYER_IDS).not.toContain(HAZARD_BEFORE_ID)
  })

  it('declara sus tres capas en orden de dibujo', () => {
    // El calor debajo, las celdas encima: es el orden en el que se relevan al
    // hacer zoom, y durante el cruce la retícula tiene que quedar por delante.
    expect([...HAZARD_LAYER_IDS]).toEqual([
      HAZARD_HEAT_LAYER_ID,
      HAZARD_FILL_LAYER_ID,
      HAZARD_LINE_LAYER_ID,
    ])
  })

  it('la capa MÁS BAJA de la lluvia es el ancla candidata', () => {
    // `SeismicHazardLayer` prefiere anclarse bajo la lluvia cuando está
    // montada, y tiene que ser bajo su capa más baja: anclarse a una intermedia
    // dejaría el modelo estático encima de parte del pronóstico.
    expect(RAIN_LAYER_IDS[0]).toBe(RAIN_HEAT_LAYER_ID)
  })

  it('separa las dos fuentes: el heatmap necesita puntos, el relleno polígonos', () => {
    expect(HAZARD_NODE_SOURCE_ID).not.toBe(HAZARD_CELL_SOURCE_ID)
  })
})

describe('mapa de calor', () => {
  const heat = hazardHeatLayer('dark', true)

  it('es de tipo heatmap', () => {
    expect(heat.type).toBe('heatmap')
  })

  it('pesa por la variable del modelo, no por densidad de puntos', () => {
    // La grilla es regular: la densidad de nodos es constante en toda la región.
    // Si el peso no viniera del PGA, el mapa de calor sería un rectángulo plano.
    const weight = JSON.stringify(heat.paint?.['heatmap-weight'])
    expect(weight).toContain('interpolate')
    expect(weight).toContain(String(HAZARD_MIN_G))
    expect(weight).toContain(String(HAZARD_MAX_G))
  })

  it('el nodo más débil pesa más que cero', () => {
    // Con peso 0 el nodo desaparecería del campo, y un hueco en una grilla
    // regular se lee como «acá no hay amenaza». Es falso y además tranquiliza.
    const weight = heat.paint?.['heatmap-weight'] as unknown[]
    const floor = weight[weight.length - 3] as number
    expect(floor).toBeGreaterThan(0)
  })

  it('arranca la rampa en transparente', () => {
    // `heatmap-color` se evalúa sobre TODO el lienzo, también donde la densidad
    // es cero. Un color opaco en la parada 0 pinta un velo sobre la región
    // entera, mar incluido.
    for (const theme of ['light', 'dark'] as const) {
      const [density, color] = HAZARD_HEAT[theme].stops[0]!
      expect(density).toBe(0)
      expect(color).toMatch(/,\s*0\)$/)
    }
  })

  it('el color de la densidad NO depende del zoom', () => {
    // MapLibre rechaza `["zoom"]` dentro de `heatmap-color`: el estilo entero
    // dejaría de compilar y el mapa se quedaría en blanco.
    expect(countZoom(heat.paint?.['heatmap-color'])).toBe(0)
  })

  it('el radio duplica por nivel de zoom', () => {
    // El radio se declara en píxeles pero representa el paso de la grilla, que
    // es una distancia sobre el terreno. Uno fijo sería la misma capa afirmando
    // dos resoluciones distintas según cuánto se haya acercado el usuario.
    const radius = heat.paint?.['heatmap-radius']
    expect(countZoom(radius)).toBe(1)
    const at9 = atZoom(radius, 9)
    const at11 = atZoom(radius, 11)
    expect(at11 / at9).toBeGreaterThan(1.8)
  })
})

describe('relevo por zoom', () => {
  const [from, to] = HAZARD_CROSSFADE

  it('la ventana avanza: el calor manda lejos y las celdas cerca', () => {
    expect(from).toBeLessThan(to)
  })

  it('el calor se retira y las celdas entran en la MISMA ventana', () => {
    const heatOpacity = hazardHeatLayer('dark', true).paint?.['heatmap-opacity']
    const fillOpacity = hazardFillLayer('dark', true).paint?.['fill-opacity']

    // Ventanas distintas dejarían un rango de zoom en el que no se ve ninguna
    // de las dos, y eso se lee como «la capa se apagó sola».
    expect(atZoom(heatOpacity, from)).toBeGreaterThan(0)
    expect(atZoom(heatOpacity, to)).toBe(0)
    expect(atZoom(fillOpacity, from)).toBe(0)
    expect(atZoom(fillOpacity, to)).toBeGreaterThan(0)
  })

  it('a mitad de camino se ve algo de las dos', () => {
    const mid = (from + to) / 2
    const heatOpacity = hazardHeatLayer('dark', true).paint?.['heatmap-opacity']
    const fillOpacity = hazardFillLayer('dark', true).paint?.['fill-opacity']

    expect(atZoom(heatOpacity, mid)).toBeGreaterThan(0)
    expect(atZoom(fillOpacity, mid)).toBeGreaterThan(0)
  })

  it('el zoom es la raíz de cada opacidad, nunca anidado', () => {
    // `["zoom"]` sólo puede ser la entrada de un `interpolate` de nivel
    // superior. Anidarlo tira el estilo completo — el error que este
    // repositorio ya pagó dos veces.
    for (const paint of [
      hazardHeatLayer('dark', true).paint?.['heatmap-opacity'],
      hazardFillLayer('dark', true).paint?.['fill-opacity'],
      hazardLineLayer('dark', true).paint?.['line-opacity'],
    ]) {
      const expr = paint as unknown[]
      expect(expr[0]).toBe('interpolate')
      expect(expr[2]).toEqual(['zoom'])
      expect(countZoom(expr)).toBe(1)
    }
  })
})

describe('visibilidad', () => {
  it('se apaga por `visibility`, nunca desmontando', () => {
    for (const factory of [hazardHeatLayer, hazardFillLayer, hazardLineLayer]) {
      expect(factory('dark', false).layout?.visibility).toBe('none')
      expect(factory('dark', true).layout?.visibility).toBe('visible')
    }
  })
})

describe('estilo sobre mapa oscuro', () => {
  it('mantiene el relleno translúcido en ambos temas', () => {
    for (const theme of ['light', 'dark'] as const) {
      const peak = atZoom(
        hazardFillLayer(theme, true).paint?.['fill-opacity'],
        HAZARD_CROSSFADE[1],
      )
      expect(peak).toBeGreaterThan(0)
      // Por sobre ~0,5 la capa deja de ser contexto y empieza a tapar el mapa.
      expect(peak).toBeLessThanOrEqual(0.4)
    }
  })

  it('usa una rampa distinta por tema, no la misma con otra opacidad', () => {
    const light = HAZARD_RAMP.light.stops.map(([, c]) => c)
    const dark = HAZARD_RAMP.dark.stops.map(([, c]) => c)
    expect(light).not.toEqual(dark)

    const lightHeat = HAZARD_HEAT.light.stops.map(([, c]) => c)
    const darkHeat = HAZARD_HEAT.dark.stops.map(([, c]) => c)
    expect(lightHeat).not.toEqual(darkHeat)
  })

  it('invierte la dirección de luminosidad según el tema', () => {
    // Claro: más amenaza = más oscuro. Oscuro: más amenaza = más brillante.
    const luminance = (hex: string) => {
      const v = hex.replace('#', '')
      return [0, 2, 4].reduce((sum, i) => sum + parseInt(v.slice(i, i + 2), 16), 0)
    }
    const lightStops = HAZARD_RAMP.light.stops.map(([, c]) => luminance(c))
    const darkStops = HAZARD_RAMP.dark.stops.map(([, c]) => luminance(c))

    expect(lightStops[0]!).toBeGreaterThan(lightStops[lightStops.length - 1]!)
    expect(darkStops[0]!).toBeLessThan(darkStops[darkStops.length - 1]!)
  })

  it('no invade la paleta cálida de las emergencias', () => {
    const emergencyHexes = ['#dc2626', '#eab308', '#ea580c', '#b91c1c', '#f97316', '#991b1b']
    const hazardHexes = [
      ...HAZARD_RAMP.light.stops,
      ...HAZARD_RAMP.dark.stops,
      ...HAZARD_HEAT.light.stops,
      ...HAZARD_HEAT.dark.stops,
    ].map(([, c]) => String(c).toLowerCase())

    for (const hex of emergencyHexes) expect(hazardHexes).not.toContain(hex)
  })

  it('el mapa de calor tampoco termina en rojo', () => {
    // La convención de los heatmap es el arcoíris, que acaba en rojo. Acá eso
    // diría «está pasando algo», que es exactamente lo que esta capa NO afirma.
    for (const theme of ['light', 'dark'] as const) {
      const hottest = HAZARD_HEAT[theme].stops[HAZARD_HEAT[theme].stops.length - 1]![1]
      const [r, g, b] = /rgba?\((\d+),\s*(\d+),\s*(\d+)/
        .exec(hottest)!
        .slice(1, 4)
        .map(Number) as [number, number, number]
      // Violeta: el azul manda sobre el verde y el rojo no domina solo.
      expect(b).toBeGreaterThanOrEqual(g)
      expect(r).toBeLessThanOrEqual(b)
    }
  })

  it('desactiva el antialias del relleno para no dibujar costuras', () => {
    expect(hazardFillLayer('dark', true).paint?.['fill-antialias']).toBe(false)
  })

  it('colorea por la variable documentada del artefacto', () => {
    const expr = JSON.stringify(hazardFillLayer('dark', true).paint?.['fill-color'])
    expect(expr).toContain(HAZARD_VARIABLE)
    expect(expr).toContain('interpolate')
  })
})
