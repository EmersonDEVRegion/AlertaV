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
  HAZARD_CELL_SOURCE_ID,
  HAZARD_FILL_LAYER_ID,
  HAZARD_LAYER_IDS,
  HAZARD_LINE_LAYER_ID,
  hazardFillLayer,
  hazardLineLayer,
} from './hazardLayers'
import { coneFillLayer, reachFillLayer } from './overlayLayers'
import { alertHaloLayer } from './incidentLayers'
import { RAIN_HEAT_LAYER_ID, RAIN_LAYER_IDS } from './rainLayers'
import {
  HAZARD_MAX_G,
  HAZARD_MIN_G,
  HAZARD_RAMP,
  HAZARD_RETICULE,
  HAZARD_VARIABLE,
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

  it('declara sus dos capas en orden de dibujo', () => {
    // La superficie debajo, la retícula encima: al revés la retícula quedaría
    // tapada por el relleno y no se vería nunca.
    expect([...HAZARD_LAYER_IDS]).toEqual([HAZARD_FILL_LAYER_ID, HAZARD_LINE_LAYER_ID])
  })

  it('la capa MÁS BAJA de la lluvia es el ancla candidata', () => {
    // `SeismicHazardLayer` prefiere anclarse bajo la lluvia cuando está
    // montada, y tiene que ser bajo su capa más baja: anclarse a una intermedia
    // dejaría el modelo estático encima de parte del pronóstico.
    expect(RAIN_LAYER_IDS[0]).toBe(RAIN_HEAT_LAYER_ID)
  })
})

describe('una sola representación', () => {
  it('no queda ninguna capa de tipo heatmap', () => {
    /*
     * La regresión que este bloque vigila. Un `heatmap` sobre una grilla
     * REGULAR no mide el valor del modelo: mide densidad, que por construcción
     * es constante, así que lo único que puede hacer el kernel es sumar el
     * valor de los vecinos. Un nodo moderado rodeado de nodos altos salía más
     * caliente que su propio PGA.
     */
    for (const factory of [hazardFillLayer, hazardLineLayer]) {
      expect(factory('dark', true).type).not.toBe('heatmap')
    }
  })

  it('las dos capas viven en la MISMA fuente de polígonos', () => {
    // Había una segunda fuente de puntos derivada de los centros de celda, sólo
    // para alimentar el heatmap. Se fue con él: una descarga, un parseo, una
    // geometría subida al worker.
    expect(HAZARD_CELL_SOURCE_ID).toBe('seismic-hazard')
  })

  it('la superficie está pintada en TODO el rango de zoom', () => {
    /*
     * Antes el relleno entraba desvaneciéndose desde 0 a z10,5 porque hasta ahí
     * mandaba el mapa de calor. Sin esa segunda capa, cualquier tramo con
     * opacidad 0 sería un rango de zoom en el que la capa encendida no se ve —y
     * eso se lee como «se apagó sola», que es un reporte de bug garantizado.
     */
    for (const theme of ['light', 'dark'] as const) {
      const opacity = hazardFillLayer(theme, true).paint?.['fill-opacity']
      for (const zoom of [7, 9, 10.5, 12, 14, 17]) {
        expect(atZoom(opacity, zoom)).toBeGreaterThan(0.2)
      }
    }
  })

  it('la superficie se aligera al acercarse, no al revés', () => {
    // De lejos la capa es el sujeto; de cerca la pregunta vuelve a ser dónde
    // está uno, y un velo del 40 % sobre los nombres de calle convierte una
    // capa de contexto en un estorbo.
    for (const theme of ['light', 'dark'] as const) {
      const opacity = hazardFillLayer(theme, true).paint?.['fill-opacity']
      expect(atZoom(opacity, 15)).toBeLessThan(atZoom(opacity, 8))
    }
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

  it('interpola en un espacio perceptual y no en sRGB', () => {
    // Entre dos violetas, la mezcla en sRGB hunde el punto medio y sobre una
    // teselación de celdas eso se ve como una banda que no está en el dato.
    const expr = hazardFillLayer('dark', true).paint?.['fill-color'] as unknown[]
    expect(expr[0]).toBe('interpolate-lab')
  })

  it('colorea por la variable documentada del artefacto', () => {
    const expr = JSON.stringify(hazardFillLayer('dark', true).paint?.['fill-color'])
    expect(expr).toContain(HAZARD_VARIABLE)
  })

  it('el color NO depende del zoom', () => {
    // El valor de una celda es el mismo mire uno de cerca o de lejos. Atarlo al
    // zoom haría que la misma celda afirmara dos amenazas distintas.
    expect(countZoom(hazardFillLayer('dark', true).paint?.['fill-color'])).toBe(0)
  })
})

describe('la retícula', () => {
  const [hidden, shown] = HAZARD_RETICULE

  it('está apagada a escala regional', () => {
    /*
     * Es la mitad visible del problema que motivó la reescritura: dibujar un
     * trazo alrededor de cada rectángulo de 20 px es exactamente cómo una
     * superficie continua se convierte en una cuadrícula.
     */
    const opacity = hazardLineLayer('dark', true).paint?.['line-opacity']
    for (const zoom of [7, 9, 11, 12]) {
      expect(atZoom(opacity, zoom)).toBe(0)
    }
  })

  it('aparece sólo cuando una celda ya ocupa buena parte de la pantalla', () => {
    // Una celda mide ~5 km: por encima de z13 llena el ancho del teléfono, y
    // ahí el borde deja de ser ruido y pasa a decir la resolución del modelo.
    expect(hidden).toBeGreaterThan(12)
    expect(shown).toBeGreaterThan(hidden)

    const opacity = hazardLineLayer('dark', true).paint?.['line-opacity']
    expect(atZoom(opacity, shown)).toBeGreaterThan(0)
  })

  it('nunca compite con la superficie', () => {
    for (const theme of ['light', 'dark'] as const) {
      const opacity = hazardLineLayer(theme, true).paint?.['line-opacity']
      const fill = hazardFillLayer(theme, true).paint?.['fill-opacity']
      expect(atZoom(opacity, 17)).toBeLessThan(atZoom(fill, 17))
    }
  })

  it('el zoom es la raíz de cada opacidad, nunca anidado', () => {
    // `["zoom"]` sólo puede ser la entrada de un `interpolate` de nivel
    // superior. Anidarlo tira el estilo completo — el error que este
    // repositorio ya pagó dos veces.
    for (const paint of [
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
    for (const factory of [hazardFillLayer, hazardLineLayer]) {
      expect(factory('dark', false).layout?.visibility).toBe('none')
      expect(factory('dark', true).layout?.visibility).toBe('visible')
    }
  })
})

describe('estilo sobre mapa oscuro', () => {
  it('mantiene el relleno translúcido en ambos temas', () => {
    for (const theme of ['light', 'dark'] as const) {
      const peak = atZoom(hazardFillLayer(theme, true).paint?.['fill-opacity'], 7)
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

  it('desactiva el antialias del relleno para no dibujar costuras', () => {
    expect(hazardFillLayer('dark', true).paint?.['fill-antialias']).toBe(false)
  })
})
