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
  RAIN_NUCLEUS_LAYER_ID,
  RAIN_RISK_RING_LAYER_ID,
  RAIN_TEXT_LAYER_ID,
  rainCoreLayer,
  rainHaloLayer,
  rainNucleusLayer,
  rainRiskRingLayer,
  rainTextLayer,
} from './rainLayers'
import { coneFillLayer } from './overlayLayers'
import { alertHaloLayer, coreLayer } from './incidentLayers'
import {
  RAIN_MM_MAX,
  RAIN_MM_MIN,
  RAIN_PALETTE,
  RAIN_TEXT,
  RAIN_TEXT_FADE,
  RAIN_TEXT_MIN_ZOOM,
} from '@/domain/rainSymbology'

/**
 * Sólo las de tipo `circle`. `rain-text` es de símbolo: no tiene
 * `circle-radius` y los bloques que iteran sobre el radio no le aplican.
 */
const ALL = [rainHaloLayer, rainCoreLayer, rainNucleusLayer, rainRiskRingLayer] as const

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

  it('declara sus cinco capas en orden de dibujo', () => {
    // Orden de dibujo, de fuera hacia dentro: halo difuso, cuerpo, núcleo, el
    // anillo de riesgo, y el texto encima de todas ellas.
    expect([...RAIN_LAYER_IDS]).toEqual([
      RAIN_HALO_LAYER_ID,
      RAIN_CORE_LAYER_ID,
      RAIN_NUCLEUS_LAYER_ID,
      RAIN_RISK_RING_LAYER_ID,
      RAIN_TEXT_LAYER_ID,
    ])
  })

  it('el texto va el último: encima de sus manchas, debajo del ancla', () => {
    // `RainLayer.tsx` monta en este orden y todas comparten `beforeId`, así que
    // el último montado queda inmediatamente debajo del cono. Si alguien mueve
    // `rain-text` en el arreglo, el texto se iría bajo sus propios discos.
    expect(RAIN_LAYER_IDS[RAIN_LAYER_IDS.length - 1]).toBe(RAIN_TEXT_LAYER_ID)
  })

  it('el re-anclaje tras cambiar de tema también cubre el texto', () => {
    // `RAIN_LAYER_IDS` es lo que recorre el efecto de `RainLayer.tsx` tras un
    // `setStyle`. Una capa fuera de la lista podría quedar por encima del cono
    // —y por tanto de los pines— sin que nadie se entere.
    expect(RAIN_LAYER_IDS).toContain(RAIN_TEXT_LAYER_ID)
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

  it('el anillo ya no anima: sin escrituras de pintura por frame', () => {
    const paint = rainRiskRingLayer('dark', true).paint

    // La transición en 0 existía sólo para que el pulso no encolara una
    // interpolación por escritura. Sin pulso, sobra.
    expect(paint?.['circle-stroke-opacity-transition']).toBeUndefined()
    // Y la opacidad es un número fijo, no algo que alguien sobrescriba.
    expect(typeof paint?.['circle-stroke-opacity']).toBe('number')
  })

  it('compensa la falta de movimiento con un trazo que crece con el zoom', () => {
    const width = rainRiskRingLayer('dark', true).paint?.['circle-stroke-width']
    expect(JSON.stringify(width)).toContain('interpolate')
    expect(JSON.stringify(width)).toContain('zoom')
  })

  it('apila tres discos para simular el degradado radial', () => {
    // MapLibre no tiene degradados en `circle`; el apilado los aproxima.
    const halo = rainHaloLayer('dark', true).paint
    const core = rainCoreLayer('dark', true).paint
    const nucleus = rainNucleusLayer('dark', true).paint

    // Cada escalón hacia dentro es menos difuso.
    expect(halo?.['circle-blur'] as number).toBeGreaterThan(core?.['circle-blur'] as number)
    expect(core?.['circle-blur'] as number).toBeGreaterThan(nucleus?.['circle-blur'] as number)
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

  it('el núcleo NO repite el color del cuerpo: la rampa avanza hacia dentro', () => {
    for (const theme of ['light', 'dark'] as const) {
      const core = JSON.stringify(rainCoreLayer(theme, true).paint?.['circle-color'])
      const nucleus = JSON.stringify(rainNucleusLayer(theme, true).paint?.['circle-color'])
      // Con el mismo color en los tres discos la pila sólo cambia de opacidad y
      // se lee como una mancha plana, no como una celda de radar.
      expect(nucleus).not.toBe(core)
      expect(nucleus).toContain(RAIN_PALETTE[theme].nucleus)
      expect(nucleus).toContain(RAIN_PALETTE[theme].nucleusRisk)
    }
  })

  it('el núcleo corre hacia el extremo caliente en la dirección del tema', () => {
    const luminance = (hex: string) => {
      const value = hex.replace('#', '')
      return [0, 2, 4].reduce((sum, i) => sum + parseInt(value.slice(i, i + 2), 16), 0)
    }
    // Oscuro: hacia dentro = más brillante. Claro: hacia dentro = más oscuro.
    expect(luminance(RAIN_PALETTE.dark.nucleus)).toBeGreaterThan(
      luminance(RAIN_PALETTE.dark.rain),
    )
    expect(luminance(RAIN_PALETTE.light.nucleus)).toBeLessThan(luminance(RAIN_PALETTE.light.rain))
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

/**
 * La capa de texto.
 *
 * Todo lo de acá falla en silencio en el navegador: un nombre de propiedad que
 * no existe se dibuja como una línea vacía, un `minzoom` que falta cuesta
 * rendimiento sin avisar, y un `text-field` que leyera `inicio` en crudo
 * mostraría UTC — la hora equivocada, sin ningún error.
 */
describe('capa de texto: el pronóstico sin clic', () => {
  const layer = rainTextLayer('dark', true)
  const field = JSON.stringify(layer.layout?.['text-field'])

  it('es de tipo símbolo y se llama como el resto de la capa', () => {
    expect(layer.type).toBe('symbol')
    expect(layer.id).toBe(RAIN_TEXT_LAYER_ID)
  })

  it('lee los nombres REALES del contrato, no los del borrador', () => {
    // El encargo hablaba de `probabilidad`, `mm`, `hora_inicio` y `hora_fin`.
    // El backend emite otros. `["get"]` sobre una propiedad inexistente
    // devuelve null y MapLibre dibuja la línea vacía: cero errores en consola y
    // un bloque de texto a medias sobre el mapa.
    expect(field).toContain('probabilidad_max')
    expect(field).toContain('mm_total')
    expect(field).toContain('comuna')

    for (const invented of ['"probabilidad"', '"mm"', 'hora_inicio', 'hora_fin']) {
      expect(field).not.toContain(invented)
    }
  })

  it('la ventana horaria sale de la propiedad derivada, nunca de la marca ISO', () => {
    // `inicio`/`fin` vienen en UTC y MapLibre no sabe de zonas horarias. Un
    // `slice` sobre la cadena mostraría la hora de Chile desplazada 3 o 4 h.
    expect(field).toContain('ventana')
    expect(field).not.toContain('slice')
    expect(field).not.toContain('"inicio"')
    expect(field).not.toContain('"fin"')
  })

  it('no escribe un 0 % cuando el modelo no publica la probabilidad', () => {
    // `probabilidad_max` es legítimamente nulo en algunos modelos, y
    // `number-format` sobre nulo escribiría "0 %". Inventar un cero sería
    // tranquilizar con un dato que nadie midió.
    expect(field).toContain('to-string')
    expect(field.indexOf('case')).toBeLessThan(field.indexOf('probabilidad_max'))
  })

  it('el milimetraje es el acumulado, no la punta horaria', () => {
    // `mm_hora_max` alimenta el radio y el flag; mostrarlo como "esperado"
    // multiplicaría la cifra en una lluvia larga y suave.
    expect(field).toContain('mm_total')
    expect(field).not.toContain('mm_hora_max')
  })

  it('se corta por minzoom, no sólo por opacidad', () => {
    // Con `text-opacity: 0` la capa sigue reservando espacio en el cálculo de
    // colisiones y desplazaría los topónimos del basemap sin verse.
    expect(layer.minzoom).toBe(RAIN_TEXT_MIN_ZOOM)
  })

  it('aparece con un desvanecido, no de golpe', () => {
    const [from, to] = RAIN_TEXT_FADE
    expect(from).toBeGreaterThanOrEqual(RAIN_TEXT_MIN_ZOOM)
    expect(to).toBeGreaterThan(from)
    expect(layer.paint?.['text-opacity']).toEqual([
      'interpolate',
      ['linear'],
      ['zoom'],
      from,
      0,
      to,
      1,
    ])
  })

  it('lleva halo: es lo único que garantiza la legibilidad', () => {
    for (const theme of ['light', 'dark'] as const) {
      const paint = rainTextLayer(theme, true).paint
      expect(paint?.['text-halo-color']).toBe(RAIN_TEXT[theme].halo)
      expect(paint?.['text-halo-width'] as number).toBeGreaterThan(0)
      // MapLibre satura el halo a 1/4 del tamaño de fuente. Con el texto más
      // pequeño en 11 px, el techo real son 2,75.
      expect(paint?.['text-halo-width'] as number).toBeLessThanOrEqual(2.75)
    }
  })

  it('el halo contrasta con el texto en los dos temas', () => {
    const luminance = (hex: string) => {
      const value = hex.replace('#', '')
      return [0, 2, 4].reduce((sum, i) => sum + parseInt(value.slice(i, i + 2), 16), 0)
    }
    // Texto claro sobre halo oscuro y viceversa. El fondo real bajo cada letra
    // depende de la intensidad de la comuna, así que el contraste tiene que
    // fabricarlo el halo.
    expect(luminance(RAIN_TEXT.dark.color)).toBeGreaterThan(luminance(RAIN_TEXT.dark.halo))
    expect(luminance(RAIN_TEXT.light.color)).toBeLessThan(luminance(RAIN_TEXT.light.halo))
  })

  it('el riesgo gana la colisión cuando dos bloques se pisan', () => {
    // Orden ascendente de `symbol-sort-key`: 0 se coloca antes que 1. Sin esto
    // el desempate lo decidiría el orden de los features en el GeoJSON.
    expect(layer.layout?.['symbol-sort-key']).toEqual(['case', IS_FLOOD_RISK, 0, 1])
  })

  it('colisiona en vez de amontonarse', () => {
    expect(layer.layout?.['text-allow-overlap']).toBe(false)
    expect(layer.layout?.['text-ignore-placement']).toBe(false)
  })

  it('no fija una fuente que el endpoint de glifos podría no servir', () => {
    // Un `text-font` inexistente no rompe el estilo: no dibuja NINGUNA letra.
    // El defecto de MapLibre sí lo sirve CARTO en los dos basemaps.
    expect(layer.layout?.['text-font']).toBeUndefined()
  })

  it('se apaga por `visibility`, igual que las manchas', () => {
    expect(rainTextLayer('dark', false).layout?.visibility).toBe('none')
    expect(rainTextLayer('dark', true).layout?.visibility).toBe('visible')
  })

  it('el riesgo cambia el color del texto, no su tamaño ni su halo', () => {
    for (const theme of ['light', 'dark'] as const) {
      const spec = rainTextLayer(theme, true)
      // Una comuna en riesgo no asciende a categoría de emergencia: sigue
      // siendo un pronóstico y se lee igual de grande.
      expect(JSON.stringify(spec.layout?.['text-size'])).not.toContain('riesgo_inundacion')
      expect(spec.paint?.['text-halo-width']).toBe(RAIN_TEXT[theme].haloWidth)
      expect(JSON.stringify(spec.paint?.['text-color'])).toContain('riesgo_inundacion')
    }
  })
})
