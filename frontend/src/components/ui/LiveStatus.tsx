import { useIncidentsFreshness } from '@/hooks/useIncidentsFreshness'
import { RELATIVE_TIME_TICK_MS, useNow } from '@/hooks/useNow'
import { formatRelativeShort } from '@/lib/format'
import { cn } from '@/lib/cn'

/**
 * «● En vivo · hace 1 min», bajo la marca.
 *
 * Reemplaza a las cápsulas de conteo: el número de activos ya está en la
 * columna («5 en curso»), y lo que la barra tiene que decir es que lo que se ve
 * es de ahora. Cuando no lo es, el cartel de `StalenessBanner` lo dice en
 * grande; esto es la versión tranquila.
 *
 * Lee la frescura del caché y su propio reloj: la barra no se repinta.
 */
export function LiveStatus() {
  const { updatedAt, fetching } = useIncidentsFreshness()
  const now = useNow(RELATIVE_TIME_TICK_MS)
  const live = updatedAt > 0

  return (
    <p className="flex items-center gap-1.5 text-[10.5px] leading-none text-white/55">
      <span
        aria-hidden
        className={cn(
          'size-1.5 shrink-0 rounded-full',
          live ? 'animate-pulse-soft bg-emerald-400' : 'bg-white/30',
        )}
      />
      <span className="hidden md:inline">Región de Valparaíso ·</span>
      <span className="truncate">
        {!live
          ? 'Conectando…'
          : fetching
            ? 'Actualizando…'
            : `En vivo · ${formatRelativeShort(updatedAt, now)}`}
      </span>
    </p>
  )
}
