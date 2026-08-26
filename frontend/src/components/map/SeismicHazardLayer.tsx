import { useCallback, useEffect } from 'react'
import { Layer, Source, useMap } from 'react-map-gl/maplibre'
// Los tipos de evento salen de maplibre-gl: el listener se registra sobre la
// instancia nativa del mapa, no sobre el envoltorio de react-map-gl.
import type { ErrorEvent, MapSourceDataEvent } from 'maplibre-gl'
import { HAZARD_SOURCE_URL } from '@/config/map'
import type { Theme } from '@/hooks/useTheme'
import {
  HAZARD_BEFORE_ID,
  HAZARD_SOURCE_ID,
  hazardFillLayer,
  hazardLineLayer,
} from './hazardLayers'

/**
 * Capa de amenaza sísmica.
 *
 * # Por qué se le pasa la URL al mapa en vez de hacer `fetch` acá
 *
 * `<Source data="/static/...">` con una cadena hace que MapLibre descargue y
 * parsee el archivo **en su web worker**. Un `fetch` + `JSON.parse` en el hook
 * bloquearía el hilo principal durante el parseo de una grilla de miles de
 * celdas, justo en el momento en que el usuario acaba de tocar el interruptor y
 * está mirando el mapa. El resultado es idéntico; el costo, no.
 *
 * Efecto secundario útil: la respuesta pasa por la caché HTTP del navegador y
 * por el service worker, así que en visitas siguientes ni siquiera viaja.
 */

interface SeismicHazardLayerProps {
  visible: boolean
  theme: Theme
  attempt: number
  onLoaded: () => void
  onError: () => void
}

export function SeismicHazardLayer({
  visible,
  theme,
  attempt,
  onLoaded,
  onError,
}: SeismicHazardLayerProps) {
  const { current: map } = useMap()

  /*
   * `<Source>` de react-map-gl no expone callbacks de carga, así que el estado
   * se deriva de los eventos del mapa filtrados por identificador de fuente.
   * `sourcedata` se dispara muchas veces; sólo interesa cuando la fuente ya
   * tiene sus datos cargados.
   */
  const handleSourceData = useCallback(
    (event: MapSourceDataEvent) => {
      if (event.sourceId === HAZARD_SOURCE_ID && event.isSourceLoaded) onLoaded()
    },
    [onLoaded],
  )

  /*
   * El evento `error` de MapLibre lleva `sourceId` cuando el fallo viene de una
   * fuente —un 404 del archivo, por ejemplo—, pero su tipo no lo declara porque
   * también se emite para errores sin fuente asociada. El ensanchamiento es
   * deliberado y acotado a la lectura de ese campo.
   */
  const handleError = useCallback(
    (event: ErrorEvent) => {
      const sourceId = (event as ErrorEvent & { sourceId?: string }).sourceId
      if (sourceId === HAZARD_SOURCE_ID) onError()
    },
    [onError],
  )

  useEffect(() => {
    if (!map) return
    const instance = map.getMap()

    /*
     * Carrera al montar.
     *
     * El `<Source>` se añade al mapa en el mismo commit que registra estos
     * escuchas. Con el archivo en la caché del navegador —o servido por el
     * service worker— puede quedar cargado ANTES de que `on('sourcedata')`
     * llegue a engancharse, y entonces el evento no se pierde: nunca se emite
     * para nosotros. El resultado era una capa dibujada correctamente con el
     * interruptor atascado en «Descargando modelo…».
     *
     * Preguntar por el estado actual antes de escuchar cierra esa ventana. Va
     * dentro de un try/catch porque `isSourceLoaded` lanza si la fuente todavía
     * no existe, que es el caso normal y no un error.
     */
    try {
      if (instance.getSource(HAZARD_SOURCE_ID) && instance.isSourceLoaded(HAZARD_SOURCE_ID)) {
        onLoaded()
      }
    } catch {
      /* la fuente aún no está registrada: se resolverá por evento */
    }

    instance.on('sourcedata', handleSourceData)
    instance.on('error', handleError)
    return () => {
      instance.off('sourcedata', handleSourceData)
      instance.off('error', handleError)
    }
  }, [map, handleSourceData, handleError, onLoaded])

  return (
    <Source
      // Remontar sólo al reintentar tras un error; en el uso normal `attempt`
      // no cambia y la fuente vive toda la sesión.
      key={attempt}
      id={HAZARD_SOURCE_ID}
      type="geojson"
      data={HAZARD_SOURCE_URL}
    >
      <Layer beforeId={HAZARD_BEFORE_ID} {...hazardFillLayer(theme, visible)} />
      <Layer beforeId={HAZARD_BEFORE_ID} {...hazardLineLayer(theme, visible)} />
    </Source>
  )
}
