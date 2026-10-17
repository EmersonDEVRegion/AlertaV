// @vitest-environment node
/**
 * Validación contra la especificación real de MapLibre.
 *
 * # Por qué este archivo vale más que todos los tests estructurales juntos
 *
 * El resto de los tests de capas comprueban *nuestras* invariantes: que el
 * ancla exista, que el zoom no esté anidado, que el riesgo cambie color y
 * opacidad. Son útiles, pero todos comparten un punto ciego: **describen lo que
 * creemos que MapLibre acepta**. Este usa el mismo validador que MapLibre
 * ejecuta al montar el estilo, así que no cree nada — comprueba.
 *
 * Importa porque el modo de falla de un estilo inválido es el peor posible:
 * MapLibre no lanza. Emite un `error` en consola, descarta la capa —a veces el
 * estilo entero— y deja un mapa en blanco o sin la capa, sin nada que apunte a
 * la línea culpable. Este repositorio ya pagó dos veces el mismo error concreto
 * (`["zoom"]` anidado dentro de otra expresión) y lo detectó a ojo en el
 * navegador las dos veces.
 *
 * # Qué cubre
 *
 * Las dos capas de referencia en sus **dos temas**, porque las expresiones se
 * construyen por tema y una rampa mal formada en oscuro no se ve nunca desde el
 * tema claro. Y los polígonos derivados, que son las anclas del orden de dibujo.
 */

import { describe, expect, it } from 'vitest'
import { validateStyleMin } from '@maplibre/maplibre-gl-style-spec'
import type { LayerSpecification, StyleSpecification } from 'maplibre-gl'
import { hazardFieldLayer } from './hazardLayers'
import {
  rainFieldLayer,
  rainRiskLabelLayer,
  rainTextLayer,
} from './rainLayers'
import { closureBodyLayer, roadClosureSpecs } from './roadClosureLayers'
import { coneFillLayer, coneLineLayer, reachFillLayer, reachLineLayer } from './overlayLayers'
import {
  alertHaloLayer,
  casingLayer,
  closedRingLayer,
  coreLayer,
  selectedLayer,
  uncertaintyLayer,
  unverifiedLayer,
} from './incidentLayers'

const THEMES = ['light', 'dark'] as const

/** Fuente vacía: el validador mira la forma del estilo, no los datos. */
const emptySource = {
  type: 'geojson' as const,
  data: { type: 'FeatureCollection' as const, features: [] },
}

function buildStyle(): StyleSpecification {
  const layers: LayerSpecification[] = []

  for (const theme of THEMES) {
    layers.push({
      ...hazardFieldLayer(theme, true),
      // Un `id` por tema: el validador rechaza duplicados y acá se monta la
      // misma capa dos veces a propósito.
      id: `hazardFieldLayer-${theme}`,
      source: 'hazardField',
    } as LayerSpecification)

    for (const factory of [rainRiskLabelLayer, rainTextLayer]) {
      layers.push({
        ...factory(theme, true),
        id: `${factory.name}-${theme}`,
        source: 'rain',
      } as LayerSpecification)
    }
    layers.push({
      ...rainFieldLayer(theme, true),
      id: `rainFieldLayer-${theme}`,
      source: 'rainField',
    } as LayerSpecification)

    /*
     * Los cortes de ruta. La rampa de color interpola sobre `severidad` DENTRO
     * de una interpolación sobre el zoom, que es exactamente la forma que el
     * validador rechaza si se anida al revés. Y como toda la capa se construye
     * por tema, una rampa mal formada en oscuro no se vería nunca desde el
     * tema claro.
     */
    // Las cinco subcapas, icono incluido, tal como las monta el componente.
    for (const spec of Object.values(roadClosureSpecs(theme, true))) {
      layers.push({
        ...spec,
        id: `${spec.id}-${theme}`,
        source: 'closures',
      } as LayerSpecification)
    }
  }

  for (const layer of [reachFillLayer, reachLineLayer, coneFillLayer, coneLineLayer]) {
    layers.push({ ...layer, source: 'overlay' } as LayerSpecification)
  }

  // El círculo de incertidumbre interpola en base 2 sobre el zoom con una
  // expresión de datos en cada tope: la forma que el validador vigila.
  for (const layer of [uncertaintyLayer('INC-2026-00001'), alertHaloLayer, coreLayer]) {
    layers.push({ ...layer, source: 'incidents' } as LayerSpecification)
  }
  // Las capas del marcador se arman por tema: placa, cerrado, sin verificar y
  // selección. Una por tema, con `id` propio.
  for (const theme of THEMES) {
    for (const layer of [
      casingLayer(theme),
      closedRingLayer(theme),
      unverifiedLayer(theme),
      selectedLayer('INC-2026-00001', theme),
    ]) {
      layers.push({
        ...layer,
        id: `${layer.id}-${theme}`,
        source: 'incidents',
      } as LayerSpecification)
    }
  }

  return {
    version: 8,
    name: 'alertav-check',
    sources: {
      hazardField: {
        type: 'image' as const,
        url: 'data:image/png;base64,',
        coordinates: [
          [-72, -32],
          [-69.8, -32],
          [-69.8, -33.8],
          [-72, -33.8],
        ],
      },
      rain: emptySource,
      rainField: {
        type: 'image' as const,
        url: 'data:image/png;base64,',
        coordinates: [
          [-72.3, -31.9],
          [-69.9, -31.9],
          [-69.9, -34],
          [-72.3, -34],
        ],
      },
      overlay: emptySource,
      closures: emptySource,
      incidents: emptySource,
    },
    layers,
  }
}

describe('las capas compilan contra la spec de MapLibre', () => {
  it('no produce ningún error de validación', () => {
    const errors = validateStyleMin(buildStyle()).map((error) => error.message)

    /*
     * Los mensajes se comparan como arreglo y no con `toHaveLength(0)` a
     * propósito: cuando falla, el diff muestra el mensaje exacto del validador
     * —«zoom expression may only be used as input to a top-level step or
     * interpolate expression»— que dice qué está mal y dónde. Un conteo sólo
     * diría que algo lo está.
     */
    expect(errors).toEqual([])
  })

  it('cubre los dos temas: una rampa rota en oscuro no se ve desde el claro', () => {
    const style = buildStyle()
    for (const theme of THEMES) {
      expect(style.layers.some((layer) => layer.id.endsWith(`-${theme}`))).toBe(true)
    }
  })

  it('el estilo apagado también es válido', () => {
    // `visibility: 'none'` no exime de validar el resto de la capa, y es el
    // estado en el que ambas arrancan.
    const layers: LayerSpecification[] = [
      { ...hazardFieldLayer('dark', false), source: 'hazard' } as LayerSpecification,
      { ...rainTextLayer('dark', false), source: 'rain' } as LayerSpecification,
      { ...closureBodyLayer('dark', false), source: 'closures' } as LayerSpecification,
    ]

    const errors = validateStyleMin({
      version: 8,
      name: 'off',
      sources: {
        hazard: {
          type: 'image' as const,
          url: 'data:image/png;base64,',
          coordinates: [
            [-72, -32],
            [-69.8, -32],
            [-69.8, -33.8],
            [-72, -33.8],
          ],
        },
        rain: emptySource,
        closures: emptySource,
      },
      layers,
    })

    expect(errors.map((error) => error.message)).toEqual([])
  })
})
