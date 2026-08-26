import type { EventSource } from '@/api/types'
import { sourceLabel } from '@/domain/labels'

/** Fuentes que un organismo confirmo yendo al lugar. Espejo de `confidence.RULES`. */
const CONFIRMING: ReadonlySet<string> = new Set(['conaf', 'bomberos'])

interface SourceChipsProps {
  sources: readonly EventSource[]
}

/**
 * Las fuentes que componen el incidente.
 *
 * Se distingue visualmente a quien confirmo en terreno de quien aporto un
 * indicio: "CONAF, FIRMS" y "FIRMS, ciudadano" son listas de dos elementos que
 * describen situaciones completamente distintas.
 */
export function SourceChips({ sources }: SourceChipsProps) {
  if (sources.length === 0) {
    return <p className="text-sm text-ink-muted">Sin fuentes registradas.</p>
  }

  return (
    <ul className="flex flex-wrap gap-1.5">
      {sources.map((source) => {
        const confirming = CONFIRMING.has(source)
        return (
          <li
            key={source}
            className={
              'rounded-full px-2.5 py-1 text-xs font-medium ring-1 ' +
              (confirming
                ? 'bg-danger-bg text-danger-ink ring-danger-line'
                : 'bg-sunken text-ink-muted ring-line')
            }
            title={
              confirming
                ? 'Fuente que confirma el hecho en terreno'
                : 'Fuente de corroboración: aporta indicios, no confirma'
            }
          >
            {sourceLabel(source)}
            {confirming && (
              <span aria-hidden className="ml-1 text-danger-ink">
                ✓
              </span>
            )}
          </li>
        )
      })}
    </ul>
  )
}
