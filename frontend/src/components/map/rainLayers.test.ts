// @vitest-environment node
/**
 * Tests de la capa de lluvia: el campo (imagen), la etiqueta de riesgo y el
 * texto por comuna.
 *
 * Lo que se cuida acá no produce errores en consola si se rompe:
 *
 *   1. **El anclaje.** Si `RAIN_BEFORE_ID` deja de coincidir con una capa real,
 *      MapLibre descarta la capa entera; y si el ancla está en el sitio
 *      equivocado, la lluvia tapa los pines de emergencia.
 *   2. **El booleano estricto.** Si `riesgo_inundacion` llegara como la cadena
 *      `"true"`, el filtro de la etiqueta no encontraría nada.
 */

import { describe, expect, it } from 'vitest'
import {
  IS_FLOOD_RISK,
  RAIN_BEFORE_ID,
  RAIN_FIELD_LAYER_ID,
  RAIN_LAYER_IDS,
  RAIN_RISK_LABEL_LAYER_ID,
  RAIN_TEXT_LAYER_ID,
  rainFieldLayer,
  rainRiskLabelLayer,
  rainTextLayer,
} from './rainLayers'
import { coneFillLayer } from './overlayLayers'
import { alertHaloLayer, coreLayer } from './incidentLayers'
import {
  RAIN_FIELD_OPACITY,
  RAIN_SCALE,
  RAIN_TEXT,
  RAIN_TEXT_FADE,
  RAIN_TEXT_MIN_ZOOM,
  rainColorAt,
  rainScaleGradient,
  rainScalePosition,
} from '@/domain/rainSymbology'

describe('jerarquía de dibujo', () => {
  it('se ancla a una capa que existe de verdad', () => {
    expect(RAIN_BEFORE_ID).toBe(coneFillLayer.id)
  })

  it('no se ancla a una capa de incidentes: la lluvia va estrictamente por debajo', () => {
    expect(RAIN_BEFORE_ID).not.toBe(alertHaloLayer.id)
    expect(RAIN_BEFORE_ID).not.toBe(coreLayer.id)
  })

  it('declara sus capas en orden de dibujo: campo, etiqueta, texto', () => {
    expect([...RAIN_LAYER_IDS]).toEqual([
      RAIN_FIELD_LAYER_ID,
      RAIN_RISK_LABEL_LAYER_ID,
      RAIN_TEXT_LAYER_ID,
    ])
    expect(RAIN_LAYER_IDS).not.toContain(RAIN_BEFORE_ID)
  })
})

describe('el campo', () => {
  it('es una imagen raster suavizada, no un heatmap ni círculos', () => {
    const layer = rainFieldLayer('dark', true)
    expect(layer.type).toBe('raster')
    expect(layer.paint?.['raster-resampling']).toBe('linear')
    expect(layer.paint?.['raster-opacity']).toBe(RAIN_FIELD_OPACITY.dark)
  })

  it('se apaga por `visibility`', () => {
    expect(rainFieldLayer('light', false).layout?.visibility).toBe('none')
    expect(rainFieldLayer('light', true).layout?.visibility).toBe('visible')
  })
})

describe('la escala (tipo Windy)', () => {
  it('bajo 0,2 mm/h no pinta nada, y sin dato tampoco', () => {
    expect(rainColorAt(0.1)[3]).toBe(0)
    expect(rainColorAt(0)[3]).toBe(0)
    expect(rainColorAt(null)[3]).toBe(0)
  })

  it('más lluvia, más opaca', () => {
    const alphas = [0.5, 1, 2, 4, 7, 10].map((mm) => rainColorAt(mm)[3])
    expect([...alphas].sort((a, b) => a - b)).toEqual(alphas)
  })

  it('interpola entre escalones', () => {
    const [r] = rainColorAt(1.5)
    const one = rainColorAt(1)[0]
    const two = rainColorAt(2)[0]
    expect(r).toBeGreaterThan(Math.min(one, two))
    expect(r).toBeLessThan(Math.max(one, two))
  })

  it('el rojo aparece recién en la lluvia fuerte', () => {
    // Rojo dominante (r alto, g bajo) sólo desde 10 mm/h: bajo eso, la lluvia
    // no se confunde con un incendio.
    const redish = (mm: number) => {
      const [r, g] = rainColorAt(mm)
      return r > 200 && g < 60
    }
    expect(redish(10)).toBe(true)
    expect([0.5, 1, 2, 4, 7].some(redish)).toBe(false)
  })

  it('satura arriba: nada desaparece por salirse de la escala', () => {
    expect(rainColorAt(80)).toEqual(RAIN_SCALE[RAIN_SCALE.length - 1]!.rgba)
  })

  it('la tira del widget va de 0 a 100 % en escala logarítmica', () => {
    expect(rainScalePosition(RAIN_SCALE[1]!.mm)).toBeCloseTo(0)
    expect(rainScalePosition(RAIN_SCALE[RAIN_SCALE.length - 1]!.mm)).toBeCloseTo(100)
    expect(rainScaleGradient()).toMatch(/^linear-gradient\(to right, rgb\(/)
  })
})

describe('etiqueta de riesgo de anegamiento', () => {
  const layer = rainRiskLabelLayer('dark', true)

  it('filtra por el booleano estricto', () => {
    expect(IS_FLOOD_RISK).toEqual(['==', ['get', 'riesgo_inundacion'], true])
    expect(layer.filter).toEqual(IS_FLOOD_RISK)
  })

  it('va a escala regional y la releva el texto por comuna', () => {
    expect(layer.maxzoom).toBe(RAIN_TEXT_MIN_ZOOM)
    expect(rainTextLayer('dark', true).minzoom).toBe(RAIN_TEXT_MIN_ZOOM)
  })

  it('dice qué es, con el nombre de la comuna', () => {
    const field = JSON.stringify(layer.layout?.['text-field'])
    expect(field).toContain('"comuna"')
    expect(field).toContain('riesgo de anegamiento')
  })

  it('usa el color de riesgo del texto, con halo', () => {
    expect(layer.paint?.['text-color']).toBe(RAIN_TEXT.dark.colorRisk)
    expect(layer.paint?.['text-halo-color']).toBe(RAIN_TEXT.dark.halo)
  })

  it('se apaga por `visibility`', () => {
    expect(rainRiskLabelLayer('light', false).layout?.visibility).toBe('none')
  })
})

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
