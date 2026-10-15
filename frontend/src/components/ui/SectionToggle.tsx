import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * Encabezado plegable de una sección: chevrón, rótulo en versalitas y conteo.
 *
 * Lo comparten las secciones del historial («En vivo», «Hoy», «Ayer») y las
 * Noticias (§I), para que plegar se vea y se toque igual en toda la pestaña.
 *
 * Todo el encabezado es el botón: en el teléfono un objetivo del ancho de la
 * hoja se acierta sin mirar.
 */
export function SectionToggle({
  label,
  count,
  open,
  onToggle,
  controls,
  leading,
  preview,
  className,
}: {
  label: string
  count?: number
  open: boolean
  onToggle: () => void
  /** `id` del contenido que pliega. */
  controls: string
  /** Algo antes del rótulo: el punto que late de «En vivo». */
  leading?: ReactNode
  /** Una línea bajo el rótulo, sólo plegada: «el último titular», por ejemplo. */
  preview?: ReactNode
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      aria-controls={controls}
      className={cn(
        'flex w-full flex-col rounded-control px-1.5 py-1 text-left transition-colors hover:bg-hover',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent',
        className,
      )}
    >
      <span className="flex w-full items-center gap-1.5">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
          className={cn(
            'size-3 shrink-0 text-ink-faint transition-transform duration-300 motion-reduce:transition-none',
            !open && '-rotate-90',
          )}
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
        {leading}
        <span className="min-w-0 flex-1 truncate text-[9.5px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
          {label}
        </span>
        {count !== undefined && (
          <span className="count text-[10px] text-ink-faint">{count}</span>
        )}
      </span>
      {!open && preview && (
        <span className="mt-0.5 block w-full truncate pl-[18px] text-[10.5px] font-normal normal-case tracking-normal text-ink-muted">
          {preview}
        </span>
      )}
    </button>
  )
}
