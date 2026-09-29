import { validateStyleMin } from '@maplibre/maplibre-gl-style-spec'
import type { LayerSpecification, StyleSpecification } from 'maplibre-gl'
import { describe, expect, it } from 'vitest'
import { ICON_IDS, OUTAGE_ICON } from '@/domain/emergencyIcons'
import { toOutageFeatureCollection } from '@/lib/geojson'
import { makeIncident } from '@/test/fixtures'
import {
  OUTAGE_CLUSTER_LAYER_ID,
  OUTAGE_CLUSTER_MAX_ZOOM,
  OUTAGE_CLUSTER_PROPERTIES,
  OUTAGE_CLUSTER_RADIUS,
  OUTAGE_HIT_LAYER_ID,
  outageClusterCountLayer,
  outageClusterLayer,
  outageGlyphLayer,
  outageHitLayer,
  outagePinLayer,
  outageSelectedLayer,
} from './outageLayers'

const THEMES = ['light', 'dark'] as const

function styleWith(layers: LayerSpecification[]): StyleSpecification {
  return {
    version: 8,
    glyphs: 'https://example.invalid/{fontstack}/{range}.pbf',
    sources: {
      outages: {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
        cluster: true,
        clusterRadius: OUTAGE_CLUSTER_RADIUS,
        clusterMaxZoom: OUTAGE_CLUSTER_MAX_ZOOM,
        clusterProperties: OUTAGE_CLUSTER_PROPERTIES,
      },
    },
    layers: layers.map((layer) => ({ ...layer, source: 'outages' })),
  } as StyleSpecification
}

function allLayers(theme: 'light' | 'dark') {
  return [
    outageClusterLayer(theme),
    outageClusterCountLayer(),
    outagePinLayer(theme),
    outageGlyphLayer(),
    outageSelectedLayer(theme, 'INC-1'),
    outageSelectedLayer(theme, null),
    outageHitLayer,
  ]
}

describe('capas de cortes de luz', () => {
  it('pasan el validador de MapLibre, fuente agrupada incluida, en ambos temas', () => {
    for (const theme of THEMES) {
      const layers = allLayers(theme).map((layer, i) => ({ ...layer, id: `${layer.id}-${i}` }))
      const errors = validateStyleMin(styleWith(layers as unknown as LayerSpecification[]))
      expect({ theme, errors: errors.map((e) => e.message) }).toEqual({ theme, errors: [] })
    }
  })

  it('el rayo es un icono registrado: si no, MapLibre no dibuja nada y no avisa', () => {
    const image = outageGlyphLayer().layout?.['icon-image']
    expect(image).toBe(OUTAGE_ICON)
    expect(ICON_IDS).toContain(OUTAGE_ICON)
  })

  it('los racimos y los cortes sueltos nunca se mezclan en una misma capa', () => {
    const cluster = JSON.stringify(outageClusterLayer('light').filter)
    const pin = JSON.stringify(outagePinLayer('light').filter)
    expect(cluster).toBe('["has","point_count"]')
    expect(pin).toBe('["!",["has","point_count"]]')
    // El objetivo táctil es de los sueltos: un racimo se toca por su propia capa.
    expect(JSON.stringify(outageHitLayer.filter)).toBe(pin)
  })

  it('las capas que reciben el toque tienen identificadores propios', () => {
    expect(OUTAGE_CLUSTER_LAYER_ID).not.toBe(OUTAGE_HIT_LAYER_ID)
    expect(outageClusterLayer('dark').id).toBe(OUTAGE_CLUSTER_LAYER_ID)
  })
})

describe('toOutageFeatureCollection', () => {
  it('resuelve la empresa: declarada, inferida de las fuentes o desconocida', () => {
    const fc = toOutageFeatureCollection([
      makeIncident({ code: 'A', type: 'power_outage', outage: { provider: 'cge', affected_clients: 10, estimated_restoration: null, sector: null, outage_count: 1 } }),
      makeIncident({ code: 'B', type: 'power_outage', sources: ['chilquinta'], outage: null }),
      makeIncident({ code: 'C', type: 'power_outage', sources: ['citizen'], outage: null }),
    ])
    expect(fc.features.map((f) => f.properties.provider)).toEqual(['cge', 'chilquinta', 'unknown'])
  })

  it('sin dato de clientes suma 0: un null envenenaría el total del racimo', () => {
    const fc = toOutageFeatureCollection([
      makeIncident({ type: 'power_outage', outage: { provider: 'cge', affected_clients: null, estimated_restoration: null, sector: null, outage_count: 1 } }),
    ])
    expect(fc.features[0]?.properties.affected_clients).toBe(0)
  })

  it('marca los cerrados, que se dibujan atenuados', () => {
    const fc = toOutageFeatureCollection([
      makeIncident({ type: 'power_outage', status: 'controlled' }),
      makeIncident({ type: 'power_outage', status: 'active' }),
    ])
    expect(fc.features.map((f) => f.properties.is_closed)).toEqual([true, false])
  })
})
