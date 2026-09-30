import { memo } from 'react'
import { RELATIVE_TIME_TICK_MS, useNow } from '@/hooks/useNow'
import { formatClock, formatRelativeShort } from '@/lib/format'

/**
 * «Último despacho de Bomberos: 10:01 · hace 2 h», bajo «N en curso».
 *
 * Distingue un día tranquilo de un motor que dejó de recibir despachos. Tiene
 * su propio reloj para que el «hace …» avance sin repintar la columna.
 */
export const LastDispatch = memo(function LastDispatch({ at }: { at: number | null }) {
  const now = useNow(RELATIVE_TIME_TICK_MS)
  return (
    <p className="truncate px-4 pb-2 text-[11px] leading-tight text-ink-muted">
      {at === null ? (
        'Sin despachos de Bomberos en las últimas 48 h'
      ) : (
        <>
          Último despacho de Bomberos:{' '}
          <span className="count font-medium text-ink">{formatClock(at)}</span> ·{' '}
          {formatRelativeShort(at, now)}
        </>
      )}
    </p>
  )
})
