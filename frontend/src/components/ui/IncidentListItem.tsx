import type { Incident } from '@/api/types'
import { STATUS_LABEL, TYPE_LABEL } from '@/domain/labels'
import { styleFor } from '@/domain/palette'
import { formatPercent, formatRelative } from '@/lib/format'

interface IncidentListItemProps {
  incident: Incident
  selected: boolean
  onSelect: (incident: Incident) => void
}

/** Fila del acordeón: lo mínimo para decidir si vale la pena volar hasta allá. */
export function IncidentListItem({
  incident,
  selected,
  onSelect,
}: IncidentListItemProps) {
  const style = styleFor(incident)

  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(incident)}
        aria-current={selected ? 'true' : undefined}
        className={
          'flex w-full items-start gap-2 rounded-control px-1.5 py-1.5 text-left transition ' +
          (selected
            ? 'bg-accent-soft'
            : 'hover:bg-hover')
        }
      >
        <span
          aria-hidden
          className="mt-1 size-2.5 shrink-0 rounded-full ring-2 ring-panel"
          style={{ backgroundColor: style.color }}
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[11px] font-semibold text-ink">
            {incident.title ?? TYPE_LABEL[incident.type]}
          </span>
          <span className="block truncate text-[10px] text-ink-muted">
            {incident.commune ?? 'sin comuna'} · {formatPercent(incident.confidence)} ·{' '}
            {formatRelative(incident.last_seen_at)}
          </span>
          {incident.status !== 'active' && (
            <span className="block text-[10px] text-ink-faint">
              {STATUS_LABEL[incident.status]}
            </span>
          )}
        </span>
        <span
          aria-hidden
          className="mt-0.5 shrink-0 text-[10px] text-ink-faint"
          title="Centrar en el mapa"
        >
          ➤
        </span>
      </button>
    </li>
  )
}
