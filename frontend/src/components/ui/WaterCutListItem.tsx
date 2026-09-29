import { memo } from 'react'
import type { WaterCut } from '@/api/waterCutTypes'
import { WATER_ICON } from '@/domain/emergencyIcons'
import { WATER } from '@/domain/waterSymbology'
import { formatDateTime } from '@/lib/format'
import { GlyphIcon } from './GlyphIcon'

interface WaterCutListItemProps {
  cut: WaterCut
  selected: boolean
  onSelect: (cut: WaterCut) => void
}

/**
 * Fila de un corte de agua en el acordeón del panel.
 *
 * Lo que un vecino necesita para decidir si es el suyo: comuna, calles y hasta
 * cuándo. La hora es la referencial de Esval; la tarjeta lo aclara.
 */
export const WaterCutListItem = memo(function WaterCutListItem({
  cut,
  selected,
  onSelect,
}: WaterCutListItemProps) {
  const where = cut.calles ?? cut.sector
  const detail = [
    where,
    cut.fin ? `hasta ${formatDateTime(cut.fin)}` : null,
    cut.coordinates ? null : 'sin ubicación en el mapa',
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(cut)}
        aria-current={selected ? 'true' : undefined}
        className={
          'flex w-full items-start gap-2 rounded-control px-1.5 py-1.5 text-left transition ' +
          (selected ? 'bg-accent-soft' : 'hover:bg-hover')
        }
      >
        {/* El mismo glifo que en el mapa. */}
        <GlyphIcon id={WATER_ICON} className="mt-0.5 size-3.5 shrink-0" color={WATER.color} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[11px] font-semibold text-ink">
            {cut.comuna ?? 'Comuna sin informar'}
            {cut.programado === false && (
              <span className="ml-1 font-medium text-warn-ink">· emergencia</span>
            )}
          </span>
          {detail && (
            <span className="block truncate text-[10px] text-ink-muted">{detail}</span>
          )}
        </span>
        {cut.coordinates && (
          <span
            aria-hidden
            className="mt-0.5 shrink-0 text-[10px] text-ink-faint"
            title="Centrar en el mapa"
          >
            ➤
          </span>
        )}
      </button>
    </li>
  )
})
