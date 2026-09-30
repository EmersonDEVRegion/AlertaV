import { memo, useState } from 'react'
import { Panel } from '@/components/ui/primitives'
import { cn } from '@/lib/cn'
import { HistoryFeed, type HistoryFeedProps } from './HistoryFeed'

/**
 * El historial en el riel izquierdo de escritorio, debajo de las capas de
 * referencia.
 *
 * Es la única superficie del riel que crece: toma el alto que dejan las otras
 * dos y desplaza su lista por dentro (`min-h-0` en la cadena flex, o el
 * contenido empujaría el panel fuera de la pantalla).
 */
export const HistoryDock = memo(function HistoryDock(props: HistoryFeedProps) {
  const [open, setOpen] = useState(true)
  const total = props.history.length

  return (
    <Panel className="pointer-events-auto flex min-h-0 w-full flex-col overflow-hidden p-1.5">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls="history-dock-body"
        className={cn(
          'flex w-full shrink-0 items-center gap-2 rounded-control px-1.5 py-1 text-left transition-colors',
          'hover:bg-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent',
        )}
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
          className={cn(
            'size-3.5 shrink-0 text-ink-faint transition-transform duration-300',
            !open && '-rotate-90',
          )}
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
        <span className="min-w-0 flex-1 truncate text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
          Historial · 24 h
        </span>
        {props.ready && (
          <span
            aria-label={`${total} en las últimas 24 horas`}
            className="count rounded-full bg-sunken px-1.5 text-[10px] font-semibold text-ink-muted"
          >
            {total}
          </span>
        )}
      </button>

      {open && (
        <div
          id="history-dock-body"
          className="mt-0.5 min-h-0 overflow-y-auto overscroll-contain"
        >
          <HistoryFeed {...props} />
        </div>
      )}
    </Panel>
  )
})
