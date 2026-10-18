import { useMemo } from 'react'
import { TRANSITO_SUBTIPOS, type TransitoSubtipo } from '@/api/transito'
import { TRANSITO_ETIQUETA, TRANSITO_PALETTE } from '@/domain/transitoSymbology'
import { useNow } from '@/hooks/useNow'
import type { Theme } from '@/hooks/useTheme'
import { useTransito } from '@/hooks/useTransito'
import { cn } from '@/lib/cn'
import { toggleTransitoSubtipo, useTransitoState } from '@/lib/transitoStore'

/**
 * La mitad «en vivo» de la ficha de «Tránsito» en Capas: un chip por subtipo
 * con su color y cuántos hay, que lo muestra u oculta en el mapa.
 *
 * Se lee sola (store y consulta propios) para que el resto de la ficha siga
 * siendo un componente de props, como las otras capas de referencia.
 */
export function TransitoLeyenda({ theme }: { theme: Theme }) {
  const now = useNow(60_000)
  const { status, data } = useTransito(now)
  const { ocultos } = useTransitoState()

  const cuantos = useMemo(() => {
    const cuenta = Object.fromEntries(TRANSITO_SUBTIPOS.map((s) => [s, 0])) as Record<
      TransitoSubtipo,
      number
    >
    for (const f of data.features) cuenta[f.properties.subtipo] += 1
    return cuenta
  }, [data])

  if (status === 'unavailable') return null

  return (
    <div>
      <p className="text-[10px] font-semibold text-ink-muted">En vivo · @TTIValparaiso (MTT)</p>
      <p className="text-[9.5px] leading-tight text-ink-faint">
        {status === 'loading'
          ? 'Consultando…'
          : status === 'error'
            ? 'No se pudo cargar'
            : data.features.length === 0
              ? 'Sin avisos de tránsito vigentes.'
              : 'Toca un tipo para ocultarlo del mapa.'}
      </p>
      <div role="group" aria-label="Tipos de aviso de tránsito" className="mt-1 flex flex-wrap gap-1">
        {TRANSITO_SUBTIPOS.map((subtipo) => {
          const visible = !ocultos.includes(subtipo)
          return (
            <button
              key={subtipo}
              type="button"
              aria-pressed={visible}
              onClick={() => toggleTransitoSubtipo(subtipo)}
              className={cn(
                'flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-current/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent',
                visible ? 'text-ink' : 'text-ink-faint line-through opacity-70',
              )}
            >
              <span
                aria-hidden
                className="h-1 w-3 shrink-0 rounded-full"
                style={{ backgroundColor: TRANSITO_PALETTE[theme][subtipo] }}
              />
              {TRANSITO_ETIQUETA[subtipo]}
              {cuantos[subtipo] > 0 && (
                <span className="tabular-nums text-ink-faint">{cuantos[subtipo]}</span>
              )}
            </button>
          )
        })}
      </div>
      <p className="mt-1.5 text-[9.5px] leading-tight text-ink-faint">
        El tramo se dibuja sobre la calle de OpenStreetMap entre las esquinas que nombra el MTT. El
        verde confirma que una vía volvió a la normalidad y dura poco.
      </p>
    </div>
  )
}
