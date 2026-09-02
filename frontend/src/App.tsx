import { useCallback, useMemo, useRef, useState } from 'react'
import type { MapRef } from 'react-map-gl/maplibre'
import type { SeismicEvent } from '@/api/seismicTypes'
import type { ActiveIncidentsQuery, Incident } from '@/api/types'
import { IncidentMap } from '@/components/map/IncidentMap'
import { MapLegend } from '@/components/map/MapLegend'
import { IncidentSheet } from '@/components/incident/IncidentSheet'
import { SeismicCard } from '@/components/incident/SeismicCard'
import {
  DEFAULT_LAYER_VISIBILITY,
  DEFAULT_PROVIDER_VISIBILITY,
  SidePanel,
} from '@/components/ui/SidePanel'
import type {
  LayerVisibility,
  ProviderVisibility,
} from '@/components/ui/SidePanel'
import { providerOf } from '@/domain/powerSymbology'
import { layerOf } from '@/domain/families'
import type { IncidentLayerKey } from '@/domain/families'
import {
  DEFAULT_SEISMIC_FILTER,
  filterSeismic,
  type SeismicFilterKey,
} from '@/domain/seismicFilter'
import { windConeFor } from '@/domain/windCone'
import { FOCUS_ZOOM, SEISMIC_FOCUS_ZOOM } from '@/config/map'
import { toConeCollection, toReachCollection } from '@/lib/overlayGeojson'
import { useCurrentWind } from '@/hooks/useCurrentWind'
import { useIsCompact } from '@/hooks/useMediaQuery'
import { useTheme } from '@/hooks/useTheme'
import { useRainLayer } from '@/hooks/useRainLayer'
import { useRoadClosures } from '@/hooks/useRoadClosures'
import { useSeismicHazard } from '@/hooks/useSeismicHazard'
import { ThemeToggle } from '@/components/ui/ThemeToggle'
import { CitizenReportControl } from '@/components/report/CitizenReportControl'
import { AppHeader } from '@/components/ui/AppHeader'
import { MobileMapControls } from '@/components/ui/MobileMapControls'
import { ReferenceDock } from '@/components/ui/ReferenceDock'
import { MapOverlayState } from '@/components/ui/MapOverlayState'
import { StalenessBanner } from '@/components/ui/StalenessBanner'
import { levelOf } from '@/domain/symbology'
import { useActiveIncidents } from '@/hooks/useActiveIncidents'
import { useCollectorHealth } from '@/hooks/useCollectorHealth'
import { useSeismicEvents } from '@/hooks/useSeismicEvents'
import { useFreshness } from '@/hooks/useFreshness'
import { useOnlineStatus } from '@/hooks/useOnlineStatus'

export default function App() {
  // La referencia del mapa vive acá y no dentro de `IncidentMap`: el panel de
  // capas es hermano del mapa, no descendiente, y necesita ordenarle un vuelo.
  const mapRef = useRef<MapRef>(null)

  const { theme, toggle: toggleTheme } = useTheme()

  /*
   * Punto de quiebre del cromo del mapa.
   *
   * Por debajo de `md`, los dos paneles flotantes no caben a la vez —488 px de
   * cromo sobre una pantalla de 430— y se relevan por una barra de fichas que
   * abre uno por vez. No es un cambio de estilo sino de árbol; el porqué está en
   * `hooks/useMediaQuery.ts` y en `components/ui/MobileMapControls.tsx`.
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

  const [selectedCode, setSelectedCode] = useState<string | null>(null)
  const [selectedUsgsId, setSelectedUsgsId] = useState<string | null>(null)
  const [confirmedOnly, setConfirmedOnly] = useState(false)
  const [visibility, setVisibility] = useState<LayerVisibility>(DEFAULT_LAYER_VISIBILITY)
  const [seismicFilter, setSeismicFilter] =
    useState<SeismicFilterKey>(DEFAULT_SEISMIC_FILTER)
  const [providers, setProviders] = useState<ProviderVisibility>(
    DEFAULT_PROVIDER_VISIBILITY,
  )

  const params = useMemo<ActiveIncidentsQuery>(
    () => ({ confirmed_only: confirmedOnly, limit: 500 }),
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
    () => filterSeismic(seismic ?? [], seismicFilter),
    [seismic, seismicFilter],
  )

  const anyIncidentLayer =
    visibility.fire || visibility.traffic || visibility.power || visibility.otros

  const isOnline = useOnlineStatus()
  const freshness = useFreshness(dataUpdatedAt || undefined)

  const all = incidents ?? []

  // Una sola consulta a `/incidents/active` alimenta las tres capas; el filtro
  // es por familia y ocurre acá. Separarlo en tres consultas multiplicaría el
  // tráfico sin ganar nada: el backend ya devuelve todo junto.
  const list = useMemo(
    () =>
      all.filter((incident) => {
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
      }),
    [all, visibility, providers],
  )

  /**
   * Los cortes se separan del resto: van a `<Marker>` y no a la fuente GeoJSON.
   * La partición ocurre acá, una sola vez, y no dentro del mapa, para que
   * `IncidentMap` reciba dos arreglos ya listos y no tenga que filtrar en cada
   * repintado.
   */
  const { regular, outages } = useMemo(() => {
    const regular: Incident[] = []
    const outages: Incident[] = []
    for (const incident of list) {
      ;(layerOf(incident.type) === 'power' ? outages : regular).push(incident)
    }
    return { regular, outages }
  }, [list])

  const countsByLayer = useMemo(() => {
    const counts = { fire: 0, traffic: 0, power: 0, otros: 0 }
    for (const incident of all) counts[layerOf(incident.type)] += 1
    return counts
  }, [all])

  /** Índice del acordeón: los mismos incidentes, agrupados por capa. */
  const incidentsByLayer = useMemo(() => {
    const groups: Record<IncidentLayerKey, Incident[]> = {
      fire: [],
      traffic: [],
      power: [],
      otros: [],
    }
    for (const incident of all) groups[layerOf(incident.type)].push(incident)
    // Los más recientes arriba: es el orden en que alguien quiere revisarlos.
    for (const key of Object.keys(groups) as IncidentLayerKey[]) {
      groups[key].sort((a, b) => b.last_seen_at.localeCompare(a.last_seen_at))
    }
    return groups
  }, [all])
  const selected = useMemo(
    () => list.find((incident) => incident.code === selectedCode) ?? null,
    [list, selectedCode],
  )

  const selectedSeismic = useMemo(
    () => seismicList.find((event) => event.usgs_id === selectedUsgsId) ?? null,
    [seismicList, selectedUsgsId],
  )

  // --- Cono de viento -------------------------------------------------------
  // Sólo se consulta el viento para un INCENDIO seleccionado: una cuña de
  // propagación sobre un choque no significa nada, y pedirlo para cada
  // incidente del mapa sería una llamada por marcador a un servicio externo.
  const selectedFire =
    selected && layerOf(selected.type) === 'fire' ? selected : null

  const { data: wind, isLoading: windLoading, isError: windError } = useCurrentWind(
    selectedFire?.lat ?? null,
    selectedFire?.lon ?? null,
    selectedFire !== null,
  )

  const cone = useMemo(
    () => windConeFor(wind?.windSpeedKmh, wind?.windDirectionDeg),
    [wind],
  )

  const coneCollection = useMemo(
    () => toConeCollection(selectedFire, cone),
    [selectedFire, cone],
  )

  // --- Radio de percepción sísmica -----------------------------------------
  const reachCollection = useMemo(
    () => toReachCollection(seismicList),
    [seismicList],
  )

  // --- Navegación de cámara -------------------------------------------------
  /**
   * Vuela hasta un punto y lo selecciona.
   *
   * El desplazamiento vertical compensa la ficha, que en teléfono ocupa el
   * tercio inferior: sin él la cámara centraría el incidente justo detrás de la
   * tarjeta que se acaba de abrir.
   */
  const flyTo = useCallback((lon: number, lat: number, zoom: number) => {
    mapRef.current?.flyTo({
      center: [lon, lat],
      zoom,
      duration: 900,
      essential: true,
      offset: [0, -Math.min(window.innerHeight * 0.18, 160)],
    })
  }, [])

  const focusIncident = useCallback(
    (incident: Incident) => {
      setSelectedUsgsId(null)
      setSelectedCode(incident.code)
      flyTo(incident.lon, incident.lat, FOCUS_ZOOM)
    },
    [flyTo],
  )

  const focusSeismic = useCallback(
    (event: SeismicEvent) => {
      setSelectedCode(null)
      setSelectedUsgsId(event.usgs_id)
      flyTo(event.lon, event.lat, SEISMIC_FOCUS_ZOOM)
    },
    [flyTo],
  )

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
  const incidentControls = {
    visibility,
    onChange: setVisibility,
    counts: { ...countsByLayer, seismic: seismicList.length },
    incidentsByLayer,
    seismicEvents: seismicList,
    selectedCode,
    selectedUsgsId,
    onFocusIncident: focusIncident,
    onFocusSeismic: focusSeismic,
    seismicFilter,
    onSeismicFilterChange: setSeismicFilter,
    providers,
    onProvidersChange: setProviders,
    // Va acá y no en cada rama por el mismo motivo que el resto: declarar las
    // propiedades dos veces garantiza que una se quede atrás.
    health: health.data,
  }

  const referenceControls = {
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
  }

  return (
    <div className="flex h-[100dvh] flex-col overflow-hidden bg-app">
      <AppHeader
        total={list.length}
        byLevel={byLevel}
        withAlert={withAlert}
        confirmedOnly={confirmedOnly}
        onToggleConfirmedOnly={setConfirmedOnly}
        themeToggle={<ThemeToggle theme={theme} onToggle={toggleTheme} />}
      />

      <StalenessBanner
        freshness={freshness}
        isOnline={isOnline}
        isFetching={isFetching}
        dataUpdatedAt={dataUpdatedAt || undefined}
        hasError={isError}
        onRetry={() => void refetch()}
      />

      <main className="relative flex-1">
        <IncidentMap
          mapRef={mapRef}
          theme={theme}
          reach={reachCollection}
          cone={coneCollection}
          hazard={hazard}
          rain={rain}
          closures={closures}
          incidents={regular}
          outages={outages}
          seismic={seismicList}
          showIncidents={visibility.fire || visibility.traffic || visibility.otros}
          showSeismic={visibility.seismic}
          selectedCode={selectedCode}
          selectedUsgsId={selectedUsgsId}
          onSelect={setSelectedCode}
          onSelectSeismic={setSelectedUsgsId}
        />

        {/*
          Riel izquierdo. Dos superficies apiladas en una sola columna, y el
          orden dice para qué sirve cada una:

          **Sólo desde `md`.** Debajo de esa medida este riel y la hoja derecha
          suman más ancho que la pantalla, y los reemplaza `MobileMapControls`.

            1. **Capas de referencia** — qué se está mostrando. Es un control.
            2. **Leyenda** — qué significa lo que se muestra. Es documentación.

          Antes la leyenda se anclaba sola a esta esquina y las capas de
          referencia vivían al final del panel derecho. Juntarlas acá deja el
          panel derecho dedicado a una sola cosa —las emergencias— y agrupa a
          este lado todo lo que responde «qué estoy viendo».

          `pointer-events-none` en el contenedor y `auto` en cada hijo: el
          hueco entre ambas superficies tiene que dejar pasar el arrastre del
          mapa, o el riel se convierte en una franja muerta de 250 px.
        */}
        {isCompact ? (
          <MobileMapControls
            incidents={incidentControls}
            reference={referenceControls}
            incidentCount={list.length}
          />
        ) : (
          <>
            <div className="pointer-events-none absolute left-3 top-3 z-10 flex w-[15.5rem] flex-col gap-2">
              <div className="animate-slide-in">
                <ReferenceDock {...referenceControls} />
              </div>

              <div className="animate-slide-in stagger-1">
                <MapLegend />
              </div>
            </div>

            <SidePanel {...incidentControls} />
          </>
        )}

        {/*
          El botón vive dentro del `main` relativo, no en el árbol del mapa: así
          no compite con los controles de MapLibre ni se pierde en un repintado
          del canvas. En teléfono se oculta mientras la ficha del incidente está
          abierta, porque esa ficha ocupa el mismo borde inferior.
        */}
        <CitizenReportControl hiddenOnMobile={selected !== null} />

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
                : all.length > 0
                  ? 'Hay incidentes vigentes, pero ninguno de las capas encendidas. Revisa el control de capas.'
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

        {selected && (
          <IncidentSheet
            incident={selected}
            onClose={() => setSelectedCode(null)}
            wind={selectedFire ? (wind ?? null) : null}
            windCone={cone}
            windLoading={selectedFire !== null && windLoading}
            windError={selectedFire !== null && windError}
          />
        )}

        {!selected && selectedSeismic && (
          <SeismicCard
            event={selectedSeismic}
            onClose={() => setSelectedUsgsId(null)}
          />
        )}
      </main>
    </div>
  )
}
