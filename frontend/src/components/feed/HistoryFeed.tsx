import { memo, useMemo, useState } from 'react'
import type { Incident } from '@/api/types'
import type { CollectorsHealth } from '@/api/health'
import { TYPE_LABEL } from '@/domain/labels'
import { styleFor } from '@/domain/palette'
import { OUTAGE_ICON, iconFor } from '@/domain/emergencyIcons'
import { OUTAGE_CLUSTER } from '@/domain/powerSymbology'
import {
  HISTORY_STATE_LABEL,
  communeLabel,
  historySections,
  historyState,
  type HistoryOutageGroup,
  type HistoryState,
} from '@/domain/displayWindow'
import { RELATIVE_TIME_TICK_MS, useNow } from '@/hooks/useNow'
import { formatClock, formatRelativeShort } from '@/lib/format'
import { cn } from '@/lib/cn'
import { useSelectedIncidentCode } from '@/lib/selectionStore'
import { GlyphIcon } from '@/components/ui/GlyphIcon'

/**
 * Historial de las últimas 24 h: lo que está en el mapa y lo que ya salió.
 *
 * Es el feed que la gente lee de arriba hacia abajo en los sitios de este tipo
 * (Watch Duty, VicEmergency): qué pasó, dónde, hace cuánto y en qué quedó.
 * El estado nunca inventa un fin: «Sin novedades» dice que dejaron de llegar
 * señales, no que se apagó; «Ya no figura» dice que la empresa dejó de listar
 * el corte, no que repuso.
 */
export interface HistoryFeedProps {
  /** Las incidencias de las últimas 24 h, ya filtradas por las capas encendidas. */
  history: readonly Incident[]
  /** Códigos de las que siguen en el mapa. */
  onMapCodes: ReadonlySet<string>
  onFocus: (incident: Incident) => void
  /** Para no confundir un historial vacío con uno ciego. */
  health?: CollectorsHealth | undefined
  /** Ya cargó al menos una vez. Antes de eso no se afirma «nada». */
  ready: boolean
}

const STATE_TONE: Record<HistoryState, string> = {
  live: 'bg-accent-soft text-accent',
  controlled: 'bg-sunken text-ink-muted',
  extinguished: 'bg-sunken text-ink-muted',
  unlisted: 'bg-sunken text-ink-muted',
  quiet: 'bg-sunken text-ink-faint',
}

function StateChip({ state }: { state: HistoryState }) {
  return (
    <span
      className={cn(
        'shrink-0 rounded-full px-1.5 py-px text-[9.5px] font-semibold leading-4',
        STATE_TONE[state],
      )}
      title={
        state === 'unlisted'
          ? 'La empresa dejó de listarlo. Casi siempre significa que repuso el suministro.'
          : state === 'quiet'
            ? 'Dejaron de llegar señales. No significa que haya terminado.'
            : undefined
      }
    >
      {HISTORY_STATE_LABEL[state]}
    </span>
  )
}

const HistoryItem = memo(function HistoryItem({
  incident,
  onMap,
  selected,
  now,
  onFocus,
  nested = false,
}: {
  incident: Incident
  onMap: boolean
  selected: boolean
  now: number
  onFocus: (incident: Incident) => void
  nested?: boolean
}) {
  const style = styleFor(incident)
  const state = historyState(incident, onMap)
  const where = communeLabel(incident.commune) ?? 'Sin comuna'

  return (
    <li>
      <button
        type="button"
        onClick={() => onFocus(incident)}
        aria-current={selected ? 'true' : undefined}
        className={cn(
          'flex w-full items-start gap-2 rounded-control px-1.5 py-1.5 text-left transition-colors',
          selected ? 'bg-accent-soft' : 'hover:bg-hover',
          nested && 'py-1',
        )}
      >
        <GlyphIcon
          id={iconFor(incident.type)}
          className={cn('mt-0.5 size-3.5 shrink-0', !onMap && 'opacity-60')}
          color={style.color}
        />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span
              className={cn(
                'min-w-0 flex-1 truncate text-[11.5px] font-semibold',
                onMap ? 'text-ink' : 'text-ink-muted',
              )}
            >
              {nested ? where : (incident.title ?? `${TYPE_LABEL[incident.type]} — ${where}`)}
            </span>
            <time
              dateTime={incident.last_seen_at}
              className="shrink-0 text-[10px] text-ink-faint"
            >
              {formatClock(incident.last_seen_at)}
            </time>
          </span>
          <span className="mt-0.5 flex items-center gap-1.5">
            <span className="min-w-0 flex-1 truncate text-[10px] text-ink-muted">
              {nested
                ? incident.outage?.affected_clients != null
                  ? `${incident.outage.affected_clients.toLocaleString('es-CL')} clientes`
                  : formatRelativeShort(incident.last_seen_at, now)
                : formatRelativeShort(incident.last_seen_at, now)}
            </span>
            {/* «En curso» ya lo dice la sección «En el mapa»: repetirlo en cada
                fila es ruido. Se muestra lo que la distingue. */}
            {state !== 'live' && <StateChip state={state} />}
          </span>
        </span>
      </button>
    </li>
  )
})

const OutageGroupItem = memo(function OutageGroupItem({
  group,
  selectedCode,
  now,
  onFocus,
}: {
  group: HistoryOutageGroup
  selectedCode: string | null
  now: number
  onFocus: (incident: Incident) => void
}) {
  const containsSelected = group.incidents.some((i) => i.code === selectedCode)
  const [open, setOpen] = useState(false)
  const expanded = open || containsSelected
  const state = historyState(group.incidents[0]!, group.onMap)
  const clients =
    group.clients != null ? `${group.clients.toLocaleString('es-CL')} clientes · ` : ''
  const span =
    formatClock(group.earliest) === formatClock(group.latest)
      ? formatClock(group.latest)
      : `${formatClock(group.earliest)}–${formatClock(group.latest)}`

  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-start gap-2 rounded-control px-1.5 py-1.5 text-left transition-colors hover:bg-hover"
      >
        <span
          aria-hidden
          className={cn(
            'mt-0.5 grid size-3.5 shrink-0 place-items-center rounded-full',
            !group.onMap && 'opacity-60',
          )}
          style={{ backgroundColor: OUTAGE_CLUSTER.color }}
        >
          <GlyphIcon id={OUTAGE_ICON} className="size-2.5" color={OUTAGE_CLUSTER.onColor} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span
              className={cn(
                'min-w-0 flex-1 truncate text-[11.5px] font-semibold',
                group.onMap ? 'text-ink' : 'text-ink-muted',
              )}
            >
              {group.incidents.length} cortes de luz — {group.commune ?? 'sin comuna'}
            </span>
            <span className="shrink-0 text-[10px] text-ink-faint">{span}</span>
          </span>
          <span className="mt-0.5 flex items-center gap-1.5">
            <span className="min-w-0 flex-1 truncate text-[10px] text-ink-muted">
              {clients}
              {expanded ? 'ocultar' : 'ver cada uno'}
            </span>
            {state !== 'live' && <StateChip state={state} />}
          </span>
        </span>
      </button>
      {expanded && (
        <ul className="ml-5 border-l border-line pl-1.5">
          {group.incidents.map((incident) => (
            <HistoryItem
              key={incident.code}
              incident={incident}
              onMap={group.onMap}
              selected={incident.code === selectedCode}
              now={now}
              onFocus={onFocus}
              nested
            />
          ))}
        </ul>
      )}
    </li>
  )
})

/** El contenido del historial, sin superficie: lo montan el riel y la ficha del teléfono. */
export const HistoryFeed = memo(function HistoryFeed({
  history,
  onMapCodes,
  onFocus,
  health,
  ready,
}: HistoryFeedProps) {
  const now = useNow(RELATIVE_TIME_TICK_MS)
  const selectedCode = useSelectedIncidentCode()
  const sections = useMemo(
    () => historySections(history, onMapCodes, now),
    [history, onMapCodes, now],
  )

  if (!ready) {
    return (
      <div className="space-y-1.5 p-1" aria-busy>
        {[0, 1, 2].map((i) => (
          <div key={i} className="shimmer h-8 rounded-control" />
        ))}
      </div>
    )
  }

  if (sections.length === 0) {
    const blind = Object.values(health?.by_family ?? {}).some(
      (status) => status === 'failing' || status === 'stale',
    )
    return blind ? (
      <p className="callout callout-warn m-1 text-[10.5px]">
        Sin emergencias en 24 h, pero algunas fuentes no responden: el vacío puede no ser
        calma.
      </p>
    ) : (
      <p className="px-2 py-3 text-center text-[10.5px] text-ink-muted">
        Sin emergencias en las últimas 24 h en las capas encendidas.
      </p>
    )
  }

  return (
    <div className="space-y-2">
      {sections.map((section) => (
        // `group` y no `section`: con nombre, una sección es un `region`, y la
        // ficha del teléfono ya es la región de este contenido.
        <div key={section.key} role="group" aria-label={section.label}>
          <h3 className="flex items-center gap-1.5 px-1.5 pb-0.5 pt-1 text-[9.5px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
            {section.key === 'now' && (
              <span aria-hidden className="size-1.5 animate-pulse-soft rounded-full bg-accent" />
            )}
            <span className="flex-1">{section.label}</span>
            <span className="count">{section.count}</span>
          </h3>
          <ul>
            {section.entries.map((entry) =>
              entry.kind === 'single' ? (
                <HistoryItem
                  key={entry.key}
                  incident={entry.incident}
                  onMap={entry.onMap}
                  selected={entry.incident.code === selectedCode}
                  now={now}
                  onFocus={onFocus}
                />
              ) : (
                <OutageGroupItem
                  key={entry.key}
                  group={entry}
                  selectedCode={selectedCode}
                  now={now}
                  onFocus={onFocus}
                />
              ),
            )}
          </ul>
        </div>
      ))}
    </div>
  )
})
