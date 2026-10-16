// @vitest-environment node
/**
 * Tests de estilo y jerarquía de la capa de amenaza.
 *
 * El más importante sigue siendo el del `beforeId`, y fija además el arreglo de
 * un bug latente: el ancla apuntaba a `seismic-reach-fill`, que es
 * **condicional** —sólo existe con la casilla de sismos encendida—. MapLibre no
 * ignora un `beforeId` inexistente: emite un `error` y **descarta la capa
 * entera**. Encender la amenaza con los sismos apagados daba una capa que no
 * aparecía nunca, sin nada en pantalla que lo explicara.
 *
 * El segundo bloque en importancia es el del encuadre de la rampa. Una rampa
 * cuyos extremos no envuelven la distribución real del artefacto satura, y una
 * capa saturada es un rectángulo de un solo color: no lanza nada, no aparece en
 * consola, y lo que muestra es plausible. Es el modo de falla más caro de esta
 * capa y el que la última reescritura vino a cerrar.
 */

import { describe, expect, it } from 'vitest'
import {
  HAZARD_BEFORE_ID,
  HAZARD_FIELD_LAYER_ID,
  HAZARD_LAYER_IDS,
  HAZARD_SOURCE_ID,
  hazardFieldLayer,
} from './hazardLayers'
import { coneFillLayer, reachFillLayer } from './overlayLayers'
import { alertHaloLayer } from './incidentLayers'
import { RAIN_FIELD_LAYER_ID, RAIN_LAYER_IDS } from './rainLayers'
import {
  HAZARD_MAX_G,
  HAZARD_MIN_G,
  HAZARD_RAMP,
} from '@/domain/hazardSymbology'

/**
 * Distribución REAL de `pga_475` en el artefacto de la V Región, medida sobre
 * `backend/static/geo/amenaza_sismica_valpo.json`.
 *
 * Son los números que delataron el bug: la rampa iba de 0,15 a 0,60 g, así que
 * el cuartil superior entero —la franja costera, la de mayor amenaza— caía
 * fuera y se pintaba todo del mismo color.
 */
const ARTEFACTO = { min: 0.276, p25: 0.33, p75: 0.603, max: 0.94 } as const

/** Cuenta apariciones de la expresión `["zoom"]` en cualquier profundidad. */
const countZoom = (expression: unknown): number => {
  if (!Array.isArray(expression)) return 0
  const self = expression.length === 1 && expression[0] === 'zoom' ? 1 : 0
  return expression.reduce<number>((total, item) => total + countZoom(item), self)
}

/**
 * Evalúa una interpolación lineal de topes escalares en un zoom dado.
 *
 * **Satura fuera de rango**, igual que MapLibre: por debajo de la primera parada
 * devuelve su valor y por encima de la última, el suyo. La versión anterior
 * calculaba una pendiente contra `stops[-2]` y devolvía `NaN` para cualquier
 * zoom anterior a la primera parada — que era imposible de notar mientras todas
 * las capas empezaran en z7, y deja de serlo en cuanto una empieza más tarde,
 * como la retícula.
 */
function atZoom(expression: unknown, zoom: number): number {
  const expr = expression as unknown[]
  const stops = expr.slice(3)
  if (zoom <= (stops[0] as number)) return stops[1] as number

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

  it('declara su única capa para el re-anclaje', () => {
    // §I: la retícula se fue con los polígonos. Una capa fuera de esta lista no
    // se re-ancla tras un cambio de tema.
    expect([...HAZARD_LAYER_IDS]).toEqual([HAZARD_FIELD_LAYER_ID])
  })

  it('la capa MÁS BAJA de la lluvia es el ancla candidata', () => {
    // `SeismicHazardLayer` prefiere anclarse bajo la lluvia cuando está
    // montada, y tiene que ser bajo su capa más baja: anclarse a una intermedia
    // dejaría el modelo estático encima de parte del pronóstico.
    expect(RAIN_LAYER_IDS[0]).toBe(RAIN_FIELD_LAYER_ID)
  })
})

describe('una sola representación', () => {
  it('es una imagen, no un heatmap ni polígonos', () => {
    /*
     * Un `heatmap` sobre una grilla REGULAR no mide el valor del modelo: mide
     * densidad, constante por construcción. Y los polígonos (hasta §I)
     * dibujaban la caja de la grilla con bordes rectos. La imagen interpola
     * entre nodos y desvanece el borde (`lib/hazardRaster.ts`).
     */
    expect(hazardFieldLayer('dark', true).type).toBe('raster')
    expect(HAZARD_SOURCE_ID).toBe('seismic-hazard')
  })

  it('se suaviza al acercarse: un píxel no se ve como un bloque', () => {
    expect(hazardFieldLayer('dark', true).paint?.['raster-resampling']).toBe('linear')
  })

  it('la superficie está pintada en TODO el rango de zoom', () => {
    // Un tramo con opacidad 0 se leería como «se apagó sola».
    for (const theme of ['light', 'dark'] as const) {
      const opacity = hazardFieldLayer(theme, true).paint?.['raster-opacity']
      for (const zoom of [7, 9, 10.5, 12, 14, 17]) {
        expect(atZoom(opacity, zoom)).toBeGreaterThan(0.2)
      }
    }
  })

  it('la superficie se aligera al acercarse, no al revés', () => {
    // De lejos la capa es el sujeto; de cerca la pregunta vuelve a ser dónde
    // está uno, y un velo del 40 % sobre los nombres de calle estorba.
    for (const theme of ['light', 'dark'] as const) {
      const opacity = hazardFieldLayer(theme, true).paint?.['raster-opacity']
      expect(atZoom(opacity, 15)).toBeLessThan(atZoom(opacity, 8))
    }
  })

  it('el zoom es la raíz de la opacidad, nunca anidado', () => {
    const expr = hazardFieldLayer('dark', true).paint?.['raster-opacity'] as unknown[]
    expect(expr[0]).toBe('interpolate')
    expect(expr[2]).toEqual(['zoom'])
    expect(countZoom(expr)).toBe(1)
  })
})

describe('encuadre de la rampa', () => {
  it('envuelve la distribución real del artefacto sin saturar', () => {
    /*
     * El bug: extremos en 0,15 y 0,60 g sobre un artefacto que va de 0,276 a
     * 0,940. Todo lo que superaba 0,60 —el 25 % del territorio, y justo la
     * franja costera— tomaba el mismo último color, así que la variación real,
     * que es un gradiente limpio de costa a cordillera, no se veía.
     */
    expect(HAZARD_MIN_G).toBeLessThanOrEqual(ARTEFACTO.min)
    expect(HAZARD_MAX_G).toBeGreaterThanOrEqual(ARTEFACTO.max)
  })

  it('no desperdicia rampa en un rango donde no hay dato', () => {
    // El error simétrico: un rango tan ancho que la distribución real ocupa un
    // cuarto de la rampa y el mapa vuelve a ser de un solo color, esta vez el
    // pálido. La distribución tiene que cubrir la mayor parte del recorrido.
    const cubierto = (ARTEFACTO.max - ARTEFACTO.min) / (HAZARD_MAX_G - HAZARD_MIN_G)
    expect(cubierto).toBeGreaterThan(0.85)
  })

  it('reparte paradas dentro del cuerpo de la distribución', () => {
    // Que los extremos encuadren no basta: si todas las paradas intermedias
    // caen fuera del rango intercuartílico, la mitad central del dato —donde
    // está la mayoría de las celdas— se pinta con una sola transición.
    for (const theme of ['light', 'dark'] as const) {
      const dentro = HAZARD_RAMP[theme].stops.filter(
        ([value]) => value > ARTEFACTO.p25 && value < ARTEFACTO.p75,
      )
      expect(dentro.length).toBeGreaterThanOrEqual(2)
    }
  })

  it('las paradas suben de forma estricta', () => {
    // `interpolate` exige entradas crecientes: una parada fuera de orden tira
    // el estilo entero al compilar.
    for (const theme of ['light', 'dark'] as const) {
      const values = HAZARD_RAMP[theme].stops.map(([value]) => value)
      expect(values).toEqual([...values].sort((a, b) => a - b))
      expect(new Set(values).size).toBe(values.length)
    }
  })

})

describe('visibilidad', () => {
  it('se apaga por `visibility`, nunca desmontando', () => {
    expect(hazardFieldLayer('dark', false).layout?.visibility).toBe('none')
    expect(hazardFieldLayer('dark', true).layout?.visibility).toBe('visible')
  })
})

describe('estilo sobre mapa oscuro', () => {
  it('mantiene la superficie translúcida en ambos temas', () => {
    for (const theme of ['light', 'dark'] as const) {
      const peak = atZoom(hazardFieldLayer(theme, true).paint?.['raster-opacity'], 7)
      expect(peak).toBeGreaterThan(0)
      // Por sobre ~0,5 la capa deja de ser contexto y empieza a tapar el mapa.
      expect(peak).toBeLessThanOrEqual(0.5)
    }
  })

  it('usa una rampa distinta por tema, no la misma con otra opacidad', () => {
    const light = HAZARD_RAMP.light.stops.map(([, c]) => c)
    const dark = HAZARD_RAMP.dark.stops.map(([, c]) => c)
    expect(light).not.toEqual(dark)
  })

  it('invierte la dirección de luminosidad según el tema', () => {
    // Claro: más amenaza = más oscuro. Oscuro: más amenaza = más brillante.
    const luminance = (hex: string) => {
      const v = hex.replace('#', '')
      return [0, 2, 4].reduce((sum, i) => sum + parseInt(v.slice(i, i + 2), 16), 0)
    }

    for (const theme of ['light', 'dark'] as const) {
      const stops = HAZARD_RAMP[theme].stops.map(([, c]) => luminance(c))
      const monotona = stops.every((value, i) =>
        i === 0 ? true : theme === 'dark' ? value > stops[i - 1]! : value < stops[i - 1]!,
      )
      // Monótona y no sólo «el primero contra el último»: un rebote a mitad de
      // la rampa hace que dos valores distintos se pinten del mismo tono.
      expect(monotona).toBe(true)
    }
  })

  it('no invade la paleta cálida de las emergencias', () => {
    const emergencyHexes = ['#dc2626', '#eab308', '#ea580c', '#b91c1c', '#f97316', '#991b1b']
    const hazardHexes = [...HAZARD_RAMP.light.stops, ...HAZARD_RAMP.dark.stops].map(
      ([, c]) => String(c).toLowerCase(),
    )

    for (const hex of emergencyHexes) expect(hazardHexes).not.toContain(hex)
  })

  it('se mantiene en la familia violeta en toda la rampa', () => {
    /*
     * El rojo y el naranja son de las emergencias; el cian, de la lluvia. Una
     * parada que se saliera de la familia haría que esta capa —que afirma
     * amenaza ESPERADA— se leyera como algo en curso.
     */
    for (const theme of ['light', 'dark'] as const) {
      for (const [, hex] of HAZARD_RAMP[theme].stops) {
        const v = String(hex).replace('#', '')
        const [r, g, b] = [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16)) as [
          number,
          number,
          number,
        ]
        // Violeta: el azul manda sobre el verde y el rojo no lo supera.
        expect(b).toBeGreaterThanOrEqual(g)
        expect(r).toBeLessThanOrEqual(b)
      }
    }
  })
})
