import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Cuartel } from '@/api/cuarteles'
import type { Incident } from '@/api/types'
import { Button } from '@/components/ui/primitives'
import { useCuartelesData } from '@/hooks/useCuarteles'
import { useGeolocation } from '@/hooks/useGeolocation'
import { NUMERO_BOMBEROS, cuartelesCercanos, formatDistancia } from '@/domain/cuarteles'
import { distanceKm } from '@/lib/geo'
import { cn } from '@/lib/cn'
import { forgetPlaceArea, type ExploreArea } from '@/lib/exploreStore'
import {
  MAX_PLACES,
  NEAR_PLACE_KM,
  addPlace,
  removePlace,
  startPicking,
  usePlaces,
  type SavedPlace,
} from '@/lib/placesStore'
import { setSheetSnap } from '@/lib/sheetStore'

/**
 * «Mis lugares»: Casa, Trabajo y uno más, con lo que pasa cerca de cada uno.
 *
 * Responde la pregunta con la que casi todo el mundo abre la app —¿hay algo
 * cerca de mi casa?— sin buscar en el mapa. Cuenta sólo lo que está en curso
 * (en el mapa), a menos de `NEAR_PLACE_KM`, el mismo radio de los avisos.
 *
 * Tocar un lugar vuela hasta él y acota el historial a su alrededor.
 */
interface SavedPlacesProps {
  /** El historial de 24 h con las capas encendidas. */
  history: readonly Incident[]
  onMapCodes: ReadonlySet<string>
  onFocusArea: (area: ExploreArea) => void
  /** Vuela a un cuartel y enciende la capa. */
  onFocusCuartel: (lon: number, lat: number) => void
}

const NAME_CHOICES = ['Casa', 'Trabajo'] as const

function PlaceGlyph({ name, className }: { name: string; className?: string }) {
  const key = name.toLowerCase()
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
    >
      {key.startsWith('casa') ? (
        <path d="M3 10.5 12 3l9 7.5M5 9v11h5v-6h4v6h5V9" />
      ) : key.startsWith('trabajo') ? (
        <>
          <rect x="3" y="7" width="18" height="13" rx="2" />
          <path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M3 13h18" />
        </>
      ) : (
        <>
          <path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21Z" />
          <circle cx="12" cy="9.5" r="2.5" />
        </>
      )}
    </svg>
  )
}

function nearby(place: SavedPlace, live: readonly Incident[]): number {
  let n = 0
  for (const incident of live) {
    if (distanceKm(place.lat, place.lon, incident.lat, incident.lon) <= NEAR_PLACE_KM) n += 1
  }
  return n
}

/**
 * Los tres cuarteles más cercanos a un punto, plegados bajo una línea.
 *
 * Dice «en línea recta» siempre: no es el tiempo de respuesta ni la compañía
 * que va a despachar la central. El teléfono del cuartel NO se muestra a
 * propósito: en una emergencia se llama al 132, y llamar a un cuartel retrasa
 * el despacho.
 */
function CuartelesCerca({
  cuarteles,
  lat,
  lon,
  onFocusCuartel,
  idBase,
}: {
  cuarteles: readonly Cuartel[]
  lat: number
  lon: number
  onFocusCuartel: (lon: number, lat: number) => void
  idBase: string
}) {
  const [open, setOpen] = useState(false)
  const cercanos = useMemo(() => cuartelesCercanos(cuarteles, lat, lon, 3), [cuarteles, lat, lon])
  const primero = cercanos[0]
  if (!primero) return null
  const panelId = `${idBase}-cuarteles`
  return (
    <div className="pb-1 pl-[46px] pr-1.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={panelId}
        className="w-full truncate text-left text-[10.5px] leading-tight text-ink-muted underline-offset-2 hover:text-ink hover:underline"
      >
        Cuartel más cercano: {primero.cuartel.nombre} · {formatDistancia(primero.km)}
      </button>
      {open && (
        <div id={panelId} className="mt-1 space-y-0.5">
          <ul>
            {cercanos.map(({ cuartel, km }) => (
              <li key={cuartel.id} className="flex items-center gap-2">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[10.5px] font-medium text-ink">
                    {cuartel.nombre}
                  </span>
                  <span className="block truncate text-[9.5px] text-ink-faint">
                    {[cuartel.direccion, formatDistancia(km)].filter(Boolean).join(' · ')}
                  </span>
                </span>
                <button
                  type="button"
                  onClick={() => onFocusCuartel(cuartel.lon, cuartel.lat)}
                  className="shrink-0 text-[10px] font-semibold text-accent underline-offset-2 hover:underline"
                >
                  Ver en el mapa
                </button>
              </li>
            ))}
          </ul>
          <p className="text-[9.5px] leading-snug text-ink-faint">
            Distancia en línea recta. La central de Bomberos decide qué compañía va.
          </p>
        </div>
      )}
    </div>
  )
}

const PlaceRow = memo(function PlaceRow({
  place,
  count,
  onFocus,
  cuarteles,
  onFocusCuartel,
}: {
  place: SavedPlace
  count: number
  onFocus: (place: SavedPlace) => void
  cuarteles: readonly Cuartel[]
  onFocusCuartel: (lon: number, lat: number) => void
}) {
  const alert = count > 0
  return (
    <li>
    <div className="flex items-center gap-1">
      <button
        type="button"
        onClick={() => onFocus(place)}
        className="flex min-w-0 flex-1 items-center gap-2.5 rounded-control px-1.5 py-1.5 text-left transition-colors hover:bg-hover"
      >
        <span
          aria-hidden
          className={cn(
            'grid size-8 shrink-0 place-items-center rounded-control',
            alert ? 'bg-danger-bg text-danger-ink' : 'bg-sunken text-ink-muted',
          )}
        >
          <PlaceGlyph name={place.name} className="size-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-semibold text-ink">{place.name}</span>
          <span
            className={cn(
              'block truncate text-[10.5px] leading-tight',
              alert ? 'font-semibold text-danger-ink' : 'text-ink-muted',
            )}
          >
            {alert
              ? `${count} ${count === 1 ? 'emergencia en curso' : 'emergencias en curso'} a menos de ${NEAR_PLACE_KM} km`
              : 'Sin emergencias en curso cerca'}
          </span>
        </span>
      </button>
      <button
        type="button"
        onClick={() => {
          removePlace(place.id)
          forgetPlaceArea(place.id)
        }}
        aria-label={`Borrar ${place.name}`}
        title={`Borrar ${place.name}`}
        className="grid size-8 shrink-0 place-items-center rounded-control text-ink-faint transition-colors hover:bg-hover hover:text-ink"
      >
        <svg
          viewBox="0 0 24 24"
          aria-hidden
          className="size-3.5"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
        >
          <path d="M6 6l12 12M18 6 6 18" />
        </svg>
      </button>
    </div>
    <CuartelesCerca
      cuarteles={cuarteles}
      lat={place.lat}
      lon={place.lon}
      onFocusCuartel={onFocusCuartel}
      idBase={`lugar-${place.id}`}
    />
    </li>
  )
})

/** «Cuarteles cerca de mí»: con la ubicación del momento, sin guardarla. */
function CuartelesCercaDeMi({
  cuarteles,
  onFocusCuartel,
  onPedir,
}: {
  cuarteles: readonly Cuartel[]
  onFocusCuartel: (lon: number, lat: number) => void
  /** Pide la lista de cuarteles si todavía no se había pedido. */
  onPedir: () => void
}) {
  const geo = useGeolocation()
  if (geo.status === 'ready' && geo.coords) {
    if (cuarteles.length === 0) {
      return <p className="px-1.5 pt-1 text-[10.5px] text-ink-muted">Cargando cuarteles…</p>
    }
    return (
      <div className="pt-1">
        <p className="px-1.5 text-[10.5px] font-semibold text-ink">Cerca de tu ubicación</p>
        <CuartelesCercaAbierto
          cuarteles={cuarteles}
          lat={geo.coords.lat}
          lon={geo.coords.lon}
          onFocusCuartel={onFocusCuartel}
        />
      </div>
    )
  }
  return (
    <div className="px-1.5 pt-1">
      <button
        type="button"
        onClick={() => {
          onPedir()
          geo.request()
        }}
        disabled={geo.status === 'locating'}
        className="text-[10.5px] font-semibold text-accent underline-offset-2 hover:underline disabled:text-ink-faint"
      >
        {geo.status === 'locating' ? 'Ubicando…' : 'Cuarteles cerca de mí'}
      </button>
      {geo.error && (
        <p role="alert" className="text-[10.5px] leading-snug text-danger-ink">
          {geo.error}
        </p>
      )}
    </div>
  )
}

function CuartelesCercaAbierto({
  cuarteles,
  lat,
  lon,
  onFocusCuartel,
}: {
  cuarteles: readonly Cuartel[]
  lat: number
  lon: number
  onFocusCuartel: (lon: number, lat: number) => void
}) {
  const cercanos = useMemo(() => cuartelesCercanos(cuarteles, lat, lon, 3), [cuarteles, lat, lon])
  return (
    <ul className="px-1.5">
      {cercanos.map(({ cuartel, km }) => (
        <li key={cuartel.id} className="flex items-center gap-2 py-0.5">
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[10.5px] font-medium text-ink">{cuartel.nombre}</span>
            <span className="block truncate text-[9.5px] text-ink-faint">
              {formatDistancia(km)} en línea recta
            </span>
          </span>
          <button
            type="button"
            onClick={() => onFocusCuartel(cuartel.lon, cuartel.lat)}
            className="shrink-0 text-[10px] font-semibold text-accent underline-offset-2 hover:underline"
          >
            Ver en el mapa
          </button>
        </li>
      ))}
    </ul>
  )
}

/** Elegir el nombre y dónde: la ubicación actual o un punto del mapa. */
function AddPlaceForm({ taken, onDone }: { taken: readonly string[]; onDone: () => void }) {
  const firstFree = NAME_CHOICES.find((n) => !taken.includes(n))
  const [choice, setChoice] = useState<string>(firstFree ?? 'Otro')
  const [custom, setCustom] = useState('')
  const geo = useGeolocation()
  const name = choice === 'Otro' ? custom.trim() : choice
  /*
   * «Estoy aquí» pide el GPS y el lugar se guarda cuando llega la lectura. El
   * nombre se fija al tocar: si la persona cambia la ficha mientras el GPS
   * responde, se guarda con el que eligió al tocar.
   */
  const pending = useRef<string | null>(null)

  useEffect(() => {
    if (pending.current === null || geo.status !== 'ready' || !geo.coords) return
    addPlace(pending.current, geo.coords.lat, geo.coords.lon)
    pending.current = null
    onDone()
  }, [geo.status, geo.coords, onDone])

  const ready = name.length > 0
  const locating = geo.status === 'locating'

  return (
    <div className="space-y-2 rounded-control bg-sunken p-2">
      <div role="radiogroup" aria-label="Nombre del lugar" className="flex flex-wrap gap-1.5">
        {[...NAME_CHOICES, 'Otro'].map((option) => (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={choice === option}
            onClick={() => setChoice(option)}
            className={cn(
              'h-7 rounded-full px-3 text-[11px] font-semibold transition-colors',
              choice === option
                ? 'bg-accent text-accent-ink'
                : 'bg-raised text-ink-muted ring-1 ring-line hover:text-ink',
            )}
          >
            {option}
          </button>
        ))}
      </div>

      {choice === 'Otro' && (
        <input
          type="text"
          value={custom}
          onChange={(event) => setCustom(event.target.value)}
          maxLength={40}
          placeholder="Casa de mis papás, colegio…"
          aria-label="Nombre del lugar"
          className="h-8 w-full rounded-control bg-raised px-2 text-xs text-ink pointer-coarse:h-9 pointer-coarse:text-base ring-1 ring-line placeholder:text-ink-faint focus:outline-none focus:ring-2 focus:ring-accent"
        />
      )}

      <div className="grid grid-cols-2 gap-1.5">
        <Button
          variant="subtle"
          size="sm"
          disabled={!ready || locating}
          onClick={() => {
            pending.current = name
            geo.request()
          }}
        >
          {locating ? 'Ubicando…' : 'Estoy aquí'}
        </Button>
        <Button
          variant="subtle"
          size="sm"
          disabled={!ready}
          onClick={() => {
            // La hoja baja para dejar el mapa libre; la mira queda al centro.
            setSheetSnap('peek')
            startPicking(name)
            onDone()
          }}
        >
          Elegir en el mapa
        </Button>
      </div>

      {geo.error && (
        <p role="alert" className="text-[10.5px] leading-snug text-danger-ink">
          {geo.error}
        </p>
      )}

      <div className="flex items-center justify-between gap-2">
        <p className="text-[9.5px] leading-snug text-ink-faint">
          Se guarda sólo en este dispositivo.
        </p>
        <Button variant="ghost" size="sm" onClick={onDone}>
          Cancelar
        </Button>
      </div>
    </div>
  )
}

export const SavedPlaces = memo(function SavedPlaces({
  history,
  onMapCodes,
  onFocusArea,
  onFocusCuartel,
}: SavedPlacesProps) {
  const places = usePlaces()
  const [adding, setAdding] = useState(false)
  /*
   * Los cuarteles se piden sólo si hay un lugar guardado: sin lugares no hay
   * nada que medir (y «cerca de mí» los pide al tocarlo, vía `pedirCuarteles`).
   */
  const [pedirCuarteles, setPedirCuarteles] = useState(false)
  const cuartelesQuery = useCuartelesData(places.length > 0 || pedirCuarteles)
  const cuarteles = cuartelesQuery.data?.cuarteles ?? SIN_CUARTELES
  const pedir = useCallback(() => setPedirCuarteles(true), [])
  const live = useMemo(
    () => history.filter((incident) => onMapCodes.has(incident.code)),
    [history, onMapCodes],
  )
  const focus = (place: SavedPlace) =>
    onFocusArea({ kind: 'place', id: place.id, name: place.name, lat: place.lat, lon: place.lon })
  const done = () => setAdding(false)

  return (
    <section aria-label="Mis lugares" className="rounded-control">
      <div className="flex items-center gap-2 px-1.5 pb-0.5 pt-1">
        <h3 className="flex-1 text-[9.5px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
          Mis lugares
        </h3>
        {places.length > 0 && places.length < MAX_PLACES && !adding && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="text-[10.5px] font-semibold text-accent underline-offset-2 hover:underline"
          >
            + Agregar
          </button>
        )}
      </div>

      {places.length > 0 && (
        <ul>
          {places.map((place) => (
            <PlaceRow
              key={place.id}
              place={place}
              count={nearby(place, live)}
              onFocus={focus}
              cuarteles={cuarteles}
              onFocusCuartel={onFocusCuartel}
            />
          ))}
        </ul>
      )}

      {adding ? (
        <AddPlaceForm taken={places.map((p) => p.name)} onDone={done} />
      ) : (
        places.length === 0 && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="flex w-full items-center gap-2.5 rounded-control px-1.5 py-1.5 text-left transition-colors hover:bg-hover"
          >
            <span
              aria-hidden
              className="grid size-8 shrink-0 place-items-center rounded-control bg-accent-soft text-accent"
            >
              <PlaceGlyph name="casa" className="size-4" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-xs font-semibold text-ink">Guarda tu casa</span>
              <span className="block text-[10.5px] leading-tight text-ink-muted">
                Para ver de un vistazo si hay algo cerca, y recibir avisos de ahí.
              </span>
            </span>
          </button>
        )
      )}

      {!adding && (
        <CuartelesCercaDeMi
          cuarteles={cuarteles}
          onFocusCuartel={onFocusCuartel}
          onPedir={pedir}
        />
      )}

      {/* Siempre a mano, y en vez del teléfono de cada cuartel: en una
          emergencia se llama a la central, que despacha la compañía. */}
      <p className="px-1.5 pt-1.5 text-[10.5px] text-ink-muted">
        Emergencias Bomberos:{' '}
        <a
          href={`tel:${NUMERO_BOMBEROS}`}
          className="font-semibold text-urgent underline-offset-2 hover:underline"
        >
          {NUMERO_BOMBEROS}
        </a>
      </p>
    </section>
  )
})

const SIN_CUARTELES: readonly Cuartel[] = []

export { PlaceGlyph }
