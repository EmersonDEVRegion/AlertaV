import { memo } from 'react'
import type { HTMLAttributes, ReactNode } from 'react'
import { LegendBody } from '@/components/map/MapLegend'
import { HistoryFeed, type HistoryFeedProps } from '@/components/feed/HistoryFeed'
import { NewsFeed } from '@/components/feed/NewsFeed'
import { DetailEmbed } from '@/components/incident/DetailSurface'
import { SelectionDetails } from '@/components/incident/SelectionDetails'
import { IncidentFilters, type SidePanelProps } from '@/components/ui/SidePanel'
import { ReferenceDock, type ReferenceDockProps } from '@/components/ui/ReferenceDock'
import type { SeismicEvent } from '@/api/seismicTypes'
import type { Incident } from '@/api/types'
import type { WaterCut } from '@/api/waterCutTypes'
import { SavedPlaces } from '@/components/places/SavedPlaces'
import { cn } from '@/lib/cn'
import { LastDispatch } from './LastDispatch'
import { setExploreTab, useExploreTab, type ExploreArea, type ExploreTab } from '@/lib/exploreStore'

/**
 * El contenido de la columna (escritorio) y de la hoja inferior (teléfono).
 *
 * # Una sola columna, como en los sitios de este tipo
 *
 * Watch Duty y VicEmergency resuelven lo mismo con la misma forma: una columna
 * de contenido y el mapa libre al lado. Acá la columna tiene tres pestañas y,
 * cuando hay algo seleccionado, la ficha las reemplaza en el mismo lugar:
 *
 *   - **Historial** (por defecto): «Mis lugares», las capas de referencia
 *     plegadas, el feed de las últimas 24 h debajo y, al final, las noticias
 *     de la prensa local (fuera del mapa desde el 2026-10-06).
 *   - **Capas**: las familias de emergencia con sus contadores, su salud, las
 *     empresas de luz y el filtro de sismos. Es el panel derecho de antes.
 *   - **Leyenda**: la leyenda compacta.
 *
 * El mismo componente vive en las dos pantallas; sólo cambia el contenedor.
 * Antes había dos paneles flotantes en escritorio y una barra de fichas en el
 * teléfono, con reglas de exclusión distintas en cada uno.
 */
interface ExploreSelection {
  incidents: readonly Incident[]
  seismic: readonly SeismicEvent[]
  waterCuts: readonly WaterCut[]
}

export interface ExplorePanelProps {
  incidents: SidePanelProps
  reference: ReferenceDockProps
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
  { key: 'history', label: 'Historial' },
  { key: 'layers', label: 'Capas' },
  { key: 'legend', label: 'Leyenda' },
]

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
  const historyCount = history.history.length

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
            {history.ready && (
              <span className="min-w-0 flex-1 truncate text-xs text-ink-muted">
                · <span className="count">{historyCount}</span> en 24 h
              </span>
            )}
            {headerAction && <span className="ml-auto self-center">{headerAction}</span>}
          </div>
          {/* Calma o falla: sin esto, un mapa quieto se lee igual en los dos casos. */}
          {history.ready && lastDispatchAt !== undefined && <LastDispatch at={lastDispatchAt} />}
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
                {t.key === 'history' && history.ready && historyCount > 0 && (
                  <span aria-hidden className="count text-[10px] text-ink-faint">
                    {historyCount}
                  </span>
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
            {tab === 'history' && (
              <div className="space-y-2">
                <SavedPlaces
                  history={history.history}
                  onMapCodes={history.onMapCodes}
                  onFocusArea={onFocusArea}
                  onFocusCuartel={onFocusCuartel}
                />
                {/* Las capas de referencia arriba del historial, plegadas: se
                    encienden de vez en cuando y no pueden empujar la lista. */}
                <ReferenceDock {...reference} inline defaultOpen={false} />
                <HistoryFeed {...history} />
                {/* La prensa, debajo y aparte: desde el 2026-10-06 no va al
                    mapa, sólo se informa. */}
                <NewsFeed />
              </div>
            )}
            {tab === 'layers' && <IncidentFilters {...incidents} />}
            {tab === 'legend' && (
              <div className="px-1.5 py-1">
                <LegendBody />
              </div>
            )}
          </div>
        </SelectionDetails>
      </div>
    </DetailEmbed>
  )
})
