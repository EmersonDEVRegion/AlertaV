import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { COMUNAS } from '@/domain/comunas'
import { PlaceGlyph } from '@/components/places/SavedPlaces'
import type { ExploreArea } from '@/lib/exploreStore'
import { usePlaces } from '@/lib/placesStore'
import { cn } from '@/lib/cn'

/**
 * Buscador de la barra: las 36 comunas de la región y los lugares guardados.
 *
 * No es un geocodificador de direcciones a propósito: lo que la gente busca en
 * una emergencia es «¿qué pasa en Quilpué?», y eso se responde con la lista
 * local, sin red y sin mandarle a un tercero lo que se escribió. Elegir una
 * comuna encuadra el mapa en ella y acota el historial.
 *
 * En el teléfono es una lupa que se abre sobre la barra.
 */
interface Option {
  key: string
  label: string
  detail: string
  area: ExploreArea
  saved: boolean
}

function normalize(text: string): string {
  return text
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .trim()
}

const COMMUNE_OPTIONS: readonly (Option & { norm: string })[] = COMUNAS.map((c) => ({
  key: `comuna:${c.cut}`,
  label: c.nombre,
  detail: `Provincia de ${c.provincia}`,
  area: { kind: 'commune', name: c.nombre },
  saved: false,
  norm: normalize(c.nombre),
}))

const MAX_OPTIONS = 8

function SearchGlyph({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      aria-hidden
      className={className}
    >
      <circle cx="11" cy="11" r="6.5" />
      <path d="m20 20-4.2-4.2" />
    </svg>
  )
}

export function PlaceSearch({ onPick }: { onPick: (area: ExploreArea) => void }) {
  const places = usePlaces()
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [active, setActive] = useState(0)
  const root = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const listId = useId()

  const options = useMemo<Option[]>(() => {
    const q = normalize(query)
    const saved: Option[] = places
      .filter((p) => !q || normalize(p.name).includes(q))
      .map((p) => ({
        key: `lugar:${p.id}`,
        label: p.name,
        detail: 'Lugar guardado',
        area: { kind: 'place', id: p.id, name: p.name, lat: p.lat, lon: p.lon },
        saved: true,
      }))
    const communes = q
      ? COMMUNE_OPTIONS.filter((c) => c.norm.includes(q)).sort(
          (a, b) => Number(!a.norm.startsWith(q)) - Number(!b.norm.startsWith(q)),
        )
      : COMMUNE_OPTIONS
    return [...saved, ...communes].slice(0, q ? MAX_OPTIONS : MAX_OPTIONS + saved.length)
  }, [query, places])

  useEffect(() => {
    if (!open && !expanded) return
    const onPointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) {
        setOpen(false)
        setExpanded(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open, expanded])

  useEffect(() => {
    if (expanded) input.current?.focus()
  }, [expanded])

  const pick = (option: Option) => {
    onPick(option.area)
    setQuery('')
    setOpen(false)
    setExpanded(false)
    input.current?.blur()
  }

  const showList = open && options.length > 0
  const current = options[Math.min(active, options.length - 1)]

  return (
    <div ref={root} className="flex min-w-0 flex-1 justify-end sm:justify-center">
      <button
        type="button"
        onClick={() => setExpanded(true)}
        aria-label="Buscar comuna o lugar"
        className={cn(
          'grid size-8 shrink-0 place-items-center rounded-full bg-chrome-raised text-ink-on-chrome sm:hidden',
          'transition-[scale] duration-150 active:scale-[0.94]',
          expanded && 'invisible',
        )}
      >
        <SearchGlyph className="size-4" />
      </button>

      <div
        className={cn(
          'w-full',
          expanded
            ? 'absolute inset-x-0 bottom-0 top-[env(safe-area-inset-top)] z-10 flex items-center gap-2 bg-chrome px-3'
            : 'relative hidden max-w-sm sm:block',
        )}
      >
        <div className="relative flex-1">
          <SearchGlyph className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-white/45" />
          <input
            ref={input}
            type="search"
            role="combobox"
            aria-expanded={showList}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={showList && current ? `${listId}-${current.key}` : undefined}
            aria-label="Buscar comuna o lugar"
            placeholder="Buscar comuna o lugar"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value)
              setActive(0)
              setOpen(true)
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown') {
                event.preventDefault()
                setOpen(true)
                setActive((i) => Math.min(i + 1, options.length - 1))
              } else if (event.key === 'ArrowUp') {
                event.preventDefault()
                setActive((i) => Math.max(i - 1, 0))
              } else if (event.key === 'Enter' && showList && current) {
                event.preventDefault()
                pick(current)
              } else if (event.key === 'Escape') {
                setOpen(false)
                setExpanded(false)
              }
            }}
            className={cn(
              'h-8 w-full rounded-full bg-chrome-raised pl-8 pr-3 text-[12.5px] text-ink-on-chrome',
              'placeholder:text-white/40 focus:outline-none focus:ring-2 focus:ring-accent',
              '[&::-webkit-search-cancel-button]:hidden',
            )}
          />

          {showList && (
            <ul
              id={listId}
              role="listbox"
              aria-label="Comunas y lugares"
              className="animate-rise absolute inset-x-0 top-[calc(100%+0.375rem)] z-30 max-h-[min(22rem,60dvh)] overflow-y-auto overscroll-contain rounded-surface bg-raised p-1 shadow-[var(--shadow-raised)] ring-1 ring-line"
            >
              {options.map((option, index) => (
                <li
                  key={option.key}
                  id={`${listId}-${option.key}`}
                  role="option"
                  aria-selected={index === active}
                  onPointerDown={(event) => event.preventDefault()}
                  onClick={() => pick(option)}
                  onPointerEnter={() => setActive(index)}
                  className={cn(
                    'flex cursor-pointer items-center gap-2 rounded-control px-2 py-1.5',
                    index === active && 'bg-hover',
                  )}
                >
                  <span
                    aria-hidden
                    className={cn(
                      'grid size-6 shrink-0 place-items-center rounded-control',
                      option.saved ? 'bg-accent-soft text-accent' : 'bg-sunken text-ink-faint',
                    )}
                  >
                    <PlaceGlyph name={option.saved ? option.label : 'comuna'} className="size-3.5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-semibold text-ink">
                      {option.label}
                    </span>
                    <span className="block truncate text-[10px] text-ink-muted">
                      {option.detail}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {expanded && (
          <button
            type="button"
            onClick={() => {
              setExpanded(false)
              setOpen(false)
            }}
            className="shrink-0 text-xs font-semibold text-white/70"
          >
            Cancelar
          </button>
        )}
      </div>
    </div>
  )
}
