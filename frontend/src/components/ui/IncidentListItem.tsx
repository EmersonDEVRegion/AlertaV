import { memo } from 'react'
import type { Incident } from '@/api/types'
import { STATUS_LABEL, TYPE_LABEL } from '@/domain/labels'
import { styleFor } from '@/domain/palette'
import { iconFor } from '@/domain/emergencyIcons'
import { formatPercent, formatRelative } from '@/lib/format'
import { GlyphIcon } from './GlyphIcon'

interface IncidentListItemProps {
  incident: Incident
  selected: boolean
  onSelect: (incident: Incident) => void
  /** Reloj con que se calcula «hace X». Lo pide la lista una vez para todas las filas. */
  now: number
}

/** Fila del acordeón: lo mínimo para decidir si vale la pena volar hasta allá. */
export const IncidentListItem = memo(function IncidentListItem({
  incident,
  selected,
  onSelect,
  now,
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
        {/* El mismo glifo que en el mapa, con el color de su confianza. */}
        <GlyphIcon
          id={iconFor(incident.type)}
          className="mt-0.5 size-3.5 shrink-0"
          color={style.color}
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[11px] font-semibold text-ink">
            {incident.title ?? TYPE_LABEL[incident.type]}
          </span>
          <span className="block truncate text-[10px] text-ink-muted">
            {incident.commune ?? 'sin comuna'} · {formatPercent(incident.confidence)} ·{' '}
            {formatRelative(incident.last_seen_at, now)}
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
})
