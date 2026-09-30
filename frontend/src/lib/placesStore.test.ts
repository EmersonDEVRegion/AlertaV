import { afterEach, describe, expect, it } from 'vitest'
import { MAX_PLACES, addPlace, getPlaces, removePlace, resetPlaces } from './placesStore'

afterEach(() => {
  localStorage.removeItem('alertav:lugares')
  resetPlaces()
})

describe('lugares guardados', () => {
  it('se guardan en este dispositivo y sobreviven a una recarga', () => {
    const casa = addPlace('  Casa ', -33.046, -71.401)
    expect(casa).toMatchObject({ name: 'Casa', lat: -33.046, lon: -71.401 })

    resetPlaces() // lo que haría una recarga: vuelve a leer localStorage
    expect(getPlaces().map((p) => p.name)).toEqual(['Casa'])
  })

  it(`hasta ${MAX_PLACES}`, () => {
    for (let i = 0; i < MAX_PLACES; i += 1) expect(addPlace(`L${i}`, -33, -71)).not.toBeNull()
    expect(addPlace('Uno más', -33, -71)).toBeNull()
    expect(getPlaces()).toHaveLength(MAX_PLACES)
  })

  it('borrar uno no toca los otros', () => {
    const casa = addPlace('Casa', -33, -71)!
    addPlace('Trabajo', -33.1, -71.1)
    removePlace(casa.id)
    expect(getPlaces().map((p) => p.name)).toEqual(['Trabajo'])
  })

  it('ignora basura en el almacenamiento', () => {
    localStorage.setItem(
      'alertav:lugares',
      JSON.stringify([{ id: 'x', name: 'Casa', lat: 'no', lon: 1 }, { id: 'y', name: 'Ok', lat: -33, lon: -71 }]),
    )
    resetPlaces()
    expect(getPlaces().map((p) => p.name)).toEqual(['Ok'])
    localStorage.setItem('alertav:lugares', 'no-es-json')
    resetPlaces()
    expect(getPlaces()).toEqual([])
  })
})
