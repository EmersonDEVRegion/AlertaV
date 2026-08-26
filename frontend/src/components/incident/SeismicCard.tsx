import { useEffect } from 'react'
import type { SeismicEvent } from '@/api/seismicTypes'
import { MAGNITUDE, bandOf } from '@/domain/seismicSymbology'
import { formatDateTime, formatRelative } from '@/lib/format'

/**
 * Ficha de un sismo.
 *
 * Deliberadamente distinta de `IncidentSheet`: más corta, sin barras de
 * confianza y sin fuentes. Un sismo es un dato medido por una sola red, no un
 * hecho reconstruido a partir de señales, así que no tiene nada que auditar.
 */
export function SeismicCard({
  event,
  onClose,
}: {
  event: SeismicEvent
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const style = MAGNITUDE[bandOf(event)]
  const preliminary = event.review_status === 'automatic'

  return (
    <section
      role="dialog"
      aria-label={`Sismo ${event.usgs_id}`}
      className="pointer-events-auto fixed inset-x-0 bottom-0 z-20 rounded-t-2xl bg-raised
        p-4 pb-[max(1rem,env(safe-area-inset-bottom))]
        shadow-[var(--shadow-raised)]
        md:inset-y-0 md:left-auto md:right-0 md:w-[26rem] md:rounded-none md:rounded-l-2xl
 "
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="mt-1 size-4 shrink-0 rounded-full"
          style={{ border: `3px solid ${style.color}` }}
        />
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-bold text-ink">
            {event.magnitude !== null ? (
              <>Sismo magnitud {event.magnitude.toFixed(1)}</>
            ) : (
              <>Sismo sin magnitud calculada</>
            )}
            {event.mag_type && (
              <span className="ml-1.5 text-xs font-normal text-ink-muted">
                ({event.mag_type})
              </span>
            )}
          </h2>
          <p className="mt-0.5 text-xs text-ink-muted">
            {formatDateTime(event.timestamp)} · {formatRelative(event.timestamp)}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Cerrar ficha del sismo"
          className="-mr-1 -mt-1 grid size-9 shrink-0 place-items-center rounded-full text-ink-faint hover:bg-sunken hover:text-ink-muted"
        >
          <span aria-hidden className="text-lg leading-none">✕</span>
        </button>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${style.chip}`}>
          {style.label} · {style.range}
        </span>
        {preliminary && (
          <span className="rounded-full bg-sunken px-2.5 py-1 text-xs font-medium text-ink-muted ring-1 ring-line">
            Solución preliminar
          </span>
        )}
        {event.tsunami && (
          <span className="rounded-full bg-info-bg px-2.5 py-1 text-xs font-medium text-info-ink ring-1 ring-info-line">
            Evaluación de tsunami
          </span>
        )}
      </div>

      <dl className="mt-4 space-y-1.5 text-sm">
        <div className="flex justify-between gap-3">
          <dt className="text-ink-muted">Profundidad</dt>
          <dd className="text-ink">
            {event.depth_km !== null ? `${event.depth_km.toFixed(1)} km` : 'sin dato'}
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-ink-muted">Ubicación</dt>
          <dd className="max-w-[60%] text-right text-ink">
            {event.commune ?? event.place ?? 'sin referencia'}
          </dd>
        </div>
        {event.felt_reports !== null && event.felt_reports > 0 && (
          <div className="flex justify-between gap-3">
            <dt className="text-ink-muted">Lo sintieron</dt>
            <dd className="text-ink">{event.felt_reports} personas</dd>
          </div>
        )}
      </dl>

      {preliminary && (
        <p className="mt-3 callout callout-warn">
          Solución automática del USGS, sin revisar por un sismólogo. La magnitud
          y la profundidad pueden corregirse en las próximas horas.
        </p>
      )}

      <p className="mt-3 rounded-control bg-sunken px-2.5 py-2 text-[11px] leading-snug text-ink-muted ring-1 ring-line">
        Un sismo no es una emergencia declarada. Puede ser causa de incendios,
        derrumbes o tsunami, pero por sí solo no implica que haya un siniestro en
        el epicentro.
        {event.tsunami && ' La bandera de tsunami es del USGS, no una alerta de SENAPRED.'}
      </p>

      {event.usgs_url && (
        <a
          href={event.usgs_url}
          target="_blank"
          rel="noreferrer"
          className="mt-3 inline-block text-xs font-medium text-info-ink underline"
        >
          Ver en el catálogo del USGS
        </a>
      )}
    </section>
  )
}
