import { useMemo } from 'react'
import { Layer, Source, useMap } from 'react-map-gl/maplibre'
import type { CuartelesData } from '@/api/cuarteles'
import type { Theme } from '@/hooks/useTheme'
import {
  CUARTELES_BEFORE_ID,
  CUARTELES_LAYER_IDS,
  CUARTELES_SOURCE_ID,
  cuartelLabelLayer,
  cuartelPointLayer,
} from './cuartelesLayers'
import { useLayerReanchor } from './useLayerReanchor'

interface CuartelesLayerProps {
  data: CuartelesData
  /** Encendida o apagada. Nunca desmonta: alterna `visibility`. */
  visible: boolean
  theme: Theme
}

const CUARTELES_ANCHORS = [CUARTELES_BEFORE_ID] as const

/** Cuarteles de Bomberos: capa de referencia, por debajo de toda emergencia. */
export function CuartelesLayer({ data, visible, theme }: CuartelesLayerProps) {
  const { current: map } = useMap()
  useLayerReanchor(map?.getMap() ?? null, CUARTELES_LAYER_IDS, CUARTELES_ANCHORS)

  const point = useMemo(() => cuartelPointLayer(theme, visible), [theme, visible])
  const label = useMemo(() => cuartelLabelLayer(theme, visible), [theme, visible])

  return (
    <Source id={CUARTELES_SOURCE_ID} type="geojson" data={data.collection}>
      <Layer beforeId={CUARTELES_BEFORE_ID} {...point} />
      <Layer beforeId={CUARTELES_BEFORE_ID} {...label} />
    </Source>
  )
}
