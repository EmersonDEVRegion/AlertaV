import { useMemo } from 'react'
import { Layer, Source, useMap } from 'react-map-gl/maplibre'
import type { RoadClosureCollection } from '@/api/roadClosureTypes'
import type { Theme } from '@/hooks/useTheme'
import {
  ROAD_CLOSURE_BEFORE_ID,
  ROAD_CLOSURE_LAYER_IDS,
  ROAD_CLOSURE_SOURCE_ID,
  closureBodyLayer,
  closureCutRingLayer,
  closureHitLayer,
  closureColor,
  closureMttDotLayer,
} from './roadClosureLayers'
import { closureIconLayer } from './emergencyIconLayers'
import { useEmergencyIcons } from '@/hooks/useEmergencyIcons'
import { useLayerReanchor } from './useLayerReanchor'

/**
 * Capa de cortes e intervenciones de la vía.
 *
 * # Contexto, no emergencia
 *
 * Es la distinción que gobierna cada decisión del componente. `road_closure`
 * está fuera de `CORRELATABLE_EVENT_TYPES` en el backend y entra con confianza
 * 0,0: no crea incidentes, no mueve la confianza de ninguno y **no abre ficha**.
 * Una emergencia del MOP sigue vigente durante semanas.
 *
 * De ahí que la capa no declare `promoteId` ni entre en `interactiveLayerIds`
 * del `<Map>`: un corte no debe robarle el clic al incidente que tenga debajo.
 * La capa `closure-hit` existe igualmente —con su radio generoso— porque el
 * popup se resuelve con `queryRenderedFeatures` en el manejador del mapa, que
 * es donde la prioridad entre capas se decide explícitamente y no por el orden
 * de un arreglo.
 *
 * # Igual que la lluvia y por los mismos motivos
 *
 * El `<Source>` recibe la MISMA referencia de objeto mientras el dato no cambie:
 * react-query hace *structural sharing* y `EMPTY_ROAD_CLOSURES` es una constante
 * compartida. Es lo que evita que MapLibre vuelva a subir el GeoJSON en cada
 * repintado del árbol.
 *
 * Y como la lluvia, se apaga por `visibility` y nunca desmontando: desmontar
 * destruiría el GeoJSON ya subido al worker y volver a encender la capa pagaría
 * la subida otra vez.
 */

interface RoadClosureLayerProps {
  data: RoadClosureCollection
  /** Encendida o apagada. Nunca desmonta: alterna `visibility`. */
  visible: boolean
  theme: Theme
}

/**
 * Ancla única: el cono de viento.
 *
 * La misma que usa la lluvia, y por la misma razón exacta: es la única capa
 * propia que está montada SIEMPRE. Anclar a incidentes o a sismos —que viven
 * bajo un interruptor— sería un modo de falla que sólo aparece cuando el
 * usuario apaga esa otra capa primero, y cuyo síntoma es que MapLibre descarta
 * esta capa entera sin dibujar nada.
 *
 * Sigue siendo una lista porque `useLayerReanchor` la comparte con la lluvia y
 * la amenaza sísmica, y una firma distinta por capa invitaría a que cada una
 * reimplementara lo suyo.
 */
const ROAD_CLOSURE_ANCHORS = [ROAD_CLOSURE_BEFORE_ID] as const

export function RoadClosureLayer({ data, visible, theme }: RoadClosureLayerProps) {
  const { current: map } = useMap()
  const instance = map?.getMap() ?? null

  /*
   * Re-anclaje tras un cambio de estilo.
   *
   * Cambiar de tema llama a `map.setStyle()`, que vacía el arreglo de capas.
   * react-map-gl vuelve a añadir cada `<Layer>` al recibir `styledata`, en el
   * orden en que están montados los componentes — por eso este bloque va
   * DESPUÉS del cono en `IncidentMap`, para que su ancla ya exista cuando le
   * toque. Aun así el orden de reconstrucción de MapLibre no es un contrato
   * público, y el precio de equivocarse es que los cortes tapen los pines de
   * emergencia.
   */
  useLayerReanchor(instance, ROAD_CLOSURE_LAYER_IDS, ROAD_CLOSURE_ANCHORS)

  // Las especificaciones sólo cambian con el tema o con el encendido.
  // react-map-gl compara propiedad por propiedad, así que un objeto nuevo con
  // los mismos valores no produce escrituras — pero memorizarlas evita incluso
  // esa comparación en cada repintado del árbol.
  const cutRing = useMemo(() => closureCutRingLayer(theme, visible), [theme, visible])
  const body = useMemo(() => closureBodyLayer(theme, visible), [theme, visible])
  const mttDot = useMemo(() => closureMttDotLayer(theme, visible), [theme, visible])
  // El icono va encima del rombo, no en su lugar: el tamaño codifica severidad.
  const icon = useMemo(() => closureIconLayer(theme, closureColor(theme)), [theme])
  const iconsReady = useEmergencyIcons(instance)
  const hit = useMemo(() => closureHitLayer(visible), [visible])

  return (
    <Source id={ROAD_CLOSURE_SOURCE_ID} type="geojson" data={data}>
      {/* Los cuatro con el mismo `beforeId`: cada uno se inserta justo antes
          del ancla, así que el orden de inserción ES el orden de dibujo.

          El anillo de corte va el PRIMERO —el más abajo— para que el cuerpo se
          dibuje encima y el anillo se lea como halo y no como borde. Moverlo de
          sitio en este bloque cambia la jerarquía visual de la capa. */}
      <Layer beforeId={ROAD_CLOSURE_BEFORE_ID} {...cutRing} />
      <Layer beforeId={ROAD_CLOSURE_BEFORE_ID} {...body} />
      <Layer beforeId={ROAD_CLOSURE_BEFORE_ID} {...mttDot} />
      {iconsReady && <Layer beforeId={ROAD_CLOSURE_BEFORE_ID} {...icon} />}
      {/* El objetivo de toque va el ÚLTIMO: queda inmediatamente debajo del
          ancla, o sea encima de sus propios rombos. Es invisible, así que no
          altera nada de lo que se ve. */}
      <Layer beforeId={ROAD_CLOSURE_BEFORE_ID} {...hit} />
    </Source>
  )
}
