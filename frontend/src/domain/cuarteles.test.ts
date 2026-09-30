import { describe, expect, it } from 'vitest'
import { CuartelesLoadError, parseCuarteles } from '@/api/cuarteles'
import { cuartelLabelLayer, cuartelPointLayer, CUARTELES_BEFORE_ID } from '@/components/map/cuartelesLayers'
import { cuartelesCercanos, formatDistancia } from './cuarteles'

const FC = {
  type: 'FeatureCollection',
  metadata: { fuente: 'SIG Bomberos de Chile (sig.bomberos.cl)', generado: '2026-09-30T14:46:23+00:00' },
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [-71.437316, -33.064228] },
      properties: { tipo: 'compania', nombre: 'Quinta de Quilpué', cuerpo: 'Quilpué', numero: 5, direccion: 'Colina de Oro 2155', comuna: 'Quilpué', telefono: '322910310' },
    },
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [-71.442837, -33.050071] },
      properties: { tipo: 'cuerpo', nombre: 'Cuerpo de Bomberos de Quilpué', cuerpo: 'Quilpué', numero: null, direccion: null, comuna: 'Quilpué' },
    },
    { type: 'Feature', geometry: { type: 'Point', coordinates: ['x', 1] }, properties: { nombre: 'roto', cuerpo: 'X' } },
  ],
}

describe('parseCuarteles', () => {
  it('descarta lo que no se puede dibujar y no arrastra el teléfono', () => {
    const datos = parseCuarteles(FC)
    expect(datos.cuarteles).toHaveLength(2)
    expect(datos.collection.features[0]!.properties).not.toHaveProperty('telefono')
    expect(datos.cuarteles[1]!.tipo).toBe('cuerpo')
  })

  it('una colección vacía es un error, no «no hay cuarteles»', () => {
    expect(() => parseCuarteles({ type: 'FeatureCollection', features: [] })).toThrow(CuartelesLoadError)
  })
})

describe('cercanía', () => {
  it('ordena por distancia en línea recta', () => {
    const { cuarteles } = parseCuarteles(FC)
    const cerca = cuartelesCercanos(cuarteles, -33.047, -71.442, 3)
    expect(cerca.map((c) => c.cuartel.nombre)).toEqual([
      'Cuerpo de Bomberos de Quilpué',
      'Quinta de Quilpué',
    ])
    expect(cerca[0]!.km).toBeLessThan(cerca[1]!.km)
  })

  it('formatea metros y kilómetros', () => {
    expect(formatDistancia(0.354)).toBe('350 m')
    expect(formatDistancia(1.26)).toMatch(/^1,3 km$/)
  })
})

describe('capas', () => {
  it('son de contexto: se anclan bajo las emergencias y aparecen al acercar', () => {
    expect(CUARTELES_BEFORE_ID).toBe('wind-cone-fill')
    expect(cuartelPointLayer('light', true).minzoom).toBe(10)
    expect(cuartelLabelLayer('dark', false).layout?.visibility).toBe('none')
  })
})
