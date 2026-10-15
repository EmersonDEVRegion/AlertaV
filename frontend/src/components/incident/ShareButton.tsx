import { useEffect, useState } from 'react'
import { shareIncident, type ShareOutcome } from '@/lib/share'

interface ShareButtonProps {
  code: string
  title: string
  text?: string
}

const FEEDBACK: Partial<Record<ShareOutcome, string>> = {
  copied: 'Enlace copiado',
  failed: 'No se pudo copiar',
}

/** Botón «Compartir» de la ficha: enlace con vista previa para WhatsApp. */
export function ShareButton({ code, title, text }: ShareButtonProps) {
  const [outcome, setOutcome] = useState<ShareOutcome | null>(null)

  useEffect(() => {
    if (!outcome) return
    const t = window.setTimeout(() => setOutcome(null), 2500)
    return () => window.clearTimeout(t)
  }, [outcome])

  const feedback = outcome ? FEEDBACK[outcome] : undefined

  return (
    <div className="relative shrink-0">
      <button
        type="button"
        onClick={async () => setOutcome(await shareIncident({ code, title, text }))}
        aria-label="Compartir enlace del incidente"
        className="-mt-1 grid size-9 place-items-center rounded-full text-ink-faint transition hover:bg-sunken hover:text-ink-muted"
      >
        <svg viewBox="0 0 24 24" aria-hidden className="size-[18px]" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 3v12" />
          <path d="m7 8 5-5 5 5" />
          <path d="M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5" />
        </svg>
      </button>
      <span role="status" aria-live="polite" className="sr-only">
        {feedback ?? ''}
      </span>
      {feedback && (
        <span
          aria-hidden
          className="animate-rise absolute right-0 top-[calc(100%+0.25rem)] z-10 whitespace-nowrap rounded-control bg-ink px-2 py-1 text-[11px] font-semibold text-raised shadow"
        >
          {feedback}
        </span>
      )}
    </div>
  )
}
