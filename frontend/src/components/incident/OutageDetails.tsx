import type { OutageDetail } from '@/api/types'
import { providerStyle } from '@/domain/powerSymbology'
import { formatDateTime, formatRelative } from '@/lib/format'

/**
 * Bloque de detalle de un corte de suministro.
 *
 * Cada campo puede faltar por separado, y no es un caso raro: los feeds de las
 * distribuidoras publican el corte apenas lo detectan, antes de contar clientes
 * o de comprometer una hora de reposición. Por eso cada línea se decide sola en
 * vez de haber un único "¿hay datos?" para todo el bloque.
 *
 * La regla es no rellenar huecos con ceros ni con texto vacío: `0 clientes
 * afectados` afirma que no hay ninguno, que es exactamente lo contrario de
 * "todavía no lo sabemos".
 */

function formatClients(value: number): string {
  // Miles con punto, como se escribe en Chile.
  return value.toLocaleString('es-CL')
}

/** ¿La hora comprometida ya pasó? Entonces dejó de ser una estimación útil. */
function isOverdue(iso: string): boolean {
  const target = new Date(iso).getTime()
  return Number.isFinite(target) && target < Date.now()
}

export function OutageDetails({ outage }: { outage: OutageDetail }) {
  const style = providerStyle(outage.provider)

  const clients = outage.affected_clients
  const restoration = outage.estimated_restoration
  // Una fecha inválida es tan inútil como una ausente, y `formatDateTime`
  // devolvería "Invalid Date" en pantalla.
  const restorationValid =
    typeof restoration === 'string' &&
    restoration.length > 0 &&
    !Number.isNaN(new Date(restoration).getTime())

  return (
    <>
      <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-ink-muted">
        Corte de suministro
      </h3>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span
          className={`rounded-full px-2.5 py-1 text-xs font-semibold ${style.chip}`}
        >
          {style.label}
        </span>
        {outage.outage_count > 1 && (
          <span className="rounded-full bg-sunken px-2.5 py-1 text-xs font-medium text-ink-muted ring-1 ring-line">
            {outage.outage_count} cortes agrupados
          </span>
        )}
      </div>

      <dl className="mt-3 space-y-1.5 text-sm">
        {/* Clientes afectados: se oculta si la empresa no lo informó. */}
        {typeof clients === 'number' && (
          <div className="flex justify-between gap-3">
            <dt className="text-ink-muted">Clientes afectados</dt>
            <dd className="font-medium tabular-nums text-ink">
              {formatClients(clients)}
            </dd>
          </div>
        )}

        {/* Reposición estimada: se oculta si falta o si no es una fecha real. */}
        {restorationValid && (
          <div className="flex justify-between gap-3">
            <dt className="text-ink-muted">Reposición estimada</dt>
            <dd className="text-right text-ink">
              {formatDateTime(restoration)}
              <span
                className={
                  'ml-1.5 text-xs ' +
                  (isOverdue(restoration)
                    ? 'text-warn-ink'
                    : 'text-ink-muted')
                }
              >
                ({formatRelative(restoration)})
              </span>
            </dd>
          </div>
        )}

        {outage.sector && (
          <div className="flex justify-between gap-3">
            <dt className="text-ink-muted">Sector</dt>
            <dd className="max-w-[60%] text-right text-ink">
              {outage.sector}
            </dd>
          </div>
        )}
      </dl>

      {restorationValid && isOverdue(restoration) && (
        <p className="mt-2 callout callout-warn">
          La hora comprometida por {style.label} ya pasó y el corte sigue
          publicado. La estimación puede haberse actualizado en su sistema.
        </p>
      )}

      {clients === null && !restorationValid && (
        <p className="mt-2 text-[11px] leading-snug text-ink-muted">
          {style.label} publicó el corte sin detallar clientes afectados ni hora
          de reposición.
        </p>
      )}
    </>
  )
}
