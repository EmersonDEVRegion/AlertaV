import { useCallback, useEffect, useMemo, useState } from 'react'
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
import { RainLayer } from './RainLayer'
import { SeismicHazardLayer } from './SeismicHazardLayer'
import type { RainLayerState } from '@/hooks/useRainLayer'
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
}

export function IncidentMap({
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
}: IncidentMapProps) {
  const [hovering, setHovering] = useState(false)
  const [dragging, setDragging] = useState(false)

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
    [onSelect, onSelectSeismic],
  )

  /**
   * Un mapa en blanco sin nada en consola es el peor modo de falla: no se sabe
   * si falló el estilo, el WebGL, el worker o el encuadre.
   *
   * Las sondas se enganchan en un efecto y no en `onLoad` a propósito: el fallo
   * más difícil de ver es justamente aquel en el que `load` no se dispara nunca.
   * Un handler de `load` no puede observar su propia ausencia; un `setTimeout`
   * registrado al montar, sí. Ver `lib/mapDiagnostics.ts`.
   */
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map) return
    return attachMapDiagnostics(map)
  }, [])

  const handleError = useCallback((event: ErrorEvent) => {
    console.error('[AlertaV/mapa] error', event.error ?? event)
  }, [])

  return (
    <Map
      ref={mapRef}
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
      style={{ position: 'absolute', inset: 0 }}
      interactiveLayerIds={interactiveLayers}
      onClick={handleClick}
      onError={handleError}
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      onDragStart={() => setDragging(true)}
      onDragEnd={() => setDragging(false)}
      /*
       * Tres estados, en orden de prioridad:
       *
       *   grabbing  mientras se arrastra — gana sobre todo lo demás, porque
       *             durante un arrastre el puntero pasa por encima de
       *             incidentes y el cursor no debe parpadear a `pointer`.
       *   pointer   sobre un incidente clicable.
       *   grab      por defecto: el mapa se puede tomar y mover.
       *
       * MapLibre trae `default` en reposo, que no comunica que el mapa sea
       * arrastrable. `grab`/`grabbing` es la convención de todo mapa web y
       * además da un contraste mucho mayor que la flecha del sistema sobre una
       * cartografía clara.
       */
      cursor={dragging ? 'grabbing' : hovering ? 'pointer' : 'grab'}
      attributionControl={false}
      // Un mapa de emergencias se consulta en la calle y con una mano: los
      // gestos que rotan o inclinan solo estorban.
      dragRotate={false}
      pitchWithRotate={false}
      touchZoomRotate={{ around: 'center' }}
    >
      <AttributionControl compact customAttribution={MAP_ATTRIBUTION} />
      <NavigationControl position="top-right" showCompass={false} />
      <GeolocateControl
        position="top-right"
        trackUserLocation
        positionOptions={{ enableHighAccuracy: true }}
      />
      <ScaleControl position="bottom-left" unit="metric" />

      {/*
        Amenaza sísmica: capa de referencia, la más baja de todas. El `<Source>`
        sólo entra al árbol cuando el usuario la enciende por primera vez —de
        ahí `hasMounted`— y a partir de entonces se queda montado para siempre:
        apagarla vuelve a ser un cambio de `visibility`, no una descarga.
      */}
      {hazard.hasMounted && (
        <SeismicHazardLayer
          visible={hazard.enabled}
          theme={theme}
          attempt={hazard.attempt}
          onLoaded={hazard.onLoaded}
          onError={hazard.onError}
        />
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

      {/* Los sismos van debajo: son contexto, no el sujeto del mapa. */}
      {showSeismic && (
        <Source id={SEISMIC_SOURCE_ID} type="geojson" data={seismicData}>
          <Layer {...seismicRingLayer} />
          <Layer {...seismicCoreLayer} />
          <Layer {...seismicSelectedLayer(selectedUsgsId)} />
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
        onSelect={(code) => {
          onSelectSeismic(null)
          onSelect(code)
        }}
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
        <Layer {...coreLayer} />
        <Layer {...closedRingLayer} />
        <Layer {...unverifiedLayer} />
        <Layer {...selectedLayer(selectedCode)} />
        <Layer {...hitLayer} />
      </Source>
      )}
    </Map>
  )
}
