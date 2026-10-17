import type { IncidentRelacion } from '@/api/types'
import { formatDistance } from '@/lib/format'
import { selectIncident } from '@/lib/selectionStore'

/**
 * Hechos de otras familias cerca y poco antes o después: un corte de luz a
 * 150 m que empezó 12 minutos después del choque.
 *
 * Dice cuándo y dónde, nunca «por»: que el auto haya botado el poste es
 * probable, pero ninguna fuente lo afirmó. Los vínculos los calcula el motor
 * (`correlation/relaciones.py`) y el servidor sólo los entrega con
 * `CORRELATION_RELACIONES=visible`; mientras están en sombra la lista llega
 * vacía y este bloque no se dibuja.
 */
export function RelatedNearby({ items }: { items: IncidentRelacion[] }) {
  if (items.length === 0) return null
  return (
    <section className="mt-5" aria-labelledby="relacionados-titulo">
      <h3
        id="relacionados-titulo"
        className="text-xs font-semibold uppercase tracking-wide text-ink-muted"
      >
        Cerca, a la misma hora
      </h3>
      <ul className="mt-2 space-y-2">
        {items.map((item) => {
          const texto = (
            <>
              <span className="font-semibold text-ink">{item.nombre}</span>
              <span className="text-ink-muted">
                {' · a '}
                {formatDistance(item.distancia_m)}
                {' · '}
                {cuando(item.desfase_min)}
              </span>
            </>
          )
          return (
            <li
              key={`${item.regla}-${item.code ?? item.tipo}-${item.distancia_m}`}
              className="rounded-control bg-raised p-2.5 text-xs ring-1 ring-line"
            >
              {item.code ? (
                <button
                  type="button"
                  onClick={() => selectIncident(item.code as string)}
                  className="w-full text-left underline decoration-line underline-offset-2
                    hover:decoration-ink-muted focus-visible:outline-none
                    focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {texto}
                </button>
              ) : (
                <p>{texto}</p>
              )}
            </li>
          )
        })}
      </ul>
      <p className="mt-1.5 text-[11px] text-ink-muted">
        Ocurrieron cerca y seguidos; no sabemos si uno causó el otro.
      </p>
    </section>
  )
}

/** «comenzó 12 min después», «comenzó 1 h 20 min antes», «casi al mismo tiempo». */
export function cuando(minutos: number): string {
  const total = Math.abs(Math.round(minutos))
  if (total < 2) return 'casi al mismo tiempo'
  const horas = Math.floor(total / 60)
  const resto = total % 60
  const lapso =
    horas === 0 ? `${resto} min` : resto === 0 ? `${horas} h` : `${horas} h ${resto} min`
  return `comenzó ${lapso} ${minutos > 0 ? 'después' : 'antes'}`
}
