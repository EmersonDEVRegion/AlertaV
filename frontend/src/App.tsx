import { useMemo, useState } from 'react'
import type { ActiveIncidentsQuery } from '@/api/types'
import { IncidentMap } from '@/components/map/IncidentMap'
import { MapLegend } from '@/components/map/MapLegend'
import { IncidentSheet } from '@/components/incident/IncidentSheet'
import { CitizenReportControl } from '@/components/report/CitizenReportControl'
import { AppHeader } from '@/components/ui/AppHeader'
import { MapOverlayState } from '@/components/ui/MapOverlayState'
import { StalenessBanner } from '@/components/ui/StalenessBanner'
import { useActiveIncidents } from '@/hooks/useActiveIncidents'
import { useFreshness } from '@/hooks/useFreshness'
import { useOnlineStatus } from '@/hooks/useOnlineStatus'

export default function App() {
  const [selectedCode, setSelectedCode] = useState<string | null>(null)
  const [confirmedOnly, setConfirmedOnly] = useState(false)

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

  const isOnline = useOnlineStatus()
  const freshness = useFreshness(dataUpdatedAt || undefined)

  const list = incidents ?? []
  const selected = useMemo(
    () => list.find((incident) => incident.code === selectedCode) ?? null,
    [list, selectedCode],
  )

  const confirmed = list.filter((incident) => incident.is_official_confirmed).length
  const withAlert = list.filter((incident) => incident.alert_level !== null).length

  return (
    <div className="flex h-[100dvh] flex-col overflow-hidden bg-slate-100">
      <AppHeader
        total={list.length}
        confirmed={confirmed}
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
          selectedCode={selectedCode}
          onSelect={setSelectedCode}
        />

        <MapLegend />

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

        {!isPending && list.length === 0 && !isError && (
          <MapOverlayState
            title="Sin incidentes activos"
            detail={
              confirmedOnly
                ? 'No hay incidentes confirmados por CONAF o Bomberos en la ventana activa. Desmarca el filtro para ver los que están en investigación.'
                : 'El motor de correlación no tiene incidentes vigentes en la Región de Valparaíso.'
            }
          />
        )}

        {!isPending && list.length === 0 && isError && (
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
      </main>
    </div>
  )
}
