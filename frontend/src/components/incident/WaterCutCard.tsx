import { memo, useEffect } from 'react'
import type { WaterCut } from '@/api/waterCutTypes'
import { GlyphIcon } from '@/components/ui/GlyphIcon'
import { WATER_ICON } from '@/domain/emergencyIcons'
import { WATER } from '@/domain/waterSymbology'
import { RELATIVE_TIME_TICK_MS, useNow } from '@/hooks/useNow'
import { formatDateTime, formatRelative } from '@/lib/format'

/**
 * Tarjeta de un corte de agua de Esval.
 *
 * Con la forma de `SeismicCard` —hoja inferior en teléfono, tarjeta a la derecha
 * en escritorio— y no la de `IncidentSheet`: un corte de agua no es un hecho
 * reconstruido a partir de señales, así que no hay confianza que auditar ni
 * fuentes que listar. Lo dice la sanitaria, que es la autoridad sobre su red.
 *
 * Cada fila se decide sola, como en `OutageDetails`: Esval publica el corte con
 * los campos que tiene, y un hueco no se rellena. En particular **no hay fila de
 * clientes afectados**: Esval no publica ese dato, y un «0 clientes» afirmaría
 * justo lo contrario de «no se sabe».
 */

/** ¿La hora referencial ya pasó y el corte sigue publicado? */
function isOverdue(iso: string | null, now: number): iso is string {
  if (iso === null) return false
  const target = Date.parse(iso)
  return Number.isFinite(target) && target < now
}

/** «emergencia» → «Emergencia». Lo que no se reconoce se muestra tal cual. */
function kindLabel(cut: WaterCut): string | null {
  if (cut.programado === false) return 'Emergencia'
  if (cut.programado === true) return 'Programado'
  return cut.tipo ? cut.tipo.charAt(0).toUpperCase() + cut.tipo.slice(1) : null
}

export const WaterCutCard = memo(function WaterCutCard({
  cut,
  onClose,
}: {
  cut: WaterCut
  onClose: () => void
}) {
  // La reposición «vence» sola con el paso del tiempo, sin que llegue nada nuevo.
  const now = useNow(RELATIVE_TIME_TICK_MS)

  useEffect(() => {
    // Fase de burbuja, como las otras fichas: el modal de reporte escucha en
    // captura y detiene el Escape, así que un Escape cierra sólo el modal.
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const kind = kindLabel(cut)
  const where = cut.calles ?? cut.sector
  const overdue = isOverdue(cut.fin, now)

  return (
    <section
      role="dialog"
      aria-label={`Corte de agua de Esval${cut.comuna ? ` en ${cut.comuna}` : ''}`}
      className="pointer-events-auto fixed inset-x-0 bottom-0 z-20 max-h-[70dvh] overflow-y-auto overscroll-contain rounded-t-2xl bg-raised
        p-4 pb-[max(1rem,env(safe-area-inset-bottom))]
        shadow-[var(--shadow-raised)]
        md:inset-y-0 md:left-auto md:right-0 md:max-h-none md:w-[26rem] md:rounded-none md:rounded-l-2xl
 "
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full"
          style={{ backgroundColor: WATER.color }}
        >
          <GlyphIcon id={WATER_ICON} className="size-4" color={WATER.onColor} />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-bold text-ink">Corte de agua — Esval</h2>
          <p className="mt-0.5 line-clamp-2 text-xs text-ink-muted">
            {cut.comuna ?? 'Comuna sin informar'}
            {where && ` · ${where}`}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Cerrar ficha del corte de agua"
          className="-mr-1 -mt-1 grid size-9 shrink-0 place-items-center rounded-full text-ink-faint hover:bg-sunken hover:text-ink-muted"
        >
          <span aria-hidden className="text-lg leading-none">✕</span>
        </button>
      </div>

      {(kind || cut.suministro_alternativo !== null) && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {kind && (
            <span
              className={
                'rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ' +
                (cut.programado === false
                  ? 'bg-warn-bg text-warn-ink ring-warn-line'
                  : 'bg-sunken text-ink-muted ring-line')
              }
            >
              {kind}
            </span>
          )}
          {cut.suministro_alternativo === true && (
            <span className="rounded-full bg-info-bg px-2.5 py-1 text-xs font-medium text-info-ink ring-1 ring-info-line">
              Con suministro alternativo
            </span>
          )}
          {cut.suministro_alternativo === false && (
            <span className="rounded-full bg-sunken px-2.5 py-1 text-xs font-medium text-ink-muted ring-1 ring-line">
              Sin suministro alternativo
            </span>
          )}
        </div>
      )}

      <dl className="mt-4 space-y-1.5 text-sm">
        {cut.inicio && (
          <div className="flex justify-between gap-3">
            <dt className="text-ink-muted">Inicio</dt>
            <dd className="text-right text-ink">{formatDateTime(cut.inicio)}</dd>
          </div>
        )}
        {cut.fin && (
          <div className="flex justify-between gap-3">
            <dt className="text-ink-muted">Reposición estimada*</dt>
            <dd className="text-right text-ink">
              {formatDateTime(cut.fin)}
              <span className={'ml-1.5 text-xs ' + (overdue ? 'text-warn-ink' : 'text-ink-muted')}>
                ({formatRelative(cut.fin, now)})
              </span>
            </dd>
          </div>
        )}
        {cut.motivo && (
          <div className="flex justify-between gap-3">
            <dt className="text-ink-muted">Motivo</dt>
            <dd className="max-w-[65%] text-right text-ink">{cut.motivo}</dd>
          </div>
        )}
        {cut.sector && cut.sector !== where && (
          <div className="flex justify-between gap-3">
            <dt className="text-ink-muted">Sector</dt>
            <dd className="max-w-[65%] text-right text-ink">{cut.sector}</dd>
          </div>
        )}
      </dl>

      {overdue && (
        <p className="mt-3 callout callout-warn">
          La hora estimada por Esval ya pasó y el corte sigue publicado. Puede
          que la reposición se haya retrasado o que Esval todavía no lo retire.
        </p>
      )}

      {!cut.coordinates && (
        <p className="mt-3 callout callout-info">
          Esval no entregó la ubicación de este corte: aparece en la lista, pero
          no en el mapa.
        </p>
      )}

      <p className="mt-3 rounded-control bg-sunken px-2.5 py-2 text-[11px] leading-snug text-ink-muted ring-1 ring-line">
        {cut.fin && '* Hora referencial de Esval. '}
        Un corte de agua no es un siniestro: es información de servicio. Esval
        no publica cuántos clientes afecta cada corte.
        {cut.visto_en && ` Visto por AlertaV ${formatRelative(cut.visto_en, now)}.`}
      </p>

      {cut.url_mapa && (
        <a
          href={cut.url_mapa}
          target="_blank"
          rel="noreferrer"
          className="mt-3 inline-block text-xs font-medium text-info-ink underline"
        >
          Ver en el visor de Esval ↗
        </a>
      )}
    </section>
  )
})
