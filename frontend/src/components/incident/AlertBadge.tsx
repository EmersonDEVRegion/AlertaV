import type { AlertLevel } from '@/api/types'
import { alertStyle } from '@/domain/symbology'

export function AlertBadge({ level }: { level: AlertLevel | null }) {
  const style = alertStyle(level)
  if (!style) return null

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${style.chip}`}
    >
      <span
        aria-hidden
        className="size-2 rounded-full bg-current opacity-80"
      />
      SENAPRED · {style.label}
    </span>
  )
}
