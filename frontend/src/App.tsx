import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { MapRef } from 'react-map-gl/maplibre'
import type { SeismicEvent } from '@/api/seismicTypes'
import type { ActiveIncidentsQuery, Incident } from '@/api/types'
import type { WaterCut } from '@/api/waterCutTypes'
import { IncidentMap } from '@/components/map/IncidentMap'
import {
  DEFAULT_LAYER_VISIBILITY,
  DEFAULT_PROVIDER_VISIBILITY,
} from '@/components/ui/SidePanel'
import type {
  LayerVisibility,
  ProviderVisibility,
  WaterPanel,
} from '@/components/ui/SidePanel'
import { providerOf } from '@/domain/powerSymbology'
import { layerOf } from '@/domain/families'
import type { IncidentLayerKey } from '@/domain/families'
import {
  DEFAULT_SEISMIC_FILTER,
  filterSeismic,
  type SeismicFilterKey,
} from '@/domain/seismicFilter'
import { FOCUS_ZOOM, SEISMIC_FOCUS_ZOOM } from '@/config/map'
import { toReachCollection } from '@/lib/overlayGeojson'
import {
  clearSelection,
  selectIncident,
  selectSeismic,
  selectWaterCut,
} from '@/lib/selectionStore'
import { useIsCompact } from '@/hooks/useMediaQuery'
import { useTheme } from '@/hooks/useTheme'
import { useRainLayer } from '@/hooks/useRainLayer'
import { useRoadClosures } from '@/hooks/useRoadClosures'
import { useSeismicHazard } from '@/hooks/useSeismicHazard'
import { ThemeToggle } from '@/components/ui/ThemeToggle'
import { NotificationBell } from '@/components/ui/NotificationBell'
import { CitizenReportControl } from '@/components/report/CitizenReportControl'
import { AppHeader } from '@/components/ui/AppHeader'
import { BottomSheet } from '@/components/shell/BottomSheet'
import { DesktopColumn } from '@/components/shell/DesktopColumn'
import type { ExplorePanelProps } from '@/components/shell/ExplorePanel'
import { focusOffset } from '@/lib/cameraOffset'
import { MapOverlayState } from '@/components/ui/MapOverlayState'
import { StalenessBanner } from '@/components/ui/StalenessBanner'
import { levelOf } from '@/domain/symbology'
import { useActiveIncidents } from '@/hooks/useActiveIncidents'
import { useCollectorHealth } from '@/hooks/useCollectorHealth'
import { useSeismicEvents } from '@/hooks/useSeismicEvents'
import { useNotificationDeepLink } from '@/hooks/useNotificationDeepLink'
import { useVehicleFeed } from '@/hooks/useVehicleFeed'
import { useWaterCuts } from '@/hooks/useWaterCuts'
import { useDisplaySplit } from '@/hooks/useDisplaySplit'
import { INCIDENT_QUERY_HOURS, INCIDENT_QUERY_STATUSES } from '@/domain/displayWindow'
import type { HistoryFeedProps } from '@/components/feed/HistoryFeed'
import { env } from '@/config/env'
import {
  loadCitizenReportModal,
  loadIncidentSheet,
  loadRadarControls,
  loadSeismicCard,
  loadWaterCutCard,
  prefetchWhenIdle,
} from '@/lib/lazyChunks'
import { usgsIdOf } from '@/lib/push'

/*
 * El radar entero va en un chunk aparte: con `VITE_VEHICLE_RADAR` apagado —así
 * está en producción hasta avisarle a GBV— no se descarga nunca.
 */
const RadarToggle = lazy(() => loadRadarControls().then((m) => ({ default: m.RadarToggle })))
const RadarPanelHost = lazy(() =>
  loadRadarControls().then((m) => ({ default: m.RadarPanelHost })),
)

/**
 * Arreglo vacío compartido. `incidents ?? []` creaba uno nuevo en cada render
 * mientras no había datos, y cada `useMemo` que dependía de él se recalculaba
 * de balde.
 */
const NO_INCIDENTS: Incident[] = []
const NO_SEISMIC: SeismicEvent[] = []
const NO_WATER: WaterCut[] = []

/** Las familias que viven en la fuente de incidentes. Los cortes tienen la suya. */
const MAP_FAMILIES = ['fire', 'traffic', 'otros'] as const satisfies readonly IncidentLayerKey[]

export default function App() {
  // La referencia del mapa vive acá y no dentro de `IncidentMap`: el panel de
  // capas es hermano del mapa, no descendiente, y necesita ordenarle un vuelo.
  const mapRef = useRef<MapRef>(null)

  const { theme, toggle: toggleTheme } = useTheme()

  /*
   * Punto de quiebre del cromo del mapa.
   *
   * Desde `md`, una columna a la izquierda con el mapa libre al lado; por
   * debajo, la misma columna como hoja inferior de tres alturas. Es el mismo
   * contenido (`components/shell/ExplorePanel`) en dos contenedores.
   */
  const isCompact = useIsCompact()

  const hazard = useSeismicHazard()
  /*
   * Lluvia pronosticada. El hook no dispara ninguna llamada hasta que alguien
   * enciende el interruptor: `enabled` de react-query arranca en `false`, así
   * que la capa no cuesta nada mientras nadie la mire. No confundir con
   * `useCurrentWind`, que consulta Open-Meteo directo para el cono de un
   * incendio seleccionado — otro dato, otro origen y otra cadencia.
   *
   * El interruptor ya no está en el riel de referencia sino dentro del widget
   * meteorológico de `AppHeader`, y la intención viaja por el store externo
   * (`lib/tacticalWeatherStore`). Por eso este hook sigue sin recibir
   * parámetros y `App` sigue sin tener estado de lluvia: la capa se enciende
   * desde otra rama del árbol sin pasar por acá, que es exactamente lo que
   * evita repintar los 500 incidentes al tocar un interruptor de contexto.
   */
  const rain = useRainLayer()
  const closures = useRoadClosures()
  // Sin `enabled`: su valor aparece justamente cuando el mapa está vacío, que
  // es cuando nadie está tocando nada y nadie iría a buscarlo.
  const health = useCollectorHealth()

  /*
   * La selección (incidente, sismo o radar) ya no vive acá: está en
   * `lib/selectionStore`, y la leen sólo los componentes que la dibujan. Tocar
   * un pin no repinta `App`.
   */

  // --- Radar de vehículos ---------------------------------------------------
  /*
   * Vehículos robados, recuperados y abandonados (GBV). No es una capa del mapa
   * —no tienen coordenadas— así que no pasa por `visibility` ni por el panel de
   * capas: tiene su botón en la barra y su propio panel.
   *
   * La consulta corre desde el arranque (el contador del botón la necesita) y
   * sólo si el interruptor está encendido: apagado, no hay botón, no hay panel
   * y no sale ninguna petición.
   *
   * El panel y la ficha del incidente se excluyen. En teléfono ocupan el mismo
   * borde inferior; en escritorio, el mismo borde derecho. Abrir el radar es
   * una selección más del store, así que la exclusión viene sola: abrirlo
   * cierra la ficha, y seleccionar algo en el mapa lo cierra a él.
   */
  const radarEnabled = env.vehicleRadarEnabled
  const vehicles = useVehicleFeed(radarEnabled)
  const radarButtonRef = useRef<HTMLButtonElement>(null)

  // --- Cortes de agua (Esval) -------------------------------------------------
  /*
   * Una fila más del panel de emergencias, junto a la luz, pero no es un
   * incidente: tiene su propia consulta, su propia fuente en el mapa y su
   * propia salud. La fila no aparece hasta que el backend leyó a Esval al menos
   * una vez (`water.available`); ver `useWaterCuts`.
   */
  const water = useWaterCuts()

  // Lo que aparece al primer toque —la ficha, las tarjetas del sismo y del
  // corte de agua, el modal de reporte y el radar— se adelanta cuando el
  // navegador queda libre.
  useEffect(() => {
    prefetchWhenIdle([
      loadIncidentSheet,
      loadSeismicCard,
      loadCitizenReportModal,
      ...(env.waterCutsEnabled ? [loadWaterCutCard] : []),
      ...(radarEnabled ? [loadRadarControls] : []),
    ])
  }, [radarEnabled])

  const [confirmedOnly, setConfirmedOnly] = useState(false)
  const [visibility, setVisibility] = useState<LayerVisibility>(DEFAULT_LAYER_VISIBILITY)
  const [seismicFilter, setSeismicFilter] =
    useState<SeismicFilterKey>(DEFAULT_SEISMIC_FILTER)
  const [providers, setProviders] = useState<ProviderVisibility>(
    DEFAULT_PROVIDER_VISIBILITY,
  )

  /*
   * Una sola consulta para el mapa y el historial: 48 h y también lo que ya
   * no está abierto (`stale`, `extinguished`). Qué va al mapa lo decide
   * `useDisplaySplit` con la ventana de cada familia (`domain/displayWindow`).
   */
  const params = useMemo<ActiveIncidentsQuery>(
    () => ({
      confirmed_only: confirmedOnly,
      limit: 500,
      hours: INCIDENT_QUERY_HOURS,
      status: INCIDENT_QUERY_STATUSES,
    }),
    [confirmedOnly],
  )

  const {
    data: incidents,
    dataUpdatedAt,
    isFetching,
    isError,
    isPending,
    refetch,
  } = useActiveIncidents(params)

  // La consulta se apaga con la capa: no tiene sentido traer sismos que nadie
  // está mirando.
  const { data: seismic } = useSeismicEvents({ hours: 72, limit: 500 }, visibility.seismic)

  // El filtro de relevancia se aplica en el cliente y no como `min_magnitude` en
  // la consulta: alternar entre microsismos y relevantes es instantáneo y no
  // vuelve a golpear la API, que además ya trajo ambos conjuntos.
  const seismicList = useMemo(
    () => filterSeismic(seismic ?? NO_SEISMIC, seismicFilter),
    [seismic, seismicFilter],
  )

  const anyIncidentLayer =
    visibility.fire || visibility.traffic || visibility.power || visibility.otros

  const all = incidents ?? NO_INCIDENTS

  /*
   * Mapa e historial. `current` es lo que está en su ventana de exhibición: lo
   * único que se dibuja y se cuenta como «activo». `history` son las últimas
   * 24 h, estén o no en el mapa. Se recalcula sólo cuando algo vence, sin un
   * reloj que repinte `App`.
   */
  const { onMap: current, history } = useDisplaySplit(all)

  const isVisible = useCallback(
    (incident: Incident) => {
      const layer = layerOf(incident.type)
      if (!visibility[layer]) return false
      // Dentro de la categoría de cortes manda además el subfiltro por
      // empresa. Un corte sin distribuidora identificable se muestra
      // siempre que la categoría esté encendida: esconderlo por no saber
      // de quién es sería perder el dato por una duda administrativa.
      if (layer === 'power') {
        const provider = providerOf(incident)
        return provider === null || providers[provider]
      }
      return true
    },
    [visibility, providers],
  )

  // Una sola consulta a `/incidents/active` alimenta las tres capas; el filtro
  // es por familia y ocurre acá. Separarlo en tres consultas multiplicaría el
  // tráfico sin ganar nada: el backend ya devuelve todo junto.
  const list = useMemo(() => current.filter(isVisible), [current, isVisible])

  // El historial obedece a las mismas capas que el mapa.
  const visibleHistory = useMemo(() => history.filter(isVisible), [history, isVisible])
  const onMapCodes = useMemo(() => new Set(current.map((incident) => incident.code)), [current])
  const offMap = useMemo(
    () => visibleHistory.filter((incident) => !onMapCodes.has(incident.code)),
    [visibleHistory, onMapCodes],
  )

  /**
   * Lo que recibe el mapa, en dos fuentes.
   *
   * - `mapIncidents`: TODOS los que no son cortes, sin filtrar por capa. El
   *   mapa esconde las familias apagadas con un `filter` de MapLibre, así que
   *   encender o apagar una capa no le cambia este arreglo.
   * - `outages`: los cortes, ya filtrados por capa y por empresa. Su fuente
   *   está agrupada, y un `filter` de capa no le cambia la cuenta a un racimo:
   *   ahí el filtro tiene que ir en los datos.
   */
  const mapIncidents = useMemo(
    () => current.filter((incident) => layerOf(incident.type) !== 'power'),
    [current],
  )
  const outages = useMemo(
    () => list.filter((incident) => layerOf(incident.type) === 'power'),
    [list],
  )
  const visibleFamilies = useMemo(
    () => MAP_FAMILIES.filter((family) => visibility[family]),
    [visibility],
  )

  /*
   * Los cortes de agua van al mapa enteros —apagar la fila es `visibility` en
   * MapLibre— pero a la tarjeta sólo si la fila está encendida: no se abre una
   * tarjeta sobre un pin que no se ve.
   */
  const mapWaterCuts = water.available ? water.cuts : NO_WATER
  const visibleWaterCuts = visibility.water ? mapWaterCuts : NO_WATER

  const countsByLayer = useMemo(() => {
    const counts = { fire: 0, traffic: 0, power: 0, otros: 0 }
    for (const incident of current) counts[layerOf(incident.type)] += 1
    return counts
  }, [current])

  /** Índice del acordeón: los mismos incidentes, agrupados por capa. */
  const incidentsByLayer = useMemo(() => {
    const groups: Record<IncidentLayerKey, Incident[]> = {
      fire: [],
      traffic: [],
      power: [],
      otros: [],
    }
    for (const incident of current) groups[layerOf(incident.type)].push(incident)
    // Los más recientes arriba: es el orden en que alguien quiere revisarlos.
    for (const key of Object.keys(groups) as IncidentLayerKey[]) {
      groups[key].sort((a, b) => b.last_seen_at.localeCompare(a.last_seen_at))
    }
    return groups
  }, [current])
  // --- Radio de percepción sísmica -----------------------------------------
  const reachCollection = useMemo(
    () => toReachCollection(seismicList),
    [seismicList],
  )

  // --- Navegación de cámara -------------------------------------------------
  /**
   * Vuela hasta un punto y lo selecciona.
   *
   * El desplazamiento compensa lo que tapa el mapa: la columna en escritorio,
   * la hoja a media pantalla en el teléfono. Sin él la cámara centraría el
   * incidente justo detrás de la ficha que se acaba de abrir.
   */
  const flyTo = useCallback((lon: number, lat: number, zoom: number) => {
    mapRef.current?.flyTo({
      center: [lon, lat],
      zoom,
      duration: 900,
      essential: true,
      offset: focusOffset(),
    })
  }, [])

  const focusIncident = useCallback(
    (incident: Incident) => {
      selectIncident(incident.code)
      flyTo(incident.lon, incident.lat, FOCUS_ZOOM)
    },
    [flyTo],
  )

  const focusSeismic = useCallback(
    (event: SeismicEvent) => {
      selectSeismic(event.usgs_id)
      flyTo(event.lon, event.lat, SEISMIC_FOCUS_ZOOM)
    },
    [flyTo],
  )

  // Un corte sin punto (el visor de Esval no respondió) abre la tarjeta igual,
  // sin mover la cámara: no hay adónde volar.
  const focusWaterCut = useCallback(
    (cut: WaterCut) => {
      selectWaterCut(cut.id)
      if (cut.coordinates) flyTo(cut.coordinates[0], cut.coordinates[1], FOCUS_ZOOM)
    },
    [flyTo],
  )

  const waterPanel = useMemo<WaterPanel | null>(
    () =>
      water.available
        ? {
            cuts: water.cuts,
            status: water.sourceStatus,
            detail: water.source?.detalle ?? null,
            onFocus: focusWaterCut,
          }
        : null,
    [water, focusWaterCut],
  )

  // --- Notificación tocada --------------------------------------------------
  /*
   * Un aviso push abre la app en `/?incidente=INC-…` (o le manda el destino si
   * ya estaba abierta). El incidente puede estar en una capa apagada o fuera del
   * filtro «Verificados», así que antes de volar hay que asegurarse de que el
   * mapa lo vaya a mostrar: si no, la ficha se abriría sobre un pin invisible.
   *
   * Se espera a que lleguen los incidentes. Si llegaron y no está —ya se
   * controló, o se fusionó con otro—, el destino se descarta en silencio y el
   * mapa queda donde estaba: no hay nada mejor que mostrar.
   */
  const { link: pendingLink, clear: clearLink } = useNotificationDeepLink()

  useEffect(() => {
    if (!pendingLink) return

    if (pendingLink.kind === 'seismic') {
      setVisibility((current) => (current.seismic ? current : { ...current, seismic: true }))
      const usgsId = usgsIdOf(pendingLink.key)
      if (usgsId) selectSeismic(usgsId)
      else clearSelection()
      flyTo(pendingLink.lon, pendingLink.lat, SEISMIC_FOCUS_ZOOM)
      clearLink()
      return
    }

    if (confirmedOnly) {
      setConfirmedOnly(false)
      return
    }
    const target = all.find((incident) => incident.code === pendingLink.code)
    if (target) {
      const layer = layerOf(target.type)
      setVisibility((current) => (current[layer] ? current : { ...current, [layer]: true }))
      focusIncident(target)
      clearLink()
    } else if (!isPending && !isFetching) {
      clearLink()
    }
  }, [pendingLink, clearLink, all, isPending, isFetching, confirmedOnly, flyTo, focusIncident])

  const byLevel = useMemo(() => {
    const counts = { unsafe: 0, possible: 0, confirmed: 0 }
    for (const incident of list) counts[levelOf(incident)] += 1
    return counts
  }, [list])
  const withAlert = list.filter((incident) => incident.alert_level !== null).length

  /*
   * Las dos bolsas de propiedades, armadas una sola vez.
   *
   * El mismo contenido se monta en dos cromos distintos —el riel y la hoja de
   * escritorio, o la barra de fichas en teléfono— y escribir la lista de
   * propiedades dos veces es garantía de que una de las dos se quede atrás
   * cuando se añada un filtro. Acá se declaran una vez y cada rama las derrama.
   */
  const incidentControls = useMemo(
    () => ({
      visibility,
      onChange: setVisibility,
      counts: { ...countsByLayer, seismic: seismicList.length, water: mapWaterCuts.length },
      incidentsByLayer,
      seismicEvents: seismicList,
      onFocusIncident: focusIncident,
      onFocusSeismic: focusSeismic,
      seismicFilter,
      onSeismicFilterChange: setSeismicFilter,
      providers,
      onProvidersChange: setProviders,
      // Va acá y no en cada rama por el mismo motivo que el resto: declarar las
      // propiedades dos veces garantiza que una se quede atrás.
      health: health.data,
      water: waterPanel,
    }),
    [
      visibility,
      countsByLayer,
      seismicList,
      mapWaterCuts,
      waterPanel,
      incidentsByLayer,
      focusIncident,
      focusSeismic,
      seismicFilter,
      providers,
      health.data,
    ],
  )

  const historyControls = useMemo<HistoryFeedProps>(
    () => ({
      history: visibleHistory,
      onMapCodes,
      onFocus: focusIncident,
      health: health.data,
      ready: !isPending,
    }),
    [visibleHistory, onMapCodes, focusIncident, health.data, isPending],
  )

  const referenceControls = useMemo(
    () => ({
      hazardEnabled: hazard.enabled,
      hazardStatus: hazard.status,
      hazardError: hazard.errorMessage,
      onHazardToggle: hazard.toggle,
      onHazardRetry: hazard.retry,
      closureEnabled: closures.enabled,
      closureStatus: closures.status,
      closureCount: closures.count,
      closureCutCount: closures.cutCount,
      onClosureToggle: closures.toggle,
      onClosureRetry: closures.retry,
      theme,
    }),
    [hazard, closures, theme],
  )

  /*
   * Todo lo que baja a un componente memorizado tiene que conservar su
   * identidad entre renders: un objeto, una función o un elemento JSX nuevo en
   * cada render anula el `memo` del hijo sin que nada avise.
   */
  /*
   * La ficha busca también en el historial: se puede abrir desde ahí algo que
   * ya salió del mapa (con su pin fantasma).
   */
  const exploreProps = useMemo<ExplorePanelProps>(
    () => ({
      incidents: incidentControls,
      reference: referenceControls,
      history: historyControls,
      selection: {
        incidents: visibleHistory,
        seismic: seismicList,
        waterCuts: visibleWaterCuts,
      },
      incidentCount: list.length,
    }),
    [
      incidentControls,
      referenceControls,
      historyControls,
      visibleHistory,
      seismicList,
      visibleWaterCuts,
      list.length,
    ],
  )

  const reportInline = useMemo(() => <CitizenReportControl placement="inline" />, [])

  const retryIncidents = useCallback(() => void refetch(), [refetch])
  const themeToggle = useMemo(
    () => <ThemeToggle theme={theme} onToggle={toggleTheme} />,
    [theme, toggleTheme],
  )
  const notifications = useMemo(() => <NotificationBell />, [])
  const radarButton = useMemo(
    () =>
      // Con 404 el servidor todavía no tiene el feed: un botón que abre
      // «no disponible» es cromo muerto, así que no se muestra.
      radarEnabled && vehicles.status !== 'unavailable' ? (
        <Suspense fallback={null}>
          <RadarToggle feed={vehicles} buttonRef={radarButtonRef} />
        </Suspense>
      ) : undefined,
    [radarEnabled, vehicles],
  )

  return (
    <div className="flex h-[100dvh] flex-col overflow-hidden bg-app">
      <AppHeader
        total={list.length}
        byLevel={byLevel}
        withAlert={withAlert}
        confirmedOnly={confirmedOnly}
        onToggleConfirmedOnly={setConfirmedOnly}
        themeToggle={themeToggle}
        notifications={notifications}
        radar={radarButton}
      />

      <StalenessBanner
        dataUpdatedAt={dataUpdatedAt || undefined}
        hasError={isError}
        onRetry={retryIncidents}
      />

      <main className="relative flex-1" data-shell={isCompact ? 'sheet' : 'column'}>
        <IncidentMap
          mapRef={mapRef}
          theme={theme}
          reach={reachCollection}
          hazard={hazard}
          rain={rain}
          closures={closures}
          incidents={mapIncidents}
          visibleFamilies={visibleFamilies}
          offMap={offMap}
          outages={outages}
          waterCuts={mapWaterCuts}
          showWater={visibility.water}
          seismic={seismicList}
          showSeismic={visibility.seismic}
        />

        {/*
          La columna (escritorio) o la hoja inferior (teléfono): el mismo
          contenido —historial, capas, leyenda y la ficha de lo seleccionado—
          en dos contenedores. Ver `components/shell/ExplorePanel.tsx`.
        */}
        {isCompact ? (
          <BottomSheet {...exploreProps} headerAction={reportInline} />
        ) : (
          <DesktopColumn {...exploreProps} />
        )}

        {/*
          El botón vive dentro del `main` relativo, no en el árbol del mapa: así
          no compite con los controles de MapLibre ni se pierde en un repintado
          del canvas. En teléfono no flota: va en el encabezado de la hoja
          (`reportInline`).
        */}
        {!isCompact && <CitizenReportControl />}

        {isPending && (
          <MapOverlayState
            busy
            title="Cargando incidentes"
            detail="Consultando el motor de correlación…"
          />
        )}

        {!isPending && anyIncidentLayer && list.length === 0 && !isError && (
          <MapOverlayState
            title="Sin incidentes activos"
            detail={
              confirmedOnly
                ? 'Ninguna fuente verificó un incidente en terreno dentro de la ventana activa. Desmarca el filtro para ver los que tienen evidencia sin verificar.'
                : current.length > 0
                  ? 'Hay incidentes vigentes, pero ninguno de las capas encendidas. Revisa el control de capas.'
                  : visibleHistory.length > 0
                    ? 'Nada en curso ahora. Lo de las últimas 24 h está en el historial.'
                    : 'El motor de correlación no tiene incidentes vigentes en la Región de Valparaíso.'
            }
          />
        )}

        {!isPending && anyIncidentLayer && list.length === 0 && isError && (
          <MapOverlayState
            title="Sin datos"
            detail="No se pudo contactar al servidor y no hay nada en cache. Revisa tu conexión o que el backend esté corriendo."
          />
        )}

        {radarEnabled && (
          <Suspense fallback={null}>
            <RadarPanelHost feed={vehicles} buttonRef={radarButtonRef} />
          </Suspense>
        )}
      </main>
    </div>
  )
}
