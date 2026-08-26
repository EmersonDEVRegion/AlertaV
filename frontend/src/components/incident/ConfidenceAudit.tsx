import type { ConfidenceBreakdown } from '@/api/types'
import { sourceLabel } from '@/domain/labels'
import { formatPercent } from '@/lib/format'

const CEILING_EXPLANATION: Record<string, string> = {
  confirming_source:
    'Una fuente que fue al lugar confirmo el hecho, así que la confianza sube a 100 %.',
  unconfirmed_ceiling:
    'Sin confirmación en terreno la confianza queda topada en 95 %: ninguna combinacion de indicios alcanza la certeza.',
}

/**
 * De dónde salió el número.
 *
 * El backend guarda `confidence_breakdown` justamente para que esto se pueda
 * mostrar sin acceso a la base. Exponerlo es lo que separa un indicador de
 * confianza de un número que hay que creer porque si.
 */
export function ConfidenceAudit({ breakdown }: { breakdown: ConfidenceBreakdown }) {
  const bySource = breakdown.by_source ?? {}
  const entries = Object.entries(bySource)
  if (entries.length === 0) return null

  const ceiling = breakdown.ceiling_applied
    ? CEILING_EXPLANATION[breakdown.ceiling_applied]
    : null

  return (
    <details className="rounded-surface bg-sunken ring-1 ring-line">
      <summary className="cursor-pointer list-none px-3 py-2.5 text-sm font-semibold text-ink-muted [&::-webkit-details-marker]:hidden">
        Cómo se calculó esta confianza
        <span aria-hidden className="float-right text-ink-faint">
          ▾
        </span>
      </summary>

      <div className="border-t border-line px-3 py-3">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-ink-muted">
              <th className="pb-1 font-medium">Fuente</th>
              <th className="pb-1 text-center font-medium">Señales</th>
              <th className="pb-1 text-right font-medium">Aporte</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {entries
              .sort((a, b) => (b[1]?.contribution ?? 0) - (a[1]?.contribution ?? 0))
              .map(([source, detail]) => (
                <tr key={source}>
                  <td className="py-1.5 text-ink">
                    {sourceLabel(source)}
                    {detail?.confirming && (
                      <span className="ml-1 text-danger-ink" title="Confirma en terreno">
                        ✓
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 text-center tabular-nums text-ink-muted">
                    {detail?.signals ?? 0}
                  </td>
                  <td className="py-1.5 text-right tabular-nums font-medium text-ink">
                    {formatPercent(detail?.contribution ?? 0)}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>

        {ceiling && (
          <p className="mt-2.5 callout callout-warn">
            {ceiling}
          </p>
        )}

        {breakdown.policy_version && (
          <p className="mt-2 text-[10px] text-ink-faint">
            Política de confianza v{breakdown.policy_version}
          </p>
        )}
      </div>
    </details>
  )
}
