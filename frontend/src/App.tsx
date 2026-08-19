import { useMemo, useState } from 'react'
import type { ActiveIncidentsQuery } from '@/api/types'
import { IncidentMap } from '@/components/map/IncidentMap'
import { MapLegend } from '@/components/map/MapLegend'
import { IncidentSheet } from '@/components/incident/IncidentSheet'
import { SeismicCard } from '@/components/incident/SeismicCard'
import { DEFAULT_LAYER_VISIBILITY, LayerToggles } from '@/components/ui/LayerToggles'
import type { LayerVisibility } from '@/components/ui/LayerToggles'
import { layerOf } from '@/domain/families'
import { CitizenReportControl } from '@/components/report/CitizenReportControl'
import { AppHeader } from '@/components/ui/AppHeader'
import { MapOverlayState } from '@/components/ui/MapOverlayState'
import { StalenessBanner } from '@/components/ui/StalenessBanner'
import { levelOf } from '@/domain/symbology'
import { useActiveIncidents } from '@/hooks/useActiveIncidents'
import { useSeismicEvents } from '@/hooks/useSeismicEvents'
import { useFreshness } from '@/hooks/useFreshness'
import { useOnlineStatus } from '@/hooks/useOnlineStatus'

export default function App() {
  const [selectedCode, setSelectedCode] = useState<string | null>(null)
  const [selectedUsgsId, setSelectedUsgsId] = useState<string | null>(null)
  const [confirmedOnly, setConfirmedOnly] = useState(false)
  const [visibility, setVisibility] = useState<LayerVisibility>(DEFAULT_LAYER_VISIBILITY)

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
  const seismicList = seismic ?? []

  const anyIncidentLayer = visibility.fire || visibility.traffic || visibility.otros

  const isOnline = useOnlineStatus()
  const freshness = useFreshness(dataUpdatedAt || undefined)

  const all = incidents ?? []

  // Una sola consulta a `/incidents/active` alimenta las tres capas; el filtro
  // es por familia y ocurre acá. Separarlo en tres consultas multiplicaría el
  // tráfico sin ganar nada: el backend ya devuelve todo junto.
  const list = useMemo(
    () => all.filter((incident) => visibility[layerOf(incident.type)]),
    [all, visibility],
  )

  const countsByLayer = useMemo(() => {
    const counts = { fire: 0, traffic: 0, otros: 0 }
    for (const incident of all) counts[layerOf(incident.type)] += 1
    return counts
  }, [all])
  const selected = useMemo(
    () => list.find((incident) => incident.code === selectedCode) ?? null,
    [list, selectedCode],
  )

  const selectedSeismic = useMemo(
    () => seismicList.find((event) => event.usgs_id === selectedUsgsId) ?? null,
    [seismicList, selectedUsgsId],
  )

  const byLevel = useMemo(() => {
    const counts = { unsafe: 0, possible: 0, confirmed: 0 }
    for (const incident of list) counts[levelOf(incident)] += 1
    return counts
  }, [list])
  const withAlert = list.filter((incident) => incident.alert_level !== null).length

  return (
    <div className="flex h-[100dvh] flex-col overflow-hidden bg-slate-100">
      <AppHeader
        total={list.length}
        byLevel={byLevel}
        withAlert={withAlert}
        confirmedOnly={confirmedOnly}
        onToggleConfirmedOnly={setConfirmedOnly}
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
          incidents={list}
          seismic={seismicList}
          showIncidents={
            visibility.fire || visibility.traffic || visibility.otros
          }
          showSeismic={visibility.seismic}
          selectedCode={selectedCode}
          selectedUsgsId={selectedUsgsId}
          onSelect={setSelectedCode}
          onSelectSeismic={setSelectedUsgsId}
        />

        <MapLegend />

        <LayerToggles
          visibility={visibility}
          onChange={setVisibility}
          counts={{ ...countsByLayer, seismic: seismicList.length }}
        />

        {/*
          El botón vive dentro del `main` relativo, no en el árbol del mapa: así
          no compite con los controles de MapLibre ni se pierde en un repintado
          del canvas. En teléfono se oculta mientras la ficha del incidente está
          abierta, porque esa ficha ocupa el mismo borde inferior.
        */}
        <CitizenReportControl hiddenOnMobile={selected !== null} />

        {isPending && (
          <MapOverlayState
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
