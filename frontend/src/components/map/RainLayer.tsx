import { useMemo } from 'react'
import { Layer, Source, useMap } from 'react-map-gl/maplibre'
import type { RainCollection } from '@/api/rainTypes'
import type { Theme } from '@/hooks/useTheme'
import {
  RAIN_BEFORE_ID,
  RAIN_LAYER_IDS,
  RAIN_SOURCE_ID,
  rainCoreLayer,
  rainHaloLayer,
  rainHeatLayer,
  rainNucleusLayer,
  rainRiskRingLayer,
  rainTextLayer,
} from './rainLayers'
import { useLayerReanchor } from './useLayerReanchor'

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

export function RainLayer({ data, visible, theme }: RainLayerProps) {
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

  // Las especificaciones sólo cambian con el tema o con el encendido. react-map-gl
  // compara propiedad por propiedad, así que un objeto nuevo con los mismos
  // valores no produce escrituras — pero memorizarlas evita incluso esa
  // comparación en cada repintado del árbol.
  const heat = useMemo(() => rainHeatLayer(theme, visible), [theme, visible])
  const halo = useMemo(() => rainHaloLayer(theme, visible), [theme, visible])
  const core = useMemo(() => rainCoreLayer(theme, visible), [theme, visible])
  const nucleus = useMemo(() => rainNucleusLayer(theme, visible), [theme, visible])
  const ring = useMemo(() => rainRiskRingLayer(theme, visible), [theme, visible])
  const text = useMemo(() => rainTextLayer(theme, visible), [theme, visible])

  return (
    /*
     * Sin `interactiveLayerIds` y sin `promoteId`: la lluvia no se selecciona, no
     * abre ficha y no debe robarle el clic al incidente que tenga debajo. Es
     * contexto, y el contexto no se toca.
     */
    <Source id={RAIN_SOURCE_ID} type="geojson" data={data}>
      {/* Los seis con el mismo `beforeId`: cada uno se inserta justo antes del
          ancla, así que el orden de inserción es el orden de dibujo.

          El campo de calor va el PRIMERO —el más abajo—. Es la misma fuente que
          los discos, sin `promoteId` ni filtro: lo único que cambia es que se
          alimenta del punto en vez del radio, que es justo lo que permite que
          el relevo por zoom no descargue nada nuevo. */}
      <Layer beforeId={RAIN_BEFORE_ID} {...heat} />
      <Layer beforeId={RAIN_BEFORE_ID} {...halo} />
      <Layer beforeId={RAIN_BEFORE_ID} {...core} />
      <Layer beforeId={RAIN_BEFORE_ID} {...nucleus} />
      <Layer beforeId={RAIN_BEFORE_ID} {...ring} />
      {/* El texto va el ÚLTIMO: queda inmediatamente debajo del ancla, o sea
          encima de sus propias manchas y debajo del cono, los sismos y los
          incidentes. Moverlo de sitio en este bloque cambia la jerarquía. */}
      <Layer beforeId={RAIN_BEFORE_ID} {...text} />
    </Source>
  )
}
