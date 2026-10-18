import { memo, useId, useState, type CSSProperties } from 'react'
import type { TransitoFeature } from '@/api/transito'
import { SectionToggle } from '@/components/ui/SectionToggle'
import { TRANSITO_PALETTE } from '@/domain/transitoSymbology'
import { useNow } from '@/hooks/useNow'
import { useTransito, type TransitoData } from '@/hooks/useTransito'
import { cn } from '@/lib/cn'
import { formatRelativeShort } from '@/lib/format'
import { setSheetSnap } from '@/lib/sheetStore'
import { useSectionOpen } from '@/lib/sectionStore'
import { showTransitoOnMap } from '@/lib/transitoStore'

/**
 * El tránsito del MTT en «Ahora»: «Marcha por Av. Pedro Montt», «Congestión en
 * Viana, de Sucre a Von Schroeders», «Tránsito normal en Av. España».
 *
 * Plegada por defecto: un día hábil son varias líneas y no es una emergencia.
 * Sin nada vigente no se dibuja: la calle despejada no necesita una tarjeta.
 *
 * «Ver en el mapa» enciende la capa «Tránsito», resalta el tramo y lo encuadra;
 * en el teléfono baja la hoja para que se vea.
 */

const TICK_MS = 60_000
/** Cuántas se ven antes de «Ver todas»: la lista no debe empujar a los incidentes. */
const VISIBLES = 5

function verEnElMapa(feature: TransitoFeature): void {
  showTransitoOnMap(feature.properties.public_id, feature.properties.subtipo)
  setSheetSnap('peek')
}

function detalle(f: TransitoFeature, now: number): string {
  const p = f.properties
  return [p.comuna, p.sentido ? `sentido ${p.sentido}` : null, formatRelativeShort(p.timestamp, now)]
    .filter(Boolean)
    .join(' · ')
}

const TransitoItem = memo(function TransitoItem({
  feature,
  now,
}: {
  feature: TransitoFeature
  now: number
}) {
  const [open, setOpen] = useState(false)
  const bodyId = useId()
  const p = feature.properties
  const enMapa = feature.geometry !== null

  return (
    <li
      className="overflow-hidden rounded-control border-l-4 border-l-[color:var(--tl)] bg-raised dark:border-l-[color:var(--td)]"
      // El color del subtipo en los dos temas, el mismo que pinta el mapa.
      style={
        {
          '--tl': TRANSITO_PALETTE.light[p.subtipo],
          '--td': TRANSITO_PALETTE.dark[p.subtipo],
        } as CSSProperties
      }
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={bodyId}
        className="block w-full px-2.5 py-1.5 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      >
        <span className="block text-[12px] font-semibold leading-tight text-ink">{p.titulo}</span>
        <span className="block text-[10.5px] text-ink-muted">{detalle(feature, now)}</span>
      </button>
      {open && (
        <div id={bodyId} className="space-y-1.5 px-2.5 pb-2 text-[11px] leading-snug">
          {p.text && <p className="text-ink-muted">{p.text}</p>}
          <div className="flex flex-wrap items-center gap-2">
            {enMapa ? (
              <button
                type="button"
                onClick={() => verEnElMapa(feature)}
                className="rounded-full px-2.5 py-1 text-[10.5px] font-semibold text-ink ring-1 ring-current/30 hover:bg-current/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
              >
                Ver en el mapa
              </button>
            ) : (
              <span className="text-[10px] text-ink-faint">No pudimos ubicarlo en el mapa.</span>
            )}
            {p.url && (
              <a
                href={p.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[10.5px] font-semibold text-ink-muted underline underline-offset-2"
              >
                Ver el aviso del MTT
              </a>
            )}
          </div>
        </div>
      )}
    </li>
  )
})

export function TransitoAhoraView({
  status,
  items,
  now,
}: Pick<TransitoData, 'status' | 'items'> & { now: number }) {
  const bodyId = useId()
  const [open, toggle] = useSectionOpen('transito-ahora', false)
  const [todas, setTodas] = useState(false)

  if (status !== 'ready' || items.length === 0) return null
  const visibles = todas ? items : items.slice(0, VISIBLES)
  const primero = items[0]!

  return (
    <section aria-label="Tránsito" className="rounded-control bg-sunken p-1.5">
      <SectionToggle
        label="Tránsito · MTT"
        count={items.length}
        open={open}
        onToggle={toggle}
        controls={bodyId}
        preview={primero.properties.titulo}
      />
      {open && (
        <div id={bodyId}>
          <ul className="mt-1 space-y-1">
            {visibles.map((f) => (
              <TransitoItem key={f.properties.public_id} feature={f} now={now} />
            ))}
          </ul>
          {items.length > VISIBLES && (
            <button
              type="button"
              onClick={() => setTodas((v) => !v)}
              className={cn(
                'mt-1 w-full rounded-control py-1 text-[10.5px] font-semibold text-ink-muted hover:bg-raised',
              )}
            >
              {todas ? 'Ver menos' : `Ver todas (${items.length})`}
            </button>
          )}
          <p className="mt-1 px-1 text-[9.5px] leading-tight text-ink-faint">
            Lo que informa @TTIValparaiso (MTT). Un taco deja de mostrarse a la hora y media si
            nadie avisa antes que se normalizó.
          </p>
        </div>
      )}
    </section>
  )
}

/** La sección con su consulta. La monta la pestaña «Ahora». */
export const TransitoAhora = memo(function TransitoAhora() {
  const now = useNow(TICK_MS)
  const { status, items } = useTransito(now)
  return <TransitoAhoraView status={status} items={items} now={now} />
})
