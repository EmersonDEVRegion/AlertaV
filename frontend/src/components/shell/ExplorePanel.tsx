import { memo, useMemo } from 'react'
import type { HTMLAttributes, ReactNode } from 'react'
import { HistoryFeed, type HistoryFeedProps } from '@/components/feed/HistoryFeed'
import { NewsFeed } from '@/components/feed/NewsFeed'
import { DetailEmbed } from '@/components/incident/DetailSurface'
import { SelectionDetails } from '@/components/incident/SelectionDetails'
import { LayersTab, type EmergencyLayersProps } from '@/components/layers/LayersTab'
import type { ReferenceLayersProps } from '@/components/layers/ReferenceLayers'
import { hiddenFilterCount } from '@/domain/layers'
import type { SeismicEvent } from '@/api/seismicTypes'
import type { Incident } from '@/api/types'
import type { WaterCut } from '@/api/waterCutTypes'
import { SavedPlaces } from '@/components/places/SavedPlaces'
import { cn } from '@/lib/cn'
import { LastDispatch } from './LastDispatch'
import { LAST_24H_MS } from '@/domain/displayWindow'
import { RELATIVE_TIME_TICK_MS, useNow } from '@/hooks/useNow'
import { setExploreTab, useExploreTab, type ExploreArea, type ExploreTab } from '@/lib/exploreStore'
import { PropagationNotice } from '@/components/shell/PropagationNotice'

/**
 * El contenido de la columna (escritorio) y de la hoja inferior (teléfono).
 *
 * # Una sola columna, como en los sitios de este tipo
 *
 * Watch Duty y VicEmergency resuelven lo mismo con la misma forma: una columna
 * de contenido y el mapa libre al lado. Acá la columna tiene tres pestañas y,
 * cuando hay algo seleccionado, la ficha las reemplaza en el mismo lugar:
 *
 *   - **Ahora** (por defecto): «En vivo», «Servicios interrumpidos»
 *     (cortes de luz, plegados) y «Mis lugares».
 *   - **Historial**: «Hoy», «Ayer» y «Antes», y la prensa local plegada.
 *   - **Capas**: el mosaico de capas de emergencia con sus filtros, las de
 *     referencia y la única leyenda (`components/layers/LayersTab.tsx`).
 *
 * El mismo componente vive en las dos pantallas; sólo cambia el contenedor, y
 * las reglas de exclusión son las mismas en escritorio y en el teléfono.
 */
interface ExploreSelection {
  incidents: readonly Incident[]
  seismic: readonly SeismicEvent[]
  waterCuts: readonly WaterCut[]
}

export interface ExplorePanelProps {
  incidents: EmergencyLayersProps
  reference: ReferenceLayersProps
  history: HistoryFeedProps
  selection: ExploreSelection
  /** Incidentes en el mapa con las capas encendidas. */
  incidentCount: number
  /**
   * Hora del último despacho de Bomberos (ms), `null` si no hubo en 48 h.
   * Sin la prop (`undefined`) la línea no se muestra.
   */
  lastDispatchAt?: number | null
  /** «Mis lugares» y el buscador: encuadra el área y acota el historial. */
  onFocusArea: (area: ExploreArea) => void
  /** «Mis lugares»: vuela a un cuartel cercano y enciende su capa. */
  onFocusCuartel: (lon: number, lat: number) => void
}

interface ExploreChrome {
  /** Algo encima del resumen: el asa de la hoja en el teléfono. */
  grip?: ReactNode
  /** Manejadores del encabezado: en el teléfono, el arrastre de la hoja. */
  headerProps?: HTMLAttributes<HTMLDivElement>
  /** Cualquier toque en las pestañas: la hoja se abre si estaba asomada. */
  onInteract?: () => void
  /** Una acción a la derecha del resumen: «Reportar» en el teléfono. */
  headerAction?: ReactNode
  /** El área con scroll de las pestañas: la hoja le engancha el gesto de la lista. */
  scrollRef?: (el: HTMLDivElement | null) => void
}

const TABS: readonly { key: ExploreTab; label: string }[] = [
  { key: 'now', label: 'Ahora' },
  { key: 'history', label: 'Historial' },
  { key: 'layers', label: 'Capas' },
]

/**
 * «· N en 24 h» del resumen.
 *
 * El historial trae hoy y ayer completos (entre 24 y 48 h), así que su largo
 * no es «en 24 h». El resumen sigue contando las últimas 24 h, que
 * es lo que dice; la pestaña Historial cuenta lo que lista. Va en su propio
 * componente para que el reloj no repinte el panel entero.
 */
function Last24h({ history }: { history: HistoryFeedProps['history'] }) {
  const now = useNow(RELATIVE_TIME_TICK_MS)
  const count = useMemo(() => {
    const since = now - LAST_24H_MS
    return history.filter((incident) => Date.parse(incident.last_seen_at) >= since).length
  }, [history, now])
  return (
    <span className="min-w-0 flex-1 truncate text-xs text-ink-muted">
      · <span className="count">{count}</span> en 24 h
    </span>
  )
}

export const ExplorePanel = memo(function ExplorePanel({
  incidents,
  reference,
  history,
  selection,
  incidentCount,
  lastDispatchAt,
  onFocusArea,
  onFocusCuartel,
  grip,
  headerProps,
  onInteract,
  headerAction,
  scrollRef,
}: ExplorePanelProps & ExploreChrome) {
  // En un store y no en estado local: el menú «⋯» de la barra abre la leyenda
  // y el buscador vuelve al historial, sin pasar por `App`.
  const tab = useExploreTab()
  // Lo que está en el mapa cuenta en «Ahora»; lo que ya salió, en «Historial».
  const nowCount = useMemo(
    () => history.history.filter((incident) => history.onMapCodes.has(incident.code)).length,
    [history.history, history.onMapCodes],
  )
  const tabCount: Partial<Record<ExploreTab, number>> = {
    now: nowCount,
    history: history.history.length - nowCount,
  }

  // Un filtro olvidado es la forma más fácil de leer mal el mapa: la pestaña
  // lo avisa aunque se esté mirando otra.
  const hiddenFilters = hiddenFilterCount(
    incidents.visibility,
    incidents.providers,
    Boolean(incidents.confirmedOnly),
  )

  const choose = (next: ExploreTab) => {
    setExploreTab(next)
    onInteract?.()
  }

  return (
    <DetailEmbed>
      <div className="flex h-full min-h-0 flex-col">
        <div {...headerProps} className={cn('shrink-0', headerProps?.className)}>
          {grip}
          {/* El resumen se lee sin abrir nada: es lo único que asoma en el teléfono. */}
          <div className="flex items-baseline gap-2 px-4 pb-2 pt-3">
            <span
              aria-hidden
              className={cn(
                'size-2 shrink-0 self-center rounded-full',
                incidentCount > 0 ? 'animate-pulse-soft bg-urgent' : 'bg-line-strong',
              )}
            />
            <h2 className="text-sm font-bold text-ink">
              <span className="count">{incidentCount}</span> en curso
            </h2>
            {history.ready && <Last24h history={history.history} />}
            {headerAction && <span className="ml-auto self-center">{headerAction}</span>}
          </div>
          {/* Calma o falla: sin esto, un mapa quieto se lee igual en los dos casos. */}
          {history.ready && lastDispatchAt !== undefined && <LastDispatch at={lastDispatchAt} />}
          <PropagationNotice />
        </div>

        <SelectionDetails
          incidents={selection.incidents}
          seismic={selection.seismic}
          waterCuts={selection.waterCuts}
        >
          <div
            role="tablist"
            aria-label="Contenido de la columna"
            className="mx-3 mb-2 flex shrink-0 rounded-control bg-sunken p-0.5"
          >
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                id={`explore-tab-${t.key}`}
                aria-selected={tab === t.key}
                aria-controls="explore-tabpanel"
                onClick={() => choose(t.key)}
                className={cn(
                  'flex h-8 flex-1 items-center justify-center gap-1.5 rounded-control text-xs font-medium transition',
                  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent',
                  tab === t.key
                    ? 'bg-raised text-ink shadow-sm'
                    : 'text-ink-muted hover:text-ink',
                )}
              >
                {t.label}
                {history.ready && (tabCount[t.key] ?? 0) > 0 && (
                  <span aria-hidden className="count text-[10px] text-ink-faint">
                    {tabCount[t.key]}
                  </span>
                )}
                {t.key === 'layers' && hiddenFilters > 0 && (
                  <>
                    <span aria-hidden className="size-1.5 rounded-full bg-accent" />
                    <span className="sr-only">
                      {hiddenFilters === 1 ? ', 1 filtro activo' : `, ${hiddenFilters} filtros activos`}
                    </span>
                  </>
                )}
              </button>
            ))}
          </div>

          <div
            ref={scrollRef}
            id="explore-tabpanel"
            role="tabpanel"
            aria-labelledby={`explore-tab-${tab}`}
            className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2.5 pb-[max(0.75rem,env(safe-area-inset-bottom))]"
          >
            {tab === 'now' && (
              <div className="space-y-2">
                <HistoryFeed {...history} scope="now" />
                {/* «Mis lugares» acá: es la pregunta de «Ahora», ¿hay algo
                    cerca de mi casa? Sin lugares guardados es una invitación. */}
                <SavedPlaces
                  history={history.history}
                  onMapCodes={history.onMapCodes}
                  onFocusArea={onFocusArea}
                  onFocusCuartel={onFocusCuartel}
                />
              </div>
            )}
            {tab === 'history' && (
              <div className="space-y-2">
                <HistoryFeed {...history} scope="past" />
                {/* La prensa al final y plegada: es contexto, no entra al mapa
                    y abierta empujaría la lista. */}
                <NewsFeed defaultOpen={false} />
              </div>
            )}
            {tab === 'layers' && <LayersTab emergencies={incidents} reference={reference} />}
          </div>
        </SelectionDetails>
      </div>
    </DetailEmbed>
  )
})
