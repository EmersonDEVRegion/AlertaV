import { useEffect, useMemo } from 'react'
import { Layer, Source, useMap } from 'react-map-gl/maplibre'
import type { ErrorEvent, MapSourceDataEvent } from 'maplibre-gl'
import type { RainCollection } from '@/api/rainTypes'
import type { RainRaster } from '@/lib/rainRaster'
import type { Theme } from '@/hooks/useTheme'
import {
  RAIN_BEFORE_ID,
  RAIN_FIELD_SOURCE_ID,
  RAIN_LAYER_IDS,
  RAIN_RISK_LABEL_LAYER_ID,
  RAIN_SOURCE_ID,
  rainFieldLayer,
  rainRiskLabelLayer,
  rainTextLayer,
} from './rainLayers'
import { useLayerReanchor } from './useLayerReanchor'
import { clearRainFieldError, reportRainFieldError } from '@/lib/tacticalWeatherStore'

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
  /** El campo interpolado; `null` sin grilla o sin lluvia en ninguna parte. */
  raster: RainRaster | null
  /** Encendida o apagada. Nunca desmonta: alterna `visibility`. */
  visible: boolean
  theme: Theme
}

/**
 * Ancla única: el cono de viento.
 *
 * A diferencia de la amenaza sísmica, la lluvia no tiene nada por encima entre
 * las capas de referencia, así que la lista tiene un solo elemento. Sigue
 * siendo una lista porque `useLayerReanchor` la comparte con la amenaza y una
 * firma distinta por capa invitaría a que cada una reimplementara lo suyo.
 */
const RAIN_ANCHORS = [RAIN_BEFORE_ID] as const

export function RainLayer({ data, raster, visible, theme }: RainLayerProps) {
  const { current: map } = useMap()
  const instance = map?.getMap() ?? null

  /*
   * Re-anclaje tras un cambio de estilo.
   *
   * Cambiar de tema llama a `map.setStyle()`, que vacía el arreglo de capas.
   * react-map-gl vuelve a añadir cada `<Layer>` al recibir `styledata`, en el
   * orden en que están montados los componentes — por eso este bloque va DESPUÉS
   * del cono en `IncidentMap`, para que su ancla ya exista cuando le toque.
   *
   * Aun así, el orden de reconstrucción de MapLibre no es un contrato público, y
   * el precio de equivocarse es que la lluvia tape los pines de emergencia. La
   * mecánica está extraída en `useLayerReanchor`, que ahora comparten esta capa
   * y la de amenaza sísmica.
   */
  useLayerReanchor(instance, RAIN_LAYER_IDS, RAIN_ANCHORS)

  /*
   * ¿Se dibujó el campo?
   *
   * La imagen es un `data:` que MapLibre 6 descarga con `fetch()`. Si algo lo
   * impide —la CSP sin `data:` en `connect-src`, como pasó en producción— el
   * error sólo llegaba a la consola y el widget seguía mostrando la escala sobre
   * un mapa vacío. Los eventos de fuente del mapa traen `sourceId`, así que se
   * filtra el del campo y se avisa por el store, que es lo que lee el widget.
   *
   * Una carga correcta (o una imagen nueva, que es otro intento) limpia el aviso.
   */
  useEffect(() => {
    if (!instance) return
    // El `error` del mapa no tipa `sourceId`, pero MapLibre se lo agrega a los
    // eventos que reenvía desde una fuente (ver `Style.addSource`).
    const onError = (event: ErrorEvent) => {
      if ((event as ErrorEvent & { sourceId?: string }).sourceId === RAIN_FIELD_SOURCE_ID) {
        reportRainFieldError()
      }
    }
    const onData = (event: MapSourceDataEvent) => {
      if (event.sourceId === RAIN_FIELD_SOURCE_ID && event.isSourceLoaded) clearRainFieldError()
    }
    instance.on('error', onError)
    instance.on('sourcedata', onData)
    return () => {
      instance.off('error', onError)
      instance.off('sourcedata', onData)
    }
  }, [instance])

  const rasterUrl = raster?.url
  useEffect(() => {
    if (rasterUrl) clearRainFieldError()
  }, [rasterUrl])

  // Las especificaciones sólo cambian con el tema o con el encendido. react-map-gl
  // compara propiedad por propiedad, así que un objeto nuevo con los mismos
  // valores no produce escrituras — pero memorizarlas evita incluso esa
  // comparación en cada repintado del árbol.
  const field = useMemo(() => rainFieldLayer(theme, visible), [theme, visible])
  const risk = useMemo(() => rainRiskLabelLayer(theme, visible), [theme, visible])
  const text = useMemo(() => rainTextLayer(theme, visible), [theme, visible])

  return (
    <>
      {/*
        El campo, primero: queda el más abajo de la lluvia. Una fuente `image`
        con la grilla interpolada; cambia una vez por hora (`url` nueva) y
        react-map-gl la actualiza sin recrear la capa. Sin lluvia en ninguna
        parte no se monta: una imagen transparente no aporta nada.

        Sin `interactiveLayerIds`: la lluvia no se selecciona ni le roba el
        clic al incidente que tenga debajo.
      */}
      {raster && (
        <Source
          id={RAIN_FIELD_SOURCE_ID}
          type="image"
          url={raster.url}
          coordinates={raster.coordinates}
        >
          {/* Antes de la etiqueta de riesgo y no del cono: la imagen llega
              después que las comunas, y anclada al cono quedaría encima del
              texto. La etiqueta existe siempre que exista esta capa. */}
          <Layer beforeId={RAIN_RISK_LABEL_LAYER_ID} {...field} />
        </Source>
      )}
      <Source id={RAIN_SOURCE_ID} type="geojson" data={data}>
        {/* Los dos con el mismo `beforeId`: el orden de inserción es el de
            dibujo, y el texto queda encima del campo y debajo del cono, los
            sismos y los incidentes. */}
        <Layer beforeId={RAIN_BEFORE_ID} {...risk} />
        <Layer beforeId={RAIN_BEFORE_ID} {...text} />
      </Source>
    </>
  )
}
