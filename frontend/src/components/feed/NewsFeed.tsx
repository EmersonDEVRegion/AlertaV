import { memo, useId, useMemo, useState } from 'react'
import type { NewsFeedItem } from '@/api/newsFeedTypes'
import { GlyphIcon } from '@/components/ui/GlyphIcon'
import { SectionToggle } from '@/components/ui/SectionToggle'
import { useSectionOpen } from '@/lib/sectionStore'
import { communeLabel, sameCommune } from '@/domain/displayWindow'
import { iconFor } from '@/domain/emergencyIcons'
import { useNewsFeed, type NewsFeedState } from '@/hooks/useNewsFeed'
import { RELATIVE_TIME_TICK_MS, useNow } from '@/hooks/useNow'
import { cn } from '@/lib/cn'
import { useExploreArea } from '@/lib/exploreStore'
import { formatRelativeShort } from '@/lib/format'

/**
 * Noticias de la prensa local, últimas 24 h. **No van al mapa.**
 *
 * Una nota llega con horas de atraso: en el mapa se mostraría como presente
 * algo que ya pasó. Por eso la prensa no entra al motor (`CORRELATION_PRENSA`) y se lee acá, con la hora de
 * publicación a la vista. Es información, no una alerta: por eso no tiene
 * color de emergencia ni se puede tocar para volar al mapa.
 *
 * Con un área de comuna activa (buscador o «Mis lugares»), sólo las notas de
 * esa comuna; con un lugar guardado no se filtra, porque una nota no tiene
 * punto con qué medir la distancia.
 */

/** Cuántas notas se ven antes de «Ver todas». */
export const NEWS_PREVIEW = 5

const NewsItem = memo(function NewsItem({ item, now }: { item: NewsFeedItem; now: number }) {
  const when = formatRelativeShort(item.publicada_en, now)
  const meta = [item.medio, communeLabel(item.comuna)].filter(Boolean).join(' · ')
  const body = (
    <>
      <GlyphIcon
        id={iconFor(item.tipo)}
        className="mt-0.5 size-3.5 shrink-0 opacity-60"
        color="currentColor"
      />
      <span className="min-w-0 flex-1">
        <span className="line-clamp-2 text-[11.5px] font-medium text-ink">{item.titular}</span>
        <span className="mt-0.5 flex items-baseline gap-1.5 text-[10px] text-ink-muted">
          <span className="min-w-0 flex-1 truncate">{meta || 'Prensa'}</span>
          <time
            dateTime={item.publicada_en}
            className="shrink-0 text-ink-faint"
            title={
              item.hora_aproximada
                ? 'El medio no publicó la hora: es aproximada.'
                : 'Hora de publicación de la nota.'
            }
          >
            {item.hora_aproximada ? `≈ ${when}` : when}
          </time>
        </span>
      </span>
    </>
  )
  const className =
    'flex w-full items-start gap-2 rounded-control px-1.5 py-1.5 text-left text-ink-muted transition-colors'
  return (
    <li>
      {item.url ? (
        <a
          href={item.url}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(className, 'hover:bg-hover')}
        >
          {body}
        </a>
      ) : (
        <div className={className}>{body}</div>
      )}
    </li>
  )
})

export function NewsFeedView({
  status,
  items,
  defaultOpen = true,
}: NewsFeedState & {
  /**
   * Abierta o plegada la primera vez. En la pestaña Historial va plegada:
   * está arriba de todo, y abierta empujaría el historial fuera de la hoja a
   * media altura. Después manda lo que la persona elija.
   */
  defaultOpen?: boolean
}) {
  const now = useNow(RELATIVE_TIME_TICK_MS)
  const area = useExploreArea()
  const bodyId = useId()
  const [open, toggle] = useSectionOpen('noticias', defaultOpen)
  const [all, setAll] = useState(false)

  const shown = useMemo(
    () =>
      area?.kind === 'commune'
        ? items.filter((item) => sameCommune(item.comuna, area.name))
        : items,
    [items, area],
  )

  // Sin la ruta en el backend no se muestra nada: no es un corte.
  if (status === 'unavailable') return null

  const visible = all ? shown : shown.slice(0, NEWS_PREVIEW)
  const hidden = shown.length - visible.length

  return (
    <section aria-label="Noticias" className="rounded-control bg-sunken p-1.5">
      <SectionToggle
        label="Noticias · 24 h"
        {...(status === 'ready' ? { count: shown.length } : {})}
        open={open}
        onToggle={toggle}
        controls={bodyId}
        // Plegada, el titular más reciente: se sabe si hay algo nuevo sin abrir.
        preview={shown[0]?.titular}
      />

      {open && (
        <div id={bodyId} className="mt-0.5">
          <p className="px-1.5 pb-1 text-[10px] leading-snug text-ink-faint">
            De medios locales. Llegan con atraso, así que no se marcan en el mapa.
          </p>
          {status === 'loading' && <div className="shimmer mx-1 h-8 rounded-control" aria-busy />}
          {status === 'error' && (
            <p className="px-1.5 py-2 text-[10.5px] text-ink-muted">
              No se pudieron cargar las noticias.
            </p>
          )}
          {status === 'blind' && (
            <p className="callout callout-warn m-1 text-[10.5px]">
              Sin noticias, pero la lectura de los medios no está respondiendo.
            </p>
          )}
          {(status === 'empty' || (status === 'ready' && shown.length === 0)) && (
            <p className="px-1.5 py-2 text-[10.5px] text-ink-muted">
              {area?.kind === 'commune'
                ? 'Sin noticias de emergencias en esta comuna en las últimas 24 h.'
                : 'Sin noticias de emergencias en las últimas 24 h.'}
            </p>
          )}
          {visible.length > 0 && (
            <ul>
              {visible.map((item) => (
                <NewsItem key={item.id} item={item} now={now} />
              ))}
            </ul>
          )}
          {hidden > 0 && (
            <button
              type="button"
              onClick={() => setAll(true)}
              className="mt-0.5 w-full rounded-control px-1.5 py-1 text-left text-[10.5px] font-medium text-accent hover:bg-hover"
            >
              Ver {hidden} más
            </button>
          )}
        </div>
      )}
    </section>
  )
}

/** El feed con su consulta. Lo monta la pestaña Historial. */
export const NewsFeed = memo(function NewsFeed({ defaultOpen = true }: { defaultOpen?: boolean }) {
  const state = useNewsFeed()
  return <NewsFeedView {...state} defaultOpen={defaultOpen} />
})
