import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { RefObject } from 'react'
import {
  AttributionControl,
  GeolocateControl,
  Layer,
  Map,
  NavigationControl,
  ScaleControl,
  Source,
} from 'react-map-gl/maplibre'
import type {
  ErrorEvent,
  MapEvent,
  MapLayerMouseEvent,
  MapRef,
} from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'
// Debe ejecutarse antes de que se instancie el mapa: sin esto el worker queda
// apuntando a una URL inexistente en produccion y el lienzo sale en blanco.
import '@/lib/maplibreWorker'

import type { SeismicEvent } from '@/api/seismicTypes'
import type { Incident } from '@/api/types'
import {
  INITIAL_VIEW_STATE,
  MAP_ATTRIBUTION,
  MAP_MAX_BOUNDS,
  mapStyleFor,
} from '@/config/map'
import type { Theme } from '@/hooks/useTheme'
import type { ConeCollection, ReachCollection } from '@/lib/overlayGeojson'
import { toFeatureCollection } from '@/lib/geojson'
import { OutagePinLayer } from './OutagePinLayer'
import {
  incidentIconLayer,
  seismicIconLayer,
} from './emergencyIconLayers'
import { useEmergencyIcons } from '@/hooks/useEmergencyIcons'
import { MAGNITUDE_COLOR_EXPRESSION } from '@/domain/seismicSymbology'
import type { ExpressionSpecification, Map as MapLibreMap } from 'maplibre-gl'
import { RainLayer } from './RainLayer'
import { RoadClosureLayer } from './RoadClosureLayer'
import { SeismicHazardLayer } from './SeismicHazardLayer'
import type { RainLayerState } from '@/hooks/useRainLayer'
import type { RoadClosureState } from '@/hooks/useRoadClosures'
import type { SeismicHazardState } from '@/hooks/useSeismicHazard'
import { toSeismicFeatureCollection } from '@/lib/seismicGeojson'
import { attachMapDiagnostics } from '@/lib/mapDiagnostics'
import {
  INCIDENT_HIT_LAYER_ID,
  INCIDENT_SOURCE_ID,
  alertHaloLayer,
  casingLayer,
  closedRingLayer,
  coreLayer,
  hitLayer,
  selectedLayer,
  unverifiedLayer,
} from './incidentLayers'
import {
  SEISMIC_HIT_LAYER_ID,
  SEISMIC_SOURCE_ID,
  seismicCoreLayer,
  seismicHitLayer,
  seismicRingLayer,
  seismicSelectedLayer,
} from './seismicLayers'
import {
  CONE_SOURCE_ID,
  REACH_SOURCE_ID,
  coneFillLayer,
  coneLineLayer,
  reachFillLayer,
  reachLineLayer,
} from './overlayLayers'

interface IncidentMapProps {
  /**
   * La referencia se crea en `App` y se pasa hacia abajo, en vez de vivir acá
   * dentro. Es lo que permite que el panel de capas —que es hermano del mapa,
   * no descendiente— pueda ordenar un `flyTo` sin levantar todo el estado del
   * mapa ni recurrir a un contexto.
   */
  mapRef: RefObject<MapRef | null>
  /** Incidentes que van a las capas GeoJSON. Excluye los cortes. */
  incidents: readonly Incident[]
  /** Cortes de suministro: se dibujan como pines DOM, no como círculos. */
  outages: readonly Incident[]
  seismic: readonly SeismicEvent[]
  /** Capas encendidas desde el control de capas. */
  showIncidents: boolean
  showSeismic: boolean
  selectedCode: string | null
  selectedUsgsId: string | null
  onSelect: (code: string | null) => void
  onSelectSeismic: (usgsId: string | null) => void
  /** Tema activo: decide el estilo del mapa base. */
  theme: Theme
  /** Polígonos derivados. Fuentes propias, separadas de la señal observada. */
  reach: ReachCollection
  cone: ConeCollection
  /** Capa de referencia de amenaza sísmica, con su propia carga diferida. */
  hazard: SeismicHazardState
  /** Lluvia pronosticada. También diferida: no se pide hasta el primer encendido. */
  rain: RainLayerState
  /**
   * Cortes e intervenciones de la vía (MOP + MTT). Diferida como las otras dos.
   *
   * Es capa de CONTEXTO: no entra en `interactiveLayerIds` y no abre ficha.
   * `road_closure` está fuera de `CORRELATABLE_EVENT_TYPES` y entra con
   * confianza 0,0 — no es un siniestro y no puede robarle el clic a uno.
   */
  closures: RoadClosureState
}

/*
 * Props del `<Map>` que no dependen de nada: constantes de módulo para que su
 * identidad no cambie entre renders. react-map-gl compara las opciones con
 * `deepEqual` en cada render; con un objeto nuevo compara de balde.
 */
const MAP_STYLE_BOX = { position: 'absolute', inset: 0 } as const
const TOUCH_ZOOM_ROTATE = { around: 'center' } as const
const GEOLOCATE_OPTIONS = { enableHighAccuracy: true } as const
const SEISMIC_ICON_COLOR = MAGNITUDE_COLOR_EXPRESSION as unknown as ExpressionSpecification

/**
 * El lienzo GIS, detrás de un `memo`.
 *
 * Todo lo que recibe tiene identidad estable (arreglos memorizados en `App`,
 * setters de estado y colecciones que react-query comparte cuando el sondeo
 * trae lo mismo), así que sólo se vuelve a renderizar cuando cambia un dato
 * que dibuja. Antes lo hacía una vez por segundo por un reloj que ni usaba.
 */
export const IncidentMap = memo(function IncidentMap({
  mapRef,
  incidents,
  outages,
  seismic,
  showIncidents,
  showSeismic,
  selectedCode,
  selectedUsgsId,
  onSelect,
  onSelectSeismic,
  theme,
  reach,
  cone,
  hazard,
  rain,
  closures,
}: IncidentMapProps) {
  /*
   * La instancia nativa, en ESTADO y no leída del ref durante el render.
   *
   * `mapRef.current` es `null` en el primer render y asignarlo no dispara uno
   * nuevo: un hook alimentado desde el ref se quedaría con `null` para siempre.
   * `onLoad` sí provoca un render, y es además el momento correcto — antes de
   * `load` el estilo todavía no acepta `addImage`.
   */
  const [instance, setInstance] = useState<MapLibreMap | null>(null)

  /*
   * Registro de los iconos SDF. Devuelve `true` cuando hay al menos uno
   * instalado; hasta entonces las capas `symbol` no se montan, porque un
   * `icon-image` que apunta a una imagen inexistente hace que MapLibre emita un
   * error por cada punto y no dibuje nada.
   */
  const iconsReady = useEmergencyIcons(instance)

  // Se recalcula solo cuando cambia el arreglo de incidentes, no en cada
  // repintado: el polling entrega un arreglo nuevo cada minuto, no cada frame.
  const data = useMemo(() => toFeatureCollection(incidents), [incidents])
  const seismicData = useMemo(() => toSeismicFeatureCollection(seismic), [seismic])

  // Sólo las capas visibles reciben el toque. Si no se filtrara, un sismo
  // oculto seguiría capturando el clic sobre el incidente que hay debajo.
  const interactiveLayers = useMemo(() => {
    const ids: string[] = []
    if (showIncidents) ids.push(INCIDENT_HIT_LAYER_ID)
    if (showSeismic) ids.push(SEISMIC_HIT_LAYER_ID)
    return ids
  }, [showIncidents, showSeismic])

  const handleClick = useCallback(
    (event: MapLayerMouseEvent) => {
      // Los incidentes tienen prioridad sobre los sismos: si ambos caen bajo el
      // dedo, gana la emergencia. El orden de `interactiveLayerIds` no lo
      // garantiza, así que se resuelve explícitamente.
      const features = event.features ?? []
      const incident = features.find((f) => typeof f.properties?.['code'] === 'string')
      const quake = features.find((f) => typeof f.properties?.['usgs_id'] === 'string')

      if (!incident && quake) {
        onSelect(null)
        onSelectSeismic(String(quake.properties!['usgs_id']))
        return
      }

      const code = incident?.properties?.['code']
      if (typeof code !== 'string') {
        onSelect(null)
        onSelectSeismic(null)
        return
      }

      onSelectSeismic(null)
      onSelect(code)

      // La tarjeta ocupa el tercio inferior en teléfono. Centrar el incidente
      // sin compensar lo dejaria justo debajo de la tarjeta, que es donde no se
      // ve. El desplazamiento vertical lo saca de ahi.
      const map = mapRef.current
      if (map) {
        map.easeTo({
          center: event.lngLat,
          offset: [0, -Math.min(window.innerHeight * 0.18, 160)],
          duration: 450,
        })
      }
    },
    [mapRef, onSelect, onSelectSeismic],
  )

  /**
   * Un mapa en blanco sin nada en consola es el peor modo de falla: no se sabe
   * si falló el estilo, el WebGL, el worker o el encuadre.
   *
   * # Por qué un ref de callback y no un efecto
   *
   * La versión anterior enganchaba las sondas en un `useEffect(…, [])` que leía
   * `mapRef.current`. Nunca funcionó: react-map-gl crea el mapa DESPUÉS de
   * resolver `import('maplibre-gl')`, y hasta entonces su `useImperativeHandle`
   * publica `null`. El efecto corría una sola vez, encontraba `null` y
   * retornaba. Con `?debug=1` no aparecía ni una línea `[AlertaV/mapa]`.
   *
   * Un ref de callback lo llama React cada vez que el handle cambia, así que
   * recibe el mapa en cuanto existe — antes de `load`, que es lo que el
   * watchdog necesita para observar un `load` que nunca llega.
   */
  const detachDiagnostics = useRef<(() => void) | null>(null)
  const setMapRef = useCallback(
    (ref: MapRef | null) => {
      mapRef.current = ref
      if (ref && !detachDiagnostics.current) {
        detachDiagnostics.current = attachMapDiagnostics(ref.getMap())
      }
    },
    [mapRef],
  )
  useEffect(
    () => () => {
      detachDiagnostics.current?.()
      detachDiagnostics.current = null
    },
    [],
  )

  const handleError = useCallback((event: ErrorEvent) => {
    console.error('[AlertaV/mapa] error', event.error ?? event)
  }, [])

  const handleLoad = useCallback((event: MapEvent) => setInstance(event.target), [])

  /*
   * El cursor se escribe directo en el lienzo, no en estado de React.
   *
   * Como estado, cada entrada o salida de un incidente y cada arrastre
   * repintaban el árbol entero del mapa. `grab` y `grabbing` los pone la hoja de
   * estilos de MapLibre (`.maplibregl-interactive` y su `:active`); acá sólo se
   * agrega `pointer` sobre lo clicable, y nunca durante un arrastre: el puntero
   * pasa por encima de incidentes y no debe parpadear.
   */
  const dragging = useRef(false)
  const handleMouseEnter = useCallback((event: MapLayerMouseEvent) => {
    if (!dragging.current) event.target.getCanvas().style.cursor = 'pointer'
  }, [])
  const handleMouseLeave = useCallback((event: MapLayerMouseEvent) => {
    event.target.getCanvas().style.cursor = ''
  }, [])
  const handleDragStart = useCallback((event: MapEvent) => {
    dragging.current = true
    event.target.getCanvas().style.cursor = ''
  }, [])
  const handleDragEnd = useCallback(() => {
    dragging.current = false
  }, [])

  // Especificaciones de capa que dependen de una prop: se arman sólo cuando
  // cambia esa prop, no en cada render.
  const incidentIcons = useMemo(() => incidentIconLayer(theme), [theme])
  const seismicIcons = useMemo(() => seismicIconLayer(theme, SEISMIC_ICON_COLOR), [theme])
  const selectedIncident = useMemo(() => selectedLayer(selectedCode), [selectedCode])
  const selectedQuake = useMemo(() => seismicSelectedLayer(selectedUsgsId), [selectedUsgsId])

  const handleSelectOutage = useCallback(
    (code: string) => {
      onSelectSeismic(null)
      onSelect(code)
    },
    [onSelect, onSelectSeismic],
  )

  return (
    <Map
      ref={setMapRef}
      initialViewState={INITIAL_VIEW_STATE}
      /*
       * Cambiar `mapStyle` dispara `map.setStyle()`. Los `<Source>` se vuelven a
       * crear solos al recibir `styledata`, así que las capas de incidentes y
       * sismos sobreviven al cambio y la cámara no se mueve. Remontar el `<Map>`
       * con una `key` también funcionaría, pero perdería el encuadre.
       */
      mapStyle={mapStyleFor(theme)}
      maxBounds={MAP_MAX_BOUNDS}
      minZoom={7}
      maxZoom={17}
      style={MAP_STYLE_BOX}
      interactiveLayerIds={interactiveLayers}
      onClick={handleClick}
      onLoad={handleLoad}
      onError={handleError}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      attributionControl={false}
      // Un mapa de emergencias se consulta en la calle y con una mano: los
      // gestos que rotan o inclinan solo estorban.
      dragRotate={false}
      pitchWithRotate={false}
      touchZoomRotate={TOUCH_ZOOM_ROTATE}
    >
      <AttributionControl compact customAttribution={MAP_ATTRIBUTION} />
      <NavigationControl position="top-right" showCompass={false} />
      <GeolocateControl
        position="top-right"
        trackUserLocation
        positionOptions={GEOLOCATE_OPTIONS}
      />
      <ScaleControl position="bottom-left" unit="metric" />

      {/*
        Amenaza sísmica: capa de referencia, la más baja de todas. El `<Source>`
        sólo entra al árbol cuando el usuario la enciende por primera vez —de
        ahí `hasMounted`— y a partir de entonces se queda montado para siempre:
        apagarla vuelve a ser un cambio de `visibility`, no una descarga.
      */}
      {hazard.hasMounted && (
        <SeismicHazardLayer grid={hazard.grid} visible={hazard.enabled} theme={theme} />
      )}

      {/*
        Polígonos derivados primero: son estimaciones calculadas y van por
        debajo de todo lo observado.
      */}
      {showSeismic && (
        <Source id={REACH_SOURCE_ID} type="geojson" data={reach}>
          <Layer {...reachFillLayer} />
          <Layer {...reachLineLayer} />
        </Source>
      )}

      <Source id={CONE_SOURCE_ID} type="geojson" data={cone}>
        <Layer {...coneFillLayer} />
        <Layer {...coneLineLayer} />
      </Source>

      {/*
        Lluvia pronosticada. Va DESPUÉS del cono en el árbol a propósito, no por
        estética: se ancla con `beforeId` a `wind-cone-fill` —la única capa
        propia que está montada siempre— y cuando un cambio de tema fuerza a
        MapLibre a reconstruir el estilo, react-map-gl vuelve a añadir las capas
        en orden de montaje. Si este bloque fuera antes, su ancla todavía no
        existiría y MapLibre descartaría la capa en silencio.

        Como la amenaza sísmica: el `<Source>` sólo entra al árbol cuando el
        usuario la enciende por primera vez, y a partir de ahí se queda.
      */}
      {rain.hasMounted && (
        <RainLayer
          data={rain.data}
          visible={rain.enabled}
          theme={theme}
        />
      )}

      {/*
        Cortes de ruta. Después del cono y por el mismo motivo que la lluvia:
        se anclan con `beforeId` a `wind-cone-fill` —la única capa propia que
        está montada siempre— y react-map-gl vuelve a añadir las capas en orden
        de montaje cuando un cambio de tema reconstruye el estilo. Si este
        bloque fuera antes, su ancla todavía no existiría y MapLibre
        descartaría las capas en silencio.

        Va DESPUÉS de la lluvia porque un corte es un hecho de la vía y la
        lluvia es condición ambiental: si se solapan, el corte tiene que quedar
        encima. Sigue por debajo de sismos e incidentes, que son el sujeto.

        Como las otras dos: el `<Source>` sólo entra al árbol cuando el usuario
        la enciende por primera vez, y a partir de ahí se queda.
      */}
      {closures.hasMounted && (
        <RoadClosureLayer data={closures.data} visible={closures.enabled} theme={theme} />
      )}

      {/* Los sismos van debajo: son contexto, no el sujeto del mapa. */}
      {showSeismic && (
        <Source id={SEISMIC_SOURCE_ID} type="geojson" data={seismicData}>
          <Layer {...seismicRingLayer} />
          <Layer {...seismicCoreLayer} />
          {iconsReady && (
            <Layer {...seismicIcons} />
          )}
          <Layer {...selectedQuake} />
          <Layer {...seismicHitLayer} />
        </Source>
      )}

      {/*
        Los cortes se interceptan antes de llegar a la fuente GeoJSON: `App` ya
        los separó del arreglo `incidents`. Acá se dibujan como marcadores DOM,
        que es lo que permite darles forma de gota y acento por empresa.
      */}
      <OutagePinLayer
        outages={outages}
        selectedCode={selectedCode}
        onSelect={handleSelectOutage}
      />

      {showIncidents && (
      <Source
        id={INCIDENT_SOURCE_ID}
        type="geojson"
        data={data}
        promoteId="code"
      >
        <Layer {...alertHaloLayer} />
        <Layer {...casingLayer} />
        {/*
          El disco sólo se dibuja mientras los iconos no estén listos. Es el
          estado de un puñado de milisegundos entre el primer cuadro del mapa y
          el registro de las imágenes: sin él, los incidentes parpadearían
          apareciendo de la nada en vez de afinarse.
        */}
        {!iconsReady && <Layer {...coreLayer} />}
        {iconsReady && <Layer {...incidentIcons} />}
        <Layer {...closedRingLayer} />
        <Layer {...unverifiedLayer} />
        <Layer {...selectedIncident} />
        <Layer {...hitLayer} />
      </Source>
      )}
    </Map>
  )
})
