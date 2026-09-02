// @vitest-environment node
/**
 * Iconografía SDF: validación contra el spec y contra el diccionario.
 *
 * El modo de falla de una capa `symbol` mal formada es silencioso: MapLibre no
 * lanza, emite un `error` por punto y no dibuja el icono. Con varias capas
 * encima, un mapa sin símbolos parece «todavía cargando».
 */

import { describe, expect, it } from 'vitest'
import { validateStyleMin } from '@maplibre/maplibre-gl-style-spec'
import type { ExpressionSpecification, LayerSpecification, StyleSpecification } from 'maplibre-gl'
import {
  CLOSURE_ICON_LAYER_ID,
  INCIDENT_ICON_LAYER_ID,
  SEISMIC_ICON_LAYER_ID,
  closureIconLayer,
  incidentIconLayer,
  seismicIconLayer,
} from './emergencyIconLayers'
import {
  FALLBACK_ICON,
  ICON_GLYPHS,
  ICON_IDS,
  INCIDENT_TYPE_ICON,
  SEISMIC_ICON,
  CLOSURE_ICON,
} from '@/domain/emergencyIcons'
import { INCIDENT_TYPES } from '@/api/types'
import { MAGNITUDE_COLOR_EXPRESSION } from '@/domain/seismicSymbology'
import { closureColor } from './roadClosureLayers'

const THEMES = ['light', 'dark'] as const

function buildStyle(layers: LayerSpecification[]): StyleSpecification {
  return {
    version: 8,
    sources: { src: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } } },
    layers: layers.map((l) => ({ ...l, source: 'src' })),
  } as StyleSpecification
}

describe('validez contra el spec de MapLibre', () => {
  it('las tres capas de símbolo pasan el validador en ambos temas', () => {
    for (const theme of THEMES) {
      const layers = [
        incidentIconLayer(theme),
        seismicIconLayer(theme, MAGNITUDE_COLOR_EXPRESSION as unknown as ExpressionSpecification),
        closureIconLayer(theme, closureColor(theme)),
      ] as unknown as LayerSpecification[]

      const errors = validateStyleMin(buildStyle(layers)).map((e) => e.message)
      expect({ theme, errors }).toEqual({ theme, errors: [] })
    }
  })

  it('los identificadores no chocan entre sí', () => {
    const ids = [INCIDENT_ICON_LAYER_ID, SEISMIC_ICON_LAYER_ID, CLOSURE_ICON_LAYER_ID]
    expect(new Set(ids).size).toBe(ids.length)
  })
})

describe('el diccionario y el `match` no se desincronizan', () => {
  it('todo icono referenciado está declarado', () => {
    const referenced = new Set<string>([
      ...Object.values(INCIDENT_TYPE_ICON),
      FALLBACK_ICON,
      SEISMIC_ICON,
      CLOSURE_ICON,
    ])
    const declared = new Set<string>(ICON_IDS)
    // Un icono referenciado y no registrado hace que MapLibre no dibuje NADA
    // en esos puntos, con un error por feature en consola.
    expect([...referenced].filter((id) => !declared.has(id))).toEqual([])
  })

  it('todo icono declarado tiene glifo con al menos un trazo', () => {
    for (const id of ICON_IDS) {
      expect(ICON_GLYPHS[id].paths.length).toBeGreaterThan(0)
      expect(ICON_GLYPHS[id].label.length).toBeGreaterThan(0)
    }
  })

  it('todo sub-trazo cierra: el rasterizador rellena, no traza', () => {
    /*
     * El modo de fallo que esto ataja no da error en ninguna parte.
     *
     * `iconRaster` hace `ctx.fill()`, y un camino que sólo describe segmentos
     * —`M12 9v4`, la forma de los iconos de lucide— no encierra área: se
     * rellena a nada. El glifo sale invisible, MapLibre dibuja un símbolo vacío
     * y la consola queda limpia. Copiar un path de lucide es exactamente la
     * forma en que alguien va a tropezar con esto.
     *
     * Exigir la `z` no prueba que el dibujo sea bonito, pero sí que haya
     * superficie que rellenar, que es la condición que el pipeline impone.
     */
    for (const id of ICON_IDS) {
      for (const d of ICON_GLYPHS[id].paths) {
        expect(d, `${id}: sub-trazo sin cerrar → se rellenaría vacío`).toMatch(/[zZ]/)
      }
    }
  })

  it('ningún glifo excede el lienzo de 24, que es lo que asume el rasterizador', () => {
    // `iconRaster` escala por `inner / VIEWBOX` con VIEWBOX = 24. Una coordenada
    // fuera de rango no se recorta con un error: se come el margen reservado
    // para el SDF y el halo aparece cortado en seco contra el borde.
    for (const id of ICON_IDS) {
      for (const d of ICON_GLYPHS[id].paths) {
        for (const n of d.match(/-?\d+(\.\d+)?/g) ?? []) {
          expect(Math.abs(Number(n)), `${id}: coordenada ${n} fuera del lienzo`).toBeLessThanOrEqual(24)
        }
      }
    }
  })

  it('el `match` se genera desde el diccionario, no a mano', () => {
    const expr = JSON.stringify(incidentIconLayer('dark').layout?.['icon-image'])
    // Cada tipo del diccionario tiene que aparecer en la expresión.
    for (const type of Object.keys(INCIDENT_TYPE_ICON)) expect(expr).toContain(`"${type}"`)
    // Y el respaldo cierra el `match`, o un tipo nuevo del backend rompería la capa.
    expect(expr.endsWith(`"${FALLBACK_ICON}"]`)).toBe(true)
  })

  it('no incluye `power_outage`: los cortes de luz son marcadores del DOM', () => {
    expect(INCIDENT_TYPE_ICON['power_outage']).toBeUndefined()
  })

  it('cubre TODOS los tipos que el backend puede emitir', () => {
    /*
     * El test de arriba comprueba que lo referenciado exista. Éste comprueba lo
     * contrario, que es el hueco por donde se cuela el fallo real: si alguien
     * borra una entrada del diccionario, el `match` sigue siendo válido y esos
     * incidentes caen al triángulo de respaldo. Nada se rompe, nada avisa, y un
     * choque pasa a dibujarse como «contingencia».
     *
     * `power_outage` es la única ausencia deliberada.
     */
    const esperados = INCIDENT_TYPES.filter((t) => t !== 'power_outage')
    const faltantes = esperados.filter((t) => !(t in INCIDENT_TYPE_ICON))
    expect(faltantes).toEqual([])
  })
})

describe('color y contraste', () => {
  it('el icono de incidente se colorea por severidad, no con un color fijo', () => {
    const color = incidentIconLayer('dark').paint?.['icon-color']
    // Si fuera una cadena, el `icon-color` estaría fijo y la severidad se
    // perdería: es justo lo que SDF permite evitar.
    expect(typeof color).not.toBe('string')
    expect(JSON.stringify(color)).toContain('confidence_level')
  })

  it('el halo sigue al tema para separar el glifo del terreno', () => {
    expect(incidentIconLayer('light').paint?.['icon-halo-color']).toBe('#f1f5f9')
    expect(incidentIconLayer('dark').paint?.['icon-halo-color']).toBe('#020617')
    for (const theme of THEMES) {
      expect(incidentIconLayer(theme).paint?.['icon-halo-width']).toBeGreaterThan(0)
    }
  })

  it('los cerrados se atenúan, como hacía el disco al que reemplazan', () => {
    expect(JSON.stringify(incidentIconLayer('dark').paint?.['icon-opacity'])).toContain('is_closed')
  })
})

describe('colocación', () => {
  it('nunca esconde un símbolo por colisión', () => {
    for (const layer of [
      incidentIconLayer('dark'),
      seismicIconLayer('dark', MAGNITUDE_COLOR_EXPRESSION as unknown as ExpressionSpecification),
      closureIconLayer('dark', closureColor('dark')),
    ]) {
      // Ocultar un incidente porque hay otro cerca sería esconder información
      // justo donde más está pasando.
      expect(layer.layout?.['icon-allow-overlap']).toBe(true)
      expect(layer.layout?.['icon-ignore-placement']).toBe(true)
    }
  })

  it('el icono sísmico es más chico que el de incidente: va dentro del círculo', () => {
    const sizeAt = (layer: { layout?: Record<string, unknown> }, i: number) =>
      (layer.layout?.['icon-size'] as unknown[])[i] as number
    const inc = incidentIconLayer('dark')
    const seis = seismicIconLayer('dark', MAGNITUDE_COLOR_EXPRESSION as unknown as ExpressionSpecification)
    // Índice 4 = primer valor de salida del `interpolate`.
    expect(sizeAt(seis, 4)).toBeLessThan(sizeAt(inc, 4))
  })
})
