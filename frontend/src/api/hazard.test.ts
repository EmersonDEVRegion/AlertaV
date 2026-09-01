// @vitest-environment node
/**
 * Parseo del artefacto de amenaza.
 *
 * # Qué se fue de este archivo
 *
 * Un bloque entero probaba `toHazardNodes`, la derivación de puntos que
 * alimentaba el mapa de calor regional. Esa capa se retiró —el porqué está en
 * `domain/hazardSymbology.ts`— y con ella la derivación y sus tests.
 *
 * Lo que queda es lo que siempre importó más: que un artefacto que NO es el
 * esperado se rechace con un mensaje accionable, en vez de dibujar una capa
 * plausible y equivocada. Una colección vacía o mal formada no lanza nada por sí
 * sola en MapLibre: pinta un mapa limpio, y un mapa limpio sobre una de las
 * zonas sísmicas más activas del planeta es la peor mentira que esta capa puede
 * contar.
 */

import { describe, expect, it } from 'vitest'
import { HazardLoadError, parseHazardGrid } from './hazard'

/** Celda tal como la emite `scripts/fetch_seismic_hazard.py`. */
function cell(lon: number, lat: number, props: Record<string, unknown> = {}) {
  const d = 0.0225
  return {
    type: 'Feature' as const,
    geometry: {
      type: 'Polygon' as const,
      coordinates: [
        [
          [lon - d, lat - d],
          [lon + d, lat - d],
          [lon + d, lat + d],
          [lon - d, lat + d],
          [lon - d, lat - d],
        ],
      ],
    },
    properties: { lon, lat, pga_475: 0.35, ...props },
  }
}

describe('validación del artefacto', () => {
  it('acepta una colección bien formada', () => {
    const grid = parseHazardGrid({
      type: 'FeatureCollection',
      features: [cell(-71.5, -33), cell(-71.4, -33)],
      metadata: { model: 'MASCSN26', cell_size_deg: { lon: 0.045, lat: 0.045 } },
    })

    expect(grid.cells.features).toHaveLength(2)
    expect(grid.cellSizeDeg).toBeCloseTo(0.045, 6)
    expect(grid.metadata?.model).toBe('MASCSN26')
  })

  it('conserva el centro del nodo en las propiedades', () => {
    // Ninguna capa lo pinta hoy, pero es lo que permite consultar el valor bajo
    // el cursor y volver a cruzar con el CSV del CSN sin recalcular centroides.
    const grid = parseHazardGrid({
      type: 'FeatureCollection',
      features: [cell(-71.5, -33.05)],
    })

    expect(grid.cells.features[0]!.properties.lon).toBe(-71.5)
    expect(grid.cells.features[0]!.properties.lat).toBe(-33.05)
  })

  it('rechaza una colección VACÍA, al revés que la lluvia', () => {
    /*
     * En la lluvia, cero comunas significa "no llueve" y es la respuesta normal
     * durante meses. Acá el modelo es estático y cubre la región entera: cero
     * celdas sólo puede significar que el artefacto no se generó. Tratarlo como
     * "sin amenaza" pintaría un mapa limpio sobre una de las zonas sísmicas más
     * activas del planeta.
     */
    expect(() => parseHazardGrid({ type: 'FeatureCollection', features: [] })).toThrow(
      HazardLoadError,
    )
  })

  it('el mensaje del artefacto vacío dice qué hacer', () => {
    try {
      parseHazardGrid({ type: 'FeatureCollection', features: [] })
      expect.unreachable('debió lanzar')
    } catch (error) {
      expect((error as Error).message).toContain('fetch_seismic_hazard')
    }
  })

  it('rechaza lo que no es un FeatureCollection', () => {
    expect(() => parseHazardGrid({ type: 'Feature' })).toThrow(HazardLoadError)
    expect(() => parseHazardGrid(null)).toThrow(HazardLoadError)
    expect(() => parseHazardGrid('<html>404</html>')).toThrow(HazardLoadError)
  })

  it('tolera un artefacto sin bloque de metadatos', () => {
    // Los artefactos viejos pueden no traerlo; que falte no puede impedir que
    // la capa se dibuje.
    const grid = parseHazardGrid({
      type: 'FeatureCollection',
      features: [cell(-71.5, -33)],
    })

    expect(grid.metadata).toBeNull()
    expect(grid.cellSizeDeg).toBeNull()
    expect(grid.cells.features).toHaveLength(1)
  })
})
