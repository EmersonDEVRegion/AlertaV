// @vitest-environment node
/**
 * Tests del parseo de la capa meteorológica.
 *
 * Dos reglas del contrato que la UI no puede romper:
 *
 *   1. **Una colección vacía es una respuesta correcta.** Significa "ninguna
 *      comuna con lluvia pronosticada", no un fallo. Si el parseo devolviera
 *      `null` o lanzara, media temporada de verano se vería como un error.
 *   2. **`riesgo_inundacion` tiene que salir de acá como booleano real.** Las
 *      expresiones de MapLibre no comparan entre tipos: la cadena `"true"` haría
 *      que el anillo de riesgo desapareciera sin un solo mensaje en consola.
 */

import { describe, expect, it, vi } from 'vitest'
import { EMPTY_RAIN, countFloodRisk, parseRainCollection } from './rain'

/** Un feature como el que documenta `backend/docs/capa-meteorologica.md`. */
function makeFeature(over: Record<string, unknown> = {}) {
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [-71.6127, -33.0472] },
    properties: {
      public_id: '3f2b6c1e-0000-4000-8000-000000000001',
      comuna: 'Valparaíso',
      inicio: '2026-06-15T14:00:00+00:00',
      fin: '2026-06-16T14:00:00+00:00',
      ventana_horas: 24,
      mm_total: 23.1,
      mm_hora_max: 8.2,
      mm_3h_max: 18.6,
      hora_pico: '2026-06-15T16:00:00+00:00',
      probabilidad_max: 90,
      horas_con_lluvia: 6,
      riesgo_inundacion: true,
      nivel: 'riesgo_alto',
      motivos: 'intensidad 8.2 mm/h ≥ 5.0 mm/h; acumulado en 3 h 18.6 mm ≥ 15.0 mm',
      modelo: 'best_match',
      es_pronostico: true,
      is_confirmed_incident: false,
      ...over,
    },
  }
}

const collection = (...features: unknown[]) => ({ type: 'FeatureCollection', features })

describe('el estado "soleado"', () => {
  it('una colección vacía es una respuesta válida, no un error', () => {
    const parsed = parseRainCollection(collection())

    expect(parsed.type).toBe('FeatureCollection')
    expect(parsed.features).toHaveLength(0)
    expect(countFloodRisk(parsed)).toBe(0)
  })

  it('devuelve SIEMPRE la misma referencia cuando está vacía', () => {
    // `<Source data={...}>` vuelve a subir el GeoJSON al worker cada vez que
    // cambia la identidad del objeto. Un literal nuevo por render haría que el
    // estado más frecuente del año fuera el único que cuesta trabajo.
    expect(parseRainCollection(collection())).toBe(EMPTY_RAIN)
    expect(parseRainCollection({ type: 'FeatureCollection' })).toBe(EMPTY_RAIN)
    expect(parseRainCollection(null)).toBe(EMPTY_RAIN)
    expect(parseRainCollection('sin lluvia')).toBe(EMPTY_RAIN)
  })
})

describe('riesgo_inundacion como booleano estricto', () => {
  it('conserva el booleano real', () => {
    const [feature] = parseRainCollection(collection(makeFeature())).features
    expect(feature?.properties.riesgo_inundacion).toBe(true)
  })

  it('normaliza la cadena "true" y deja constancia', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const parsed = parseRainCollection(
      collection(makeFeature({ riesgo_inundacion: 'true' })),
    )

    expect(parsed.features[0]?.properties.riesgo_inundacion).toBe(true)
    // El aviso importa tanto como la corrección: un contrato que se repara en
    // silencio es una bomba de tiempo.
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('"false" NO se convierte en `true` por una coerción perezosa', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const parsed = parseRainCollection(
      collection(makeFeature({ riesgo_inundacion: 'false' })),
    )

    // `Boolean("false")` es `true`. Ese es el error caro: una alerta de
    // inundación inventada sobre una comuna que no la tiene.
    expect(parsed.features[0]?.properties.riesgo_inundacion).toBe(false)
    warn.mockRestore()
  })

  it('cuenta las comunas en riesgo', () => {
    const parsed = parseRainCollection(
      collection(
        makeFeature(),
        makeFeature({ public_id: 'b', comuna: 'Quillota', riesgo_inundacion: false }),
        makeFeature({ public_id: 'c', comuna: 'Limache' }),
      ),
    )
    expect(parsed.features).toHaveLength(3)
    expect(countFloodRisk(parsed)).toBe(2)
  })
})

describe('robustez del contrato', () => {
  it('descarta features sin coordenadas usables', () => {
    const parsed = parseRainCollection(
      collection(
        makeFeature(),
        { type: 'Feature', geometry: { type: 'Point', coordinates: [NaN, -33] }, properties: {} },
        { type: 'Feature', geometry: { type: 'Polygon', coordinates: [] }, properties: {} },
        { type: 'Feature', properties: {} },
      ),
    )
    // Un `NaN` en las coordenadas no rompe MapLibre: dibuja el punto en
    // cualquier parte, que es peor que no dibujarlo.
    expect(parsed.features).toHaveLength(1)
  })

  it('absorbe `motivos` si algún día llegara como lista', () => {
    const parsed = parseRainCollection(
      collection(makeFeature({ motivos: ['intensidad 8.2 mm/h', 'acumulado 18.6 mm'] })),
    )
    expect(parsed.features[0]?.properties.motivos).toBe(
      'intensidad 8.2 mm/h; acumulado 18.6 mm',
    )
  })

  it('acepta `probabilidad_max` nula sin descartar la comuna', () => {
    // No todos los modelos publican la variable, y la probabilidad NO filtra:
    // 20 mm/h con 30 % es justo el escenario que hay que mostrar.
    const parsed = parseRainCollection(collection(makeFeature({ probabilidad_max: null })))
    expect(parsed.features).toHaveLength(1)
    expect(parsed.features[0]?.properties.probabilidad_max).toBeNull()
  })

  it('cae a un nivel conocido si el vocabulario del backend crece', () => {
    const parsed = parseRainCollection(collection(makeFeature({ nivel: 'diluvio' })))
    // El color degrada; el riesgo lo sigue decidiendo el booleano, que no se toca.
    expect(parsed.features[0]?.properties.nivel).toBe('lluvia')
    expect(parsed.features[0]?.properties.riesgo_inundacion).toBe(true)
  })
})
