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
  LayerProps,
  MapLayerMouseEvent,
  MapRef,
} from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'
// Debe ejecutarse antes de que se instancie el mapa: sin esto el worker queda
// apuntando a una URL inexistente en produccion y el lienzo sale en blanco.
import '@/lib/maplibreWorker'

import type { FeatureCollection, Point } from 'geojson'
import type { SeismicEvent } from '@/api/seismicTypes'
import type { Incident } from '@/api/types'
import type { WaterCut } from '@/api/waterCutTypes'
import {
  INITIAL_VIEW_STATE,
  MAP_ATTRIBUTION,
  MAP_MAX_BOUNDS,
  mapStyleFor,
} from '@/config/map'
import type { IncidentLayerKey } from '@/domain/families'
import type { Theme } from '@/hooks/useTheme'
import { toConeCollection, type ReachCollection } from '@/lib/overlayGeojson'
import {
  toFeatureCollection,
  toOutageFeatureCollection,
  toWaterCutFeatureCollection,
} from '@/lib/geojson'
import {
  clearSelection,
  selectIncident,
  selectSeismic,
  selectWaterCut,
  useSelectedIncidentCode,
  useSelectedSeismicId,
  useSelectedWaterCutId,
} from '@/lib/selectionStore'
import {
  incidentIconLayer,
  seismicIconLayer,
} from './emergencyIconLayers'
import { useEmergencyIcons } from '@/hooks/useEmergencyIcons'
import { useSelectedFire } from '@/hooks/useSelectedFire'
import { MAGNITUDE_COLOR_EXPRESSION } from '@/domain/seismicSymbology'
import type {
  ExpressionSpecification,
  FilterSpecification,
  GeoJSONSource,
  Map as MapLibreMap,
} from 'maplibre-gl'
import { RainLayer } from './RainLayer'
import { RoadClosureLayer } from './RoadClosureLayer'
import { SeismicHazardLayer } from './SeismicHazardLayer'
import type { RainLayerState } from '@/hooks/useRainLayer'
import type { RoadClosureState } from '@/hooks/useRoadClosures'
import type { SeismicHazardState } from '@/hooks/useSeismicHazard'
import { toSeismicFeatureCollection } from '@/lib/seismicGeojson'
import { attachMapDiagnostics } from '@/lib/mapDiagnostics'
import { focusOffset } from '@/lib/cameraOffset'
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
  OUTAGE_CLUSTER_LAYER_ID,
  OUTAGE_CLUSTER_MAX_ZOOM,
  OUTAGE_CLUSTER_PROPERTIES,
  OUTAGE_CLUSTER_RADIUS,
  OUTAGE_HIT_LAYER_ID,
  OUTAGE_SELECTED_LAYER_ID,
  OUTAGE_SOURCE_ID,
  outageClusterCountLayer,
  outageClusterLayer,
  outageGlyphLayer,
  outageHitLayer,
  outagePinLayer,
  outageSelectedLayer,
} from './outageLayers'
import {
  WATER_HIT_LAYER_ID,
  WATER_SELECTED_LAYER_ID,
  WATER_SOURCE_ID,
  waterGlyphLayer,
  waterHitLayer,
  waterPinLayer,
  waterSelectedLayer,
} from './waterCutLayers'
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
  /**
   * TODOS los incidentes que van a la fuente GeoJSON, sin filtrar por capa.
   * Excluye los cortes de luz, que tienen su fuente propia.
   *
   * Apagar una capa no cambia este arreglo: cambia `visibleFamilies`, que es un
   * `filter` de MapLibre. Así apagar «Tránsito» no vuelve a subir los datos al
   * worker ni lo obliga a reindexar cada punto.
   */
  incidents: readonly Incident[]
  /** Familias encendidas de las que viven en `incidents` (no incluye `power`). */
  visibleFamilies: readonly IncidentLayerKey[]
  /**
   * Lo del historial que ya salió del mapa (con las capas encendidas). No se
   * dibuja: sólo sirve para marcar con un pin fantasma el que se abra desde el
   * historial, que si no quedaría como una ficha sin lugar.
   */
  offMap: readonly Incident[]
  /** Cortes de luz ya filtrados por capa y por empresa. Van agrupados. */
  outages: readonly Incident[]
  /**
   * Cortes de agua vigentes (Esval), con y sin punto: sólo se dibujan los que
   * lo tienen. Encender o apagar su fila cambia `showWater`, no este arreglo.
   */
  waterCuts: readonly WaterCut[]
  showWater: boolean
  seismic: readonly SeismicEvent[]
  showSeismic: boolean
  /** Tema activo: decide el estilo del mapa base. */
  theme: Theme
  /** Radio de percepción sísmica. Polígonos derivados, en fuente propia. */
  reach: ReachCollection
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


type WithFilter = { filter?: FilterSpecification }

/**
 * Suma el filtro de familias al filtro propio de la capa.
 *
 * Es lo que reemplaza a volver a filtrar el arreglo en `App`: cambiar el filtro
 * de una capa es un `setFilter`, sin datos nuevos para el worker.
 */
function withFamilies<T extends WithFilter>(spec: T, families: FilterSpecification): T {
  return {
    ...spec,
    filter: spec.filter ? (['all', families, spec.filter] as FilterSpecification) : families,
  }
}

/*
 * Lo efímero, en componentes que se suscriben solos.
 *
 * La selección vive en `lib/selectionStore`. Estos tres componentes son los
 * únicos del mapa que la leen, así que tocar un pin repinta un anillo —una
 * capa— y no el lienzo con todas sus fuentes.
 *
 * `<Source>` les inyecta `source` con `cloneElement` porque son hijos directos;
 * como componentes intermedios, tienen que reenviarlo a su `<Layer>` o MapLibre
 * rechaza la capa por no tener fuente.
 */
interface SourceChild {
  source?: string
}

const SelectedIncidentLayer = memo(function SelectedIncidentLayer({
  source,
  families,
}: SourceChild & { families: FilterSpecification }) {
  const code = useSelectedIncidentCode()
  const spec = useMemo(() => withFamilies(selectedLayer(code), families), [code, families])
  return <Layer {...spec} source={source} />
})

const SelectedOutageLayer = memo(function SelectedOutageLayer({
  source,
  theme,
}: SourceChild & { theme: Theme }) {
  const code = useSelectedIncidentCode()
  const spec = useMemo(() => outageSelectedLayer(theme, code), [theme, code])
  return <Layer {...spec} source={source} />
})

const SelectedWaterLayer = memo(function SelectedWaterLayer({
  source,
  theme,
  visible,
}: SourceChild & { theme: Theme; visible: boolean }) {
  const id = useSelectedWaterCutId()
  const spec = useMemo(() => waterSelectedLayer(theme, id, visible), [theme, id, visible])
  return <Layer {...spec} source={source} />
})

const SelectedQuakeLayer = memo(function SelectedQuakeLayer({ source }: SourceChild) {
  const usgsId = useSelectedSeismicId()
  const spec = useMemo(() => seismicSelectedLayer(usgsId), [usgsId])
  return <Layer {...spec} source={source} />
})

const EMPTY_GHOST: FeatureCollection<Point> = {
  type: 'FeatureCollection',
  features: [],
}

const GHOST_RING = {
  id: 'incident-ghost-ring',
  type: 'circle',
  paint: {
    'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 9, 14, 14],
    'circle-color': '#a1a1aa',
    'circle-opacity': 0.22,
    'circle-stroke-color': '#e4e4e7',
    'circle-stroke-width': 2.5,
    'circle-stroke-opacity': 0.95,
  },
} as const satisfies LayerProps
const GHOST_DOT = {
  id: 'incident-ghost-dot',
  type: 'circle',
  paint: {
    'circle-radius': 3.5,
    'circle-color': '#52525b',
    'circle-stroke-color': '#ffffff',
    'circle-stroke-width': 1.5,
  },
} as const satisfies LayerProps

/**
 * Pin fantasma: el incidente abierto desde el historial que ya no está en el
 * mapa. Gris y hueco, para que se lea «estuvo acá» y no «está pasando».
 *
 * Siempre montado (vacío si no hay nada que marcar), por la misma razón que el
 * resto de las fuentes: un `<Source>` que entra y sale se agrega al final del
 * estilo y cambia el orden de las capas.
 */
const GhostSelection = memo(function GhostSelection({
  offMap,
}: {
  offMap: readonly Incident[]
}) {
  const code = useSelectedIncidentCode()
  const data = useMemo(() => {
    const ghost = code === null ? undefined : offMap.find((i) => i.code === code)
    if (!ghost) return EMPTY_GHOST
    return {
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [ghost.lon, ghost.lat] },
          properties: { code: ghost.code },
        },
      ],
    } satisfies FeatureCollection<Point>
  }, [code, offMap])
  return (
    <Source id="incident-ghost" type="geojson" data={data}>
      <Layer {...GHOST_RING} />
      <Layer {...GHOST_DOT} />
    </Source>
  )
})

/**
 * El cono de viento del incendio seleccionado.
 *
 * Está SIEMPRE montado, aunque vacío: la lluvia y los cortes de ruta se anclan
 * con `beforeId` a `wind-cone-fill` (ver más abajo). Pide el viento con la misma
 * clave que la ficha, así que es una sola llamada a Open-Meteo.
 */
const WindConeSource = memo(function WindConeSource({
  incidents,
}: {
  incidents: readonly Incident[]
}) {
  const { fire, cone } = useSelectedFire(incidents)
  const data = useMemo(() => toConeCollection(fire, cone), [fire, cone])
  return (
    <Source id={CONE_SOURCE_ID} type="geojson" data={data}>
      <Layer {...coneFillLayer} />
      <Layer {...coneLineLayer} />
    </Source>
  )
})

/**
 * El lienzo GIS, detrás de un `memo`.
 *
 * Todo lo que recibe tiene identidad estable (arreglos memorizados en `App` y
 * colecciones que react-query comparte cuando el sondeo trae lo mismo), así que
 * sólo se vuelve a renderizar cuando cambia un dato que dibuja o una capa se
 * enciende. La selección no pasa por acá: la leen los componentes de arriba.
 */
export const IncidentMap = memo(function IncidentMap({
  mapRef,
  incidents,
  visibleFamilies,
  offMap,
  outages,
  waterCuts,
  showWater,
  seismic,
  showSeismic,
  theme,
  reach,
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

  // Se recalcula sólo cuando cambia el arreglo, no al encender o apagar una
  // capa: eso ahora es un filtro. El sondeo que trae lo mismo devuelve el mismo
  // arreglo (structural sharing de react-query) y acá no pasa nada.
  const data = useMemo(() => toFeatureCollection(incidents), [incidents])
  const outageData = useMemo(() => toOutageFeatureCollection(outages), [outages])
  const seismicData = useMemo(() => toSeismicFeatureCollection(seismic), [seismic])
  const waterData = useMemo(() => toWaterCutFeatureCollection(waterCuts), [waterCuts])

  const showIncidents = visibleFamilies.length > 0
  const showOutages = outages.length > 0
  const waterClickable = showWater && waterData.features.length > 0

  const families = useMemo<FilterSpecification>(
    () => ['in', ['get', 'layer'], ['literal', [...visibleFamilies]]],
    [visibleFamilies],
  )

  // Sólo las capas visibles reciben el toque. Si no se filtrara, un sismo
  // oculto seguiría capturando el clic sobre el incidente que hay debajo.
  const interactiveLayers = useMemo(() => {
    const ids: string[] = []
    if (showIncidents) ids.push(INCIDENT_HIT_LAYER_ID)
    if (showOutages) ids.push(OUTAGE_HIT_LAYER_ID, OUTAGE_CLUSTER_LAYER_ID)
    if (showSeismic) ids.push(SEISMIC_HIT_LAYER_ID)
    if (waterClickable) ids.push(WATER_HIT_LAYER_ID)
    return ids
  }, [showIncidents, showOutages, showSeismic, waterClickable])

  const handleClick = useCallback(
    (event: MapLayerMouseEvent) => {
      // Prioridad explícita, porque el orden de `interactiveLayerIds` no la
      // garantiza: una emergencia (incidente o corte de luz) gana sobre un
      // racimo de cortes, un racimo gana sobre un sismo, y todos ganan sobre un
      // corte de agua, que es información de servicio.
      const features = event.features ?? []
      const incident = features.find((f) => typeof f.properties?.['code'] === 'string')
      const cluster = features.find((f) => typeof f.properties?.['cluster_id'] === 'number')
      const quake = features.find((f) => typeof f.properties?.['usgs_id'] === 'string')
      const water = features.find((f) => typeof f.properties?.['water_id'] === 'string')
      const map = event.target

      if (incident) {
        selectIncident(String(incident.properties!['code']))
        map.easeTo({ center: event.lngLat, offset: focusOffset(), duration: 450 })
        return
      }

      if (cluster && cluster.geometry.type === 'Point') {
        // Un racimo no se selecciona: se abre. Se acerca la cámara hasta el
        // zoom en el que MapLibre lo separa.
        //
        // Al menos un nivel y medio por toque: el zoom de expansión de un
        // racimo apretado puede ser apenas unas décimas más, y abrir un
        // enjambre de sesenta cortes no debería pedir cinco toques. Nunca más
        // allá del primer zoom sin agrupar.
        const center = cluster.geometry.coordinates as [number, number]
        const unclustered = OUTAGE_CLUSTER_MAX_ZOOM + 1
        const target = (zoom: number) =>
          Math.min(Math.max(zoom, map.getZoom() + 1.5), unclustered)
        const source = map.getSource<GeoJSONSource>(OUTAGE_SOURCE_ID)
        void source
          ?.getClusterExpansionZoom(cluster.properties!['cluster_id'] as number)
          .then((zoom) => map.easeTo({ center, zoom: target(zoom) }))
          .catch(() => map.easeTo({ center, zoom: unclustered }))
        return
      }

      if (quake) {
        selectSeismic(String(quake.properties!['usgs_id']))
        return
      }

      if (water) {
        selectWaterCut(String(water.properties!['water_id']))
        map.easeTo({ center: event.lngLat, offset: focusOffset(), duration: 450 })
        return
      }

      clearSelection()
    },
    [],
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
  const incidentSpecs = useMemo(
    () => ({
      halo: withFamilies(alertHaloLayer, families),
      casing: withFamilies(casingLayer, families),
      core: withFamilies(coreLayer, families),
      icon: withFamilies(incidentIconLayer(theme), families),
      closed: withFamilies(closedRingLayer, families),
      unverified: withFamilies(unverifiedLayer, families),
      hit: withFamilies(hitLayer, families),
    }),
    [families, theme],
  )
  const seismicIcons = useMemo(() => seismicIconLayer(theme, SEISMIC_ICON_COLOR), [theme])
  const outageSpecs = useMemo(
    () => ({
      cluster: outageClusterLayer(theme),
      count: outageClusterCountLayer(),
      pin: outagePinLayer(theme),
      glyph: outageGlyphLayer(),
    }),
    [theme],
  )
  const waterSpecs = useMemo(
    () => ({
      pin: waterPinLayer(theme, showWater),
      glyph: waterGlyphLayer(showWater),
      hit: waterHitLayer(showWater),
    }),
    [theme, showWater],
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

      <WindConeSource incidents={incidents} />

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
          {iconsReady && <Layer {...seismicIcons} />}
          <SelectedQuakeLayer />
          <Layer {...seismicHitLayer} />
        </Source>
      )}

      {/*
        Cortes de agua (Esval): contexto, así que por debajo de los cortes de
        luz y de los incidentes. Siempre montada, como la de la luz: sin datos
        recibe una colección vacía, y apagar la fila es `visibility`, no un
        remontaje. Ver `waterCutLayers.ts`.
      */}
      <Source id={WATER_SOURCE_ID} type="geojson" data={waterData} promoteId="water_id">
        <Layer {...waterSpecs.pin} />
        {/* Mismo motivo que el rayo de abajo: montada tarde, necesita ancla. */}
        {iconsReady && <Layer {...waterSpecs.glyph} beforeId={WATER_SELECTED_LAYER_ID} />}
        <SelectedWaterLayer theme={theme} visible={showWater} />
        <Layer {...waterSpecs.hit} />
      </Source>

      {/*
        Cortes de luz: fuente propia, la única agrupada (ver `outageLayers.ts`).
        Encima de los sismos y debajo de los incidentes: un incendio o un choque
        tapan a un corte, nunca al revés. Siempre montada: sin cortes visibles
        recibe una colección vacía, que es un `setData` y no un remontaje.
      */}
      <Source
        id={OUTAGE_SOURCE_ID}
        type="geojson"
        data={outageData}
        promoteId="code"
        cluster
        clusterRadius={OUTAGE_CLUSTER_RADIUS}
        clusterMaxZoom={OUTAGE_CLUSTER_MAX_ZOOM}
        clusterProperties={OUTAGE_CLUSTER_PROPERTIES}
      >
        <Layer {...outageSpecs.cluster} />
        <Layer {...outageSpecs.count} />
        <Layer {...outageSpecs.pin} />
        {/*
          `beforeId`: montado cuando los iconos están listos, sin ancla se
          agregaría al final del estilo, encima de los incidentes. Así queda
          sobre su disco y bajo el anillo de selección.
        */}
        {iconsReady && <Layer {...outageSpecs.glyph} beforeId={OUTAGE_SELECTED_LAYER_ID} />}
        <SelectedOutageLayer theme={theme} />
        <Layer {...outageHitLayer} />
      </Source>

      {/*
        Siempre montada: las familias apagadas se esconden con `filter`
        (`visibleFamilies`), no desmontando la fuente.
      */}
      <Source
        id={INCIDENT_SOURCE_ID}
        type="geojson"
        data={data}
        promoteId="code"
      >
        <Layer {...incidentSpecs.halo} />
        <Layer {...incidentSpecs.casing} />
        {/*
          El disco sólo se dibuja mientras los iconos no estén listos. Es el
          estado de un puñado de milisegundos entre el primer cuadro del mapa y
          el registro de las imágenes: sin él, los incidentes parpadearían
          apareciendo de la nada en vez de afinarse.
        */}
        {!iconsReady && <Layer {...incidentSpecs.core} />}
        {iconsReady && <Layer {...incidentSpecs.icon} />}
        <Layer {...incidentSpecs.closed} />
        <Layer {...incidentSpecs.unverified} />
        <SelectedIncidentLayer families={families} />
        <Layer {...incidentSpecs.hit} />
      </Source>

      <GhostSelection offMap={offMap} />
    </Map>
  )
})
