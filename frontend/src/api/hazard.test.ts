// @vitest-environment node
/**
 * Parseo del artefacto de amenaza y derivación de sus nodos.
 *
 * # Por qué la derivación merece tests propios
 *
 * El `heatmap` sólo se alimenta de geometrías `Point`, y las celdas del modelo
 * son polígonos. Traducir de una a otra es la línea que hace posible toda la
 * capa nueva, y su modo de falla es silencioso: una celda mal traducida no
 * lanza nada, sólo aporta un peso equivocado a un campo continuo. Nadie
 * distingue a ojo un mapa de calor correcto de uno con un 3 % de nodos malos.
 *
 * El caso que más importa es el de una celda sin la variable: tiene que
 * **desaparecer**, no aportar un cero. Un cero hunde la densidad de esa zona, y
 * hundir la amenaza sísmica hacia abajo es equivocarse del lado que tranquiliza.
 */

import { describe, expect, it } from 'vitest'
import { HazardLoadError, parseHazardGrid, toHazardNodes } from './hazard'
import type { HazardCells } from './hazardTypes'

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

function collection(...features: ReturnType<typeof cell>[]): HazardCells {
  return { type: 'FeatureCollection', features } as unknown as HazardCells
}

describe('derivación de nodos', () => {
  it('usa el nodo original del CSN, no un centroide recalculado', () => {
    const nodes = toHazardNodes(collection(cell(-71.5, -33.05)))

    // El script conserva el centro en `properties.lon` / `lat`. Promediar los
    // vértices daría el mismo número hoy y se rompería el día que el artefacto
    // emita celdas que no sean rectángulos.
    expect(nodes.features).toHaveLength(1)
    expect(nodes.features[0]!.geometry.coordinates).toEqual([-71.5, -33.05])
  })

  it('lleva el valor de la variable como peso', () => {
    const nodes = toHazardNodes(collection(cell(-71.5, -33, { pga_475: 0.42 })))
    expect(nodes.features[0]!.properties.value).toBe(0.42)
  })

  it('descarta la celda sin la variable en vez de darle un cero', () => {
    const nodes = toHazardNodes(
      collection(cell(-71.5, -33), cell(-71.4, -33, { pga_475: null })),
    )

    // Ver la nota de la cabecera: un cero mentiría hacia el lado que tranquiliza.
    expect(nodes.features).toHaveLength(1)
  })

  it('descarta la celda con coordenadas no numéricas', () => {
    const nodes = toHazardNodes(collection(cell(-71.5, -33, { lon: 'x' })))
    expect(nodes.features).toHaveLength(0)
  })

  it('descarta el valor cero, que es un hueco y no un dato', () => {
    const nodes = toHazardNodes(collection(cell(-71.5, -33, { pga_475: 0 })))
    expect(nodes.features).toHaveLength(0)
  })

  it('no arrastra las otras variables del modelo', () => {
    const nodes = toHazardNodes(
      collection(cell(-71.5, -33, { pga_2475: 0.8, sa03_475: 0.9 })),
    )

    // El heatmap sólo lee `value`. Arrastrar las cinco variables multiplicaría
    // por cinco lo que se sube a la GPU sin que nada las mire.
    expect(Object.keys(nodes.features[0]!.properties)).toEqual(['value'])
  })
})

describe('validación del artefacto', () => {
  it('acepta una colección bien formada y deriva ambas representaciones', () => {
    const grid = parseHazardGrid({
      type: 'FeatureCollection',
      features: [cell(-71.5, -33), cell(-71.4, -33)],
      metadata: { model: 'MASCSN26', cell_size_deg: { lon: 0.045, lat: 0.045 } },
    })

    expect(grid.cells.features).toHaveLength(2)
    expect(grid.nodes.features).toHaveLength(2)
    expect(grid.cellSizeDeg).toBeCloseTo(0.045, 6)
    expect(grid.metadata?.model).toBe('MASCSN26')
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
    expect(grid.nodes.features).toHaveLength(1)
  })
})
