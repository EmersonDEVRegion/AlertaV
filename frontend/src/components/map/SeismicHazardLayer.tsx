import { useMemo } from 'react'
import { Layer, Source, useMap } from 'react-map-gl/maplibre'
import type { HazardGrid } from '@/api/hazardTypes'
import type { Theme } from '@/hooks/useTheme'
import { RAIN_HEAT_LAYER_ID } from './rainLayers'
import {
  HAZARD_BEFORE_ID,
  HAZARD_CELL_SOURCE_ID,
  HAZARD_LAYER_IDS,
  hazardFillLayer,
  hazardLineLayer,
} from './hazardLayers'
import { useLayerReanchor } from './useLayerReanchor'

/**
 * Capa de amenaza sísmica.
 *
 * # Qué se fue de este archivo, y por qué eso ES el arreglo
 *
 * Dos veces, por motivos distintos.
 *
 * **La primera**: vivía acá una máquina de estados alimentada por eventos de
 * MapLibre —un `useEffect` que enganchaba `sourcedata` y `error`, los filtraba
 * por identificador de fuente y consultaba además `isSourceLoaded()` al montar
 * para cerrar la ventana en que el archivo llegaba desde la caché antes que el
 * escucha—. Todo eso desapareció: el estado de carga sale de una promesa en
 * `hooks/useSeismicHazard.ts`, que resuelve o rechaza exactamente una vez. El
 * detalle del bug —el interruptor que rebotaba al terminar la carga— está
 * documentado en ese hook.
 *
 * **La segunda**: había una SEGUNDA fuente, de puntos, derivada de los centros
 * de celda, que alimentaba un `heatmap` para la escala regional. Se fue entera.
 * El porqué está en `domain/hazardSymbology.ts`: un mapa de calor sobre una
 * grilla regular no mide el valor del modelo sino la suma de sus vecinos, y sólo
 * hacía falta porque las celdas venían mal dimensionadas del generador. Con las
 * celdas teselando, el relleno cubre todo el rango de zoom él solo.
 *
 * Lo que queda es sólo lo que un componente de mapa debe hacer: declarar una
 * fuente y sus capas.
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
  const fill = useMemo(() => hazardFillLayer(theme, visible), [theme, visible])
  const line = useMemo(() => hazardLineLayer(theme, visible), [theme, visible])

  return (
    /*
      Una sola fuente. El orden de montaje es el orden de dibujo porque las dos
      capas se insertan antes del mismo ancla: la superficie debajo, la retícula
      encima — que es el único orden en el que la retícula se ve.
    */
    <Source id={HAZARD_CELL_SOURCE_ID} type="geojson" data={grid.cells}>
      <Layer beforeId={HAZARD_BEFORE_ID} {...fill} />
      <Layer beforeId={HAZARD_BEFORE_ID} {...line} />
    </Source>
  )
}
