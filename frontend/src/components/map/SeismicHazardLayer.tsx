import { useMemo } from 'react'
import { Layer, Source, useMap } from 'react-map-gl/maplibre'
import type { HazardGrid } from '@/api/hazardTypes'
import type { Theme } from '@/hooks/useTheme'
import { rasterizeHazard } from '@/lib/hazardRaster'
import { RAIN_FIELD_LAYER_ID, RAIN_RISK_LABEL_LAYER_ID } from './rainLayers'
import {
  HAZARD_BEFORE_ID,
  HAZARD_LAYER_IDS,
  HAZARD_SOURCE_ID,
  hazardFieldLayer,
} from './hazardLayers'
import { useLayerReanchor } from './useLayerReanchor'

/**
 * Capa de amenaza sísmica.
 *
 * # Qué se fue de este archivo, y por qué eso ES el arreglo
 *
 * **La máquina de estados**: vivía acá un `useEffect` que enganchaba
 * `sourcedata` y `error` de MapLibre. Hoy el estado de carga sale de una
 * promesa en `hooks/useSeismicHazard.ts`, que resuelve o rechaza exactamente
 * una vez.
 *
 * **El mapa de calor** y su segunda fuente de puntos: ver
 * `domain/hazardSymbology.ts`.
 *
 * **Los polígonos**: el artefacto sigue llegando como celdas, pero se
 * pinta como una imagen interpolada con el borde difuminado
 * (`lib/hazardRaster.ts`). La caja recta de la grilla ya no se ve.
 *
 * Lo que queda es sólo lo que un componente de mapa debe hacer: declarar una
 * fuente y su capa.
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
 * más baja: el campo (la imagen), o la etiqueta de riesgo cuando no llueve en
 * ninguna parte y el campo no se monta. Si no, el cono de viento, la única capa
 * propia que existe siempre.
 */
const HAZARD_ANCHORS = [RAIN_FIELD_LAYER_ID, RAIN_RISK_LABEL_LAYER_ID, HAZARD_BEFORE_ID] as const

export function SeismicHazardLayer({ grid, visible, theme }: SeismicHazardLayerProps) {
  const { current: map } = useMap()
  const instance = map?.getMap() ?? null

  useLayerReanchor(instance, HAZARD_LAYER_IDS, HAZARD_ANCHORS)

  // La imagen depende del dato y del tema (cada tema tiene su rampa). Se arma
  // una vez por combinación: ~100 mil píxeles, unos milisegundos.
  const raster = useMemo(() => rasterizeHazard(grid, theme), [grid, theme])
  const field = useMemo(() => hazardFieldLayer(theme, visible), [theme, visible])

  // Sin `document` (pruebas) o con una grilla que no alcanza, no hay nada que montar.
  if (!raster) return null

  return (
    <Source
      id={HAZARD_SOURCE_ID}
      type="image"
      url={raster.url}
      coordinates={raster.coordinates}
    >
      <Layer beforeId={HAZARD_BEFORE_ID} {...field} />
    </Source>
  )
}
