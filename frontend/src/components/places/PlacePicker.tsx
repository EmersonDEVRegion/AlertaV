import type { RefObject } from 'react'
import type { MapRef } from 'react-map-gl/maplibre'
import { Button } from '@/components/ui/primitives'
import { addPlace, stopPicking, usePicking } from '@/lib/placesStore'
import { setSheetSnap } from '@/lib/sheetStore'
import { PlaceGlyph } from './SavedPlaces'

/**
 * «Elegir en el mapa»: una mira fija al centro y el mapa se mueve debajo.
 *
 * Mover el mapa bajo una mira es lo que hacen las apps de transporte al fijar
 * un punto de retiro: con una mano, sin tener que acertarle con el dedo a un
 * pin de 20 px. Lee el modo del store: `App` no se entera.
 */
export function PlacePicker({ mapRef }: { mapRef: RefObject<MapRef | null> }) {
  const name = usePicking()
  if (!name) return null

  const save = () => {
    const center = mapRef.current?.getCenter()
    if (center) addPlace(name, center.lat, center.lng)
    stopPicking()
    setSheetSnap('half')
  }

  return (
    <>
      {/* La mira: la punta del alfiler marca el centro exacto del mapa. */}
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-full text-accent drop-shadow-[0_2px_4px_rgb(0_0_0/0.35)]"
      >
        <svg viewBox="0 0 24 24" className="size-9" fill="currentColor" stroke="white" strokeWidth={1.2}>
          <path d="M12 23s-7.5-6.6-7.5-12.3a7.5 7.5 0 0 1 15 0C19.5 16.4 12 23 12 23Z" />
          <circle cx="12" cy="10.5" r="2.6" fill="white" stroke="none" />
        </svg>
      </div>

      <div
        role="dialog"
        aria-label={`Ubicar ${name} en el mapa`}
        className="animate-rise absolute inset-x-3 top-3 z-30 mx-auto flex max-w-md items-center gap-2.5 rounded-surface bg-raised p-2.5 shadow-[var(--shadow-raised)] ring-1 ring-line md:left-[23.5rem] md:right-auto md:w-[26rem]"
      >
        <span
          aria-hidden
          className="grid size-8 shrink-0 place-items-center rounded-control bg-accent-soft text-accent"
        >
          <PlaceGlyph name={name} className="size-4" />
        </span>
        <p className="min-w-0 flex-1 text-[11px] leading-snug text-ink-muted">
          Mueve el mapa hasta dejar <span className="font-semibold text-ink">{name}</span> bajo
          el alfiler.
        </p>
        <Button variant="ghost" size="sm" onClick={stopPicking}>
          Cancelar
        </Button>
        <button
          type="button"
          onClick={save}
          className="inline-flex h-7 shrink-0 items-center rounded-control bg-accent px-2.5 text-xs font-semibold text-accent-ink transition-[filter,scale] duration-150 hover:brightness-110 active:scale-[0.97]"
        >
          Guardar aquí
        </button>
      </div>
    </>
  )
}
