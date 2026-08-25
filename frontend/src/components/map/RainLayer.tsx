import { useEffect, useMemo } from 'react'
import { Layer, Source, useMap } from 'react-map-gl/maplibre'
import type { RainCollection } from '@/api/rainTypes'
import { RAIN_PALETTE } from '@/domain/rainSymbology'
import type { Theme } from '@/hooks/useTheme'
import { useRainPulse } from '@/hooks/useRainPulse'
import {
  RAIN_BEFORE_ID,
  RAIN_LAYER_IDS,
  RAIN_SOURCE_ID,
  rainCoreLayer,
  rainHaloLayer,
  rainRiskRingLayer,
} from './rainLayers'

/**
 * Capa de lluvia pronosticada.
 *
 * # Al revés que la amenaza sísmica
 *
 * `SeismicHazardLayer` le pasa una URL al `<Source>` para que MapLibre descargue
 * y parsee el archivo en su worker: es una grilla de miles de celdas y hacer el
 * `JSON.parse` en el hilo principal se notaría. Acá son 36 puntos que ya vienen
 * de react-query —con reintentos, caché y cancelación— y el objeto se pasa
 * directo. Para este tamaño, la ruta con URL sólo añadiría una segunda vía de
 * carga y errores que habría que mantener.
 *
 * El `<Source>` recibe la MISMA referencia de objeto mientras el dato no cambie:
 * react-query hace *structural sharing* y `EMPTY_RAIN` es una constante
 * compartida. Es lo que evita que MapLibre vuelva a subir el GeoJSON en cada
 * repintado del árbol.
 */

interface RainLayerProps {
  data: RainCollection
  /** Encendida o apagada. Nunca desmonta: alterna `visibility`. */
  visible: boolean
  theme: Theme
  /** ¿Hay alguna comuna con `riesgo_inundacion`? Decide si el pulso arranca. */
  hasRisk: boolean
}

export function RainLayer({ data, visible, theme, hasRisk }: RainLayerProps) {
  const { current: map } = useMap()
  const instance = map?.getMap() ?? null

  useRainPulse(instance, visible && hasRisk, RAIN_PALETTE[theme].ringOpacity)

  /*
   * Re-anclaje tras un cambio de estilo.
   *
   * Cambiar de tema llama a `map.setStyle()`, que vacía el arreglo de capas.
   * react-map-gl vuelve a añadir cada `<Layer>` al recibir `styledata`, en el
   * orden en que están montados los componentes — por eso este bloque va DESPUÉS
   * del cono en `IncidentMap`, para que su ancla ya exista cuando le toque.
   *
   * Aun así, el orden de reconstrucción de MapLibre no es un contrato público, y
   * el precio de equivocarse es que la lluvia tape los pines de emergencia: la
   * jerarquía invertida, en silencio y sólo después de tocar el tema. Esta
   * comprobación cuesta un `indexOf` sobre un arreglo de identificadores y
   * corrige el caso si llega a darse.
   */
  useEffect(() => {
    if (!instance) return

    const reanchor = () => {
      const order = instance.getLayersOrder()
      const anchor = order.indexOf(RAIN_BEFORE_ID)
      if (anchor === -1) return

      for (const id of RAIN_LAYER_IDS) {
        const position = order.indexOf(id)
        // Sólo si quedó POR ENCIMA del ancla. Mover una capa que ya está en su
        // sitio marcaría el estilo como sucio y forzaría un repintado inútil.
        if (position !== -1 && position > anchor) instance.moveLayer(id, RAIN_BEFORE_ID)
      }
    }

    instance.on('styledata', reanchor)
    return () => {
      instance.off('styledata', reanchor)
    }
  }, [instance])

  // Las especificaciones sólo cambian con el tema o con el encendido. react-map-gl
  // compara propiedad por propiedad, así que un objeto nuevo con los mismos
  // valores no produce escrituras — pero memorizarlas evita incluso esa
  // comparación en cada repintado del árbol.
  const halo = useMemo(() => rainHaloLayer(theme, visible), [theme, visible])
  const core = useMemo(() => rainCoreLayer(theme, visible), [theme, visible])
  const ring = useMemo(() => rainRiskRingLayer(theme, visible), [theme, visible])

  return (
    /*
     * Sin `interactiveLayerIds` y sin `promoteId`: la lluvia no se selecciona, no
     * abre ficha y no debe robarle el clic al incidente que tenga debajo. Es
     * contexto, y el contexto no se toca.
     */
    <Source id={RAIN_SOURCE_ID} type="geojson" data={data}>
      {/* Los tres con el mismo `beforeId`: cada uno se inserta justo antes del
          ancla, así que el orden de inserción es el orden de dibujo. */}
      <Layer beforeId={RAIN_BEFORE_ID} {...halo} />
      <Layer beforeId={RAIN_BEFORE_ID} {...core} />
      <Layer beforeId={RAIN_BEFORE_ID} {...ring} />
    </Source>
  )
}
