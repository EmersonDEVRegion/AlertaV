// @vitest-environment node
/**
 * Normalización de la capa de cortes.
 *
 * Todo este archivo existe por una sola línea que NO está en el código:
 *
 *     const severidad = props.severidad ?? 0
 *
 * Es la forma obvia de leer el campo y es una mentira. Un aviso del MTT no
 * tiene escala publicada; pintarlo como severidad 0 lo declara «la emergencia
 * más leve del MOP», que es una afirmación que nadie midió y que empuja hacia
 * «no pasa nada». En una capa que alguien mira para decidir si sale de casa,
 * ese error no es cosmético.
 *
 * El fallo, además, sería **mudo**: no hay excepción, no hay aviso en consola y
 * el mapa se ve perfectamente bien. Sólo está diciendo algo falso.
 */

import { describe, expect, it, vi } from 'vitest'
import { countCutRoutes, parseRoadClosures } from './roadClosures'
import type { RoadClosureCollection } from './roadClosureTypes'

function collection(...properties: Record<string, unknown>[]): RoadClosureCollection {
  return {
    type: 'FeatureCollection',
    features: properties.map((props, index) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [-71.5, -33.0] },
      properties: {
        public_id: `id-${index}`,
        timestamp: '2026-09-01T12:00:00+00:00',
        type: 'road_closure',
        confidence: 0,
        text: 'Emergencia vial',
        commune: 'Valparaíso',
        is_confirmed_incident: false,
        ...props,
      },
    })),
  } as unknown as RoadClosureCollection
}

const props = (parsed: RoadClosureCollection, index = 0) =>
  parsed.features[index]!.properties as unknown as Record<string, unknown>

describe('la severidad ausente no se inventa', () => {
  it('un aviso del MTT queda SIN la clave, no con un cero', () => {
    const parsed = parseRoadClosures(collection({ source: 'transporte_informa' }))

    // Las dos mitades de la invariante. `in` es lo que ve MapLibre a través de
    // `["has","severidad"]`; el valor nulo es lo que ve TypeScript.
    expect('severidad' in props(parsed)).toBe(false)
    expect(props(parsed)['severidad']).toBeUndefined()
  })

  it('un `severidad: null` explícito también se borra', () => {
    // El backend omite la clave, pero una plantilla vieja o un proxy podrían
    // mandarla en null. `has` respondería `true` y el aviso caería en la rampa
    // del MOP como gravedad mínima: justo lo que hay que evitar.
    const parsed = parseRoadClosures(collection({ source: 'mtt', severidad: null }))
    expect('severidad' in props(parsed)).toBe(false)
  })

  it('un cero REAL del MOP sí se conserva', () => {
    // La otra cara: `0` es un valor legítimo y falsy. Un `if (severidad)` lo
    // descartaría y convertiría una emergencia medida en un aviso sin escala.
    const parsed = parseRoadClosures(collection({ source: 'mop', severidad: 0 }))

    expect('severidad' in props(parsed)).toBe(true)
    expect(props(parsed)['severidad']).toBe(0)
  })
})

describe('valores fuera de contrato', () => {
  it('una severidad no numérica se descarta y se avisa por consola', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const parsed = parseRoadClosures(collection({ severidad: 'alta' }))

    expect('severidad' in props(parsed)).toBe(false)
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('se recorta al rango del contrato en vez de saturar en silencio', () => {
    // `severity_rank` no puede salir de 0..5 por construcción. Un valor fuera
    // de rango significa que el contrato cambió; el recorte deja el mapa
    // coherente mientras tanto.
    expect(props(parseRoadClosures(collection({ severidad: 9 })))['severidad']).toBe(5)
    expect(props(parseRoadClosures(collection({ severidad: -3 })))['severidad']).toBe(0)
  })

  it('una colección sin features no revienta', () => {
    // Ninguna ruta cortada es una respuesta CORRECTA y frecuente.
    expect(parseRoadClosures({ type: 'FeatureCollection' } as RoadClosureCollection)).toEqual({
      type: 'FeatureCollection',
      features: [],
    })
  })
})

describe('conteo de rutas cortadas', () => {
  it('cuenta sólo las intransitables, no todo lo vigente', () => {
    const parsed = parseRoadClosures(
      collection(
        { severidad: 5 },
        { severidad: 4 },
        { severidad: 3 },
        { severidad: 0 },
        { source: 'transporte_informa' },
      ),
    )

    expect(parsed.features).toHaveLength(5)
    // El subtítulo del panel dice «N rutas cortadas · M vigentes», y esa
    // distinción es lo único accionable de la capa: una repavimentación
    // programada no es un puente caído.
    expect(countCutRoutes(parsed)).toBe(2)
  })

  it('un lote entero del MTT no suma ninguna ruta cortada', () => {
    const parsed = parseRoadClosures(
      collection({ source: 'transporte_informa' }, { source: 'transporte_informa' }),
    )
    expect(countCutRoutes(parsed)).toBe(0)
  })
})
