import { formatPercent } from '@/lib/format'

interface ConfidenceBarProps {
  label: string
  value: number
  color: string
  /** Que mide exactamente esta barra. Se muestra siempre, no en un tooltip. */
  caption: string
  emphasis?: string
}

/**
 * Una barra por eje, con su explicacion pegada.
 *
 * `confidence` y `alert_confidence` son dos numeros entre 0 y 1 que aparecen
 * juntos y significan cosas incomparables. Mostrarlos con la misma forma pero
 * sin decir que mide cada uno es la manera más facil de que alguien lea "96 %"
 * y entienda lo que no es.
 */
export function ConfidenceBar({
  label,
  value,
  color,
  caption,
  emphasis,
}: ConfidenceBarProps) {
  const pct = Math.max(0, Math.min(1, value)) * 100

  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          {label}
        </span>
        <span className="text-sm font-bold tabular-nums text-slate-900 dark:text-slate-100">
          {formatPercent(value)}
          {emphasis && (
            <span className="ml-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">
              {emphasis}
            </span>
          )}
        </span>
      </div>

      <div
        role="meter"
        aria-label={`${label}: ${formatPercent(value)}`}
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
        className="mt-1 h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700"
      >
        <div
          className="h-full rounded-full transition-[width] duration-500"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>

      <p className="mt-1 text-[11px] leading-snug text-slate-500 dark:text-slate-400">{caption}</p>
    </div>
  )
}
