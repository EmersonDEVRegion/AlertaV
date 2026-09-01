import { useMemo } from 'react'
import { Layer, Source, useMap } from 'react-map-gl/maplibre'
import type { HazardGrid } from '@/api/hazardTypes'
import type { Theme } from '@/hooks/useTheme'
import { RAIN_HEAT_LAYER_ID } from './rainLayers'
import {
  HAZARD_BEFORE_ID,
  HAZARD_CELL_SOURCE_ID,
  HAZARD_LAYER_IDS,
  HAZARD_NODE_SOURCE_ID,
  hazardFillLayer,
  hazardHeatLayer,
  hazardLineLayer,
} from './hazardLayers'
import { useLayerReanchor } from './useLayerReanchor'

/**
 * Capa de amenaza sísmica.
 *
 * # Qué se fue de este archivo, y por qué eso ES el arreglo
 *
 * Antes vivía acá una máquina de estados alimentada por eventos de MapLibre:
 * un `useEffect` que enganchaba `sourcedata` y `error`, los filtraba por
 * identificador de fuente, y —porque con el archivo en caché la fuente podía
 * quedar cargada antes de que el escucha existiera— consultaba además
 * `isSourceLoaded()` al montar, dentro de un `try/catch`, para cerrar esa
 * ventana.
 *
 * Todo eso desapareció. El estado de carga ahora sale de una promesa en
 * `hooks/useSeismicHazard.ts`, que resuelve o rechaza exactamente una vez. No
 * hay ventana que cerrar porque no hay dos relojes que sincronizar. El detalle
 * completo del bug —el interruptor que rebotaba al terminar la carga— está
 * documentado en ese hook.
 *
 * Lo que queda acá es sólo lo que un componente de mapa debe hacer: declarar
 * fuentes y capas.
 *
 * # Dos fuentes del MISMO archivo
 *
 * `heatmap` sólo se alimenta de geometrías `Point`; las celdas del modelo son
 * polígonos. El artefacto del CSN conserva el nodo original de la grilla dentro
 * de cada celda, así que `api/hazard.ts` deriva los puntos en el mismo parseo:
 * una descarga, un `JSON.parse`, dos representaciones. Ver `api/hazardTypes.ts`.
 */

interface SeismicHazardLayerProps {
  grid: HazardGrid
  /** Encendida o apagada. Nunca desmonta: alterna `visibility`. */
  visible: boolean
  theme: Theme
}

/**
 * Anclas en orden de preferencia.
 *
 * La amenaza tiene que quedar **debajo de TODA la lluvia**, pero la lluvia puede
 * no estar montada —es otra capa diferida—. Si está, la referencia es su capa
 * más baja, que es el campo de calor; si no, el cono de viento, la única capa
 * propia que existe siempre.
 *
 * Anclar al halo en vez de al calor dejaría la amenaza intercalada entre las dos
 * capas de la lluvia: no rompe nada, pero pone un modelo estático encima de un
 * pronóstico, que es al revés de lo que la jerarquía afirma.
 */
const HAZARD_ANCHORS = [RAIN_HEAT_LAYER_ID, HAZARD_BEFORE_ID] as const

export function SeismicHazardLayer({ grid, visible, theme }: SeismicHazardLayerProps) {
  const { current: map } = useMap()
  const instance = map?.getMap() ?? null

  useLayerReanchor(instance, HAZARD_LAYER_IDS, HAZARD_ANCHORS)

  // Sólo cambian con el tema o con el encendido. react-map-gl compara propiedad
  // por propiedad, así que un objeto nuevo con los mismos valores no produce
  // escrituras — pero memorizarlas evita incluso esa comparación.
  const heat = useMemo(() => hazardHeatLayer(theme, visible), [theme, visible])
  const fill = useMemo(() => hazardFillLayer(theme, visible), [theme, visible])
  const line = useMemo(() => hazardLineLayer(theme, visible), [theme, visible])

  return (
    <>
      {/*
        El mapa de calor va PRIMERO en el árbol: los dos bloques se insertan
        antes del mismo ancla, así que el orden de montaje es el orden de
        dibujo. El calor debajo, las celdas encima — que es el orden en el que
        se relevan al hacer zoom.
      */}
      <Source id={HAZARD_NODE_SOURCE_ID} type="geojson" data={grid.nodes}>
        <Layer beforeId={HAZARD_BEFORE_ID} {...heat} />
      </Source>

      <Source id={HAZARD_CELL_SOURCE_ID} type="geojson" data={grid.cells}>
        <Layer beforeId={HAZARD_BEFORE_ID} {...fill} />
        <Layer beforeId={HAZARD_BEFORE_ID} {...line} />
      </Source>
    </>
  )
}
