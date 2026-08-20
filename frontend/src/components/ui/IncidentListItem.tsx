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
          'flex w-full items-start gap-2 rounded-lg px-1.5 py-1.5 text-left transition ' +
          (selected
            ? 'bg-slate-200 dark:bg-slate-700'
            : 'hover:bg-slate-100 dark:hover:bg-slate-800')
        }
      >
        <span
          aria-hidden
          className="mt-1 size-2.5 shrink-0 rounded-full ring-2 ring-white dark:ring-slate-900"
          style={{ backgroundColor: style.color }}
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[11px] font-semibold text-slate-800 dark:text-slate-200">
            {incident.title ?? TYPE_LABEL[incident.type]}
          </span>
          <span className="block truncate text-[10px] text-slate-500 dark:text-slate-400">
            {incident.commune ?? 'sin comuna'} · {formatPercent(incident.confidence)} ·{' '}
            {formatRelative(incident.last_seen_at)}
          </span>
          {incident.status !== 'active' && (
            <span className="block text-[10px] text-slate-400 dark:text-slate-500">
              {STATUS_LABEL[incident.status]}
            </span>
          )}
        </span>
        <span
          aria-hidden
          className="mt-0.5 shrink-0 text-[10px] text-slate-400 dark:text-slate-500"
          title="Centrar en el mapa"
        >
          ➤
        </span>
      </button>
    </li>
  )
}
