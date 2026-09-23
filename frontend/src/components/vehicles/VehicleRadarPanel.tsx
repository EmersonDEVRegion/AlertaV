import { useEffect, useMemo, useRef, useState } from 'react'
import type { HealthStatus } from '@/api/health'
import type { VehicleStatus } from '@/api/vehicleFeedTypes'
import { FEED_LIMIT, FEED_WINDOW_HOURS } from '@/domain/vehicleFeed'
import { VEHICLE_STATUS, VEHICLE_STATUS_ORDER } from '@/domain/vehicleSymbology'
import type { VehicleFeedState } from '@/hooks/useVehicleFeed'
import { cn } from '@/lib/cn'
import { formatRelative } from '@/lib/format'
import { RadarGlyph } from './RadarGlyph'
import { VehicleCard } from './VehicleCard'

/**
 * Panel del radar de vehículos.
 *
 * # Dónde vive, y por qué dos formas con un solo árbol
 *
 * En escritorio es un cajón que cuelga bajo la barra, a la derecha: el mismo
 * borde del botón que lo abre. No lleva velo, porque el mapa tiene que seguir
 * usable detrás. En teléfono es una hoja inferior de 72 dvh con velo: ahí no
 * queda mapa útil al lado, y el velo dice dónde tocar para volver.
 *
 * Las dos formas salen de las mismas clases con variantes `md:`, no de dos
 * árboles como `MobileMapControls`. Allá el cambio es de interfaz entera; acá
 * es sólo de posición, y un solo árbol conserva el filtro y la tarjeta abierta
 * si alguien gira el teléfono.
 *
 * # No es modal
 *
 * Igual que la ficha del incidente: en una emergencia, tapar el contexto para
 * mostrar un detalle es lo contrario de lo que hace falta. Sin trampa de foco.
 * Al abrir, el foco entra al panel (el lector de pantalla anuncia su nombre) y
 * Escape lo cierra.
 */

export const RADAR_PANEL_ID = 'vehicle-radar-panel'

type Filter = 'todos' | VehicleStatus

interface VehicleRadarPanelProps {
  feed: VehicleFeedState
  onClose: () => void
}

/**
 * Qué decir cuando no hay avisos y la fuente no está sana. Mismo espíritu que
 * `LayerHealth`: no dice «GBV tiene un problema», dice «no leas este cero como
 * calma».
 */
const BLIND_TEXT: Record<Exclude<HealthStatus, 'ok'>, string> = {
  failing: 'La última lectura de GBV falló.',
  stale: 'Hace demasiado que AlertaV no logra leer GBV.',
  degraded: 'GBV respondió, pero lo que entregó no describe el presente.',
  never: 'AlertaV todavía no ha leído GBV.',
}

function CloseGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.2}
      strokeLinecap="round"
      aria-hidden
      className="size-4"
    >
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  )
}

function SkeletonRows() {
  return (
    <ul aria-hidden className="space-y-1.5 px-1 py-1">
      {[0, 1, 2].map((row) => (
        <li key={row} className="flex items-center gap-2.5 px-1.5 py-2">
          <span className="shimmer h-[2.125rem] w-[5.25rem] shrink-0 rounded-[4px] bg-sunken" />
          <span className="flex-1 space-y-1.5">
            <span className="shimmer block h-2.5 w-2/3 rounded-full bg-sunken" />
            <span className="shimmer block h-2 w-1/2 rounded-full bg-sunken" />
          </span>
        </li>
      ))}
    </ul>
  )
}

function FilterChip({
  active,
  label,
  count,
  color,
  onClick,
}: {
  active: boolean
  label: string
  count: number
  color?: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-[11px] font-medium',
        'transition-[background-color,color] duration-150',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent',
        active ? 'bg-accent-soft text-accent' : 'bg-sunken text-ink-muted hover:bg-hover',
      )}
    >
      {color && (
        <span aria-hidden className="size-1.5 rounded-full" style={{ backgroundColor: color }} />
      )}
      {label}
      <span className={cn('font-semibold tabular-nums', active ? 'text-accent' : 'text-ink')}>
        {count}
      </span>
    </button>
  )
}

export function VehicleRadarPanel({ feed, onClose }: VehicleRadarPanelProps) {
  const [filter, setFilter] = useState<Filter>('todos')
  const [openId, setOpenId] = useState<string | null>(null)
  const root = useRef<HTMLElement>(null)

  // El foco entra al panel al abrirlo: sin esto, quien navega con teclado
  // seguiría en la barra y el lector de pantalla no anunciaría nada.
  useEffect(() => {
    root.current?.focus({ preventScroll: true })
  }, [])

  // Burbuja y no captura, a propósito. El modal de reporte escucha Escape en
  // captura y detiene la propagación: si está abierto encima del radar, un
  // Escape cierra sólo el modal. Con la ficha del incidente no hay conflicto,
  // porque `App` nunca deja las dos abiertas a la vez.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const counts = useMemo(() => {
    const byStatus: Record<VehicleStatus, number> = { robado: 0, recuperado: 0, abandonado: 0 }
    for (const item of feed.items) byStatus[item.estado] += 1
    return byStatus
  }, [feed.items])

  const shown = useMemo(
    () => (filter === 'todos' ? feed.items : feed.items.filter((item) => item.estado === filter)),
    [feed.items, filter],
  )

  const toggle = (id: string) => setOpenId((current) => (current === id ? null : id))

  const lastRun = feed.source?.ultima_corrida
  const hasList = feed.status === 'ready'

  return (
    <>
      {/* Velo sólo en teléfono. Tocarlo cierra, que es el gesto que se espera. */}
      <div
        aria-hidden
        onClick={onClose}
        className="animate-fade fixed inset-0 z-40 bg-scrim md:hidden"
      />

      <section
        ref={root}
        id={RADAR_PANEL_ID}
        role="dialog"
        aria-modal="false"
        aria-labelledby="vehicle-radar-title"
        tabIndex={-1}
        className={cn(
          'animate-rise pointer-events-auto flex flex-col bg-raised shadow-[var(--shadow-raised)]',
          'outline-none',
          // Teléfono: hoja inferior, por encima del botón de reporte (z-30).
          'fixed inset-x-0 bottom-0 z-40 max-h-[72dvh] rounded-t-surface',
          // Escritorio: cajón bajo la barra, por encima de la hoja de capas (z-10).
          'md:absolute md:inset-x-auto md:bottom-auto md:right-3 md:top-3 md:z-20',
          'md:max-h-[calc(100%-1.5rem)] md:w-[23rem] md:rounded-surface',
        )}
      >
        <div aria-hidden className="mx-auto mt-2 h-1 w-10 rounded-full bg-line-strong md:hidden" />

        <header className="flex items-start gap-2.5 px-3.5 pb-2 pt-3">
          <span
            aria-hidden
            className="grid size-8 shrink-0 place-items-center rounded-control bg-sunken text-ink-muted"
          >
            <RadarGlyph className="size-[18px]" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="vehicle-radar-title" className="text-[13px] font-semibold leading-tight text-ink">
              Radar de vehículos
            </h2>
            <p className="mt-0.5 text-[10.5px] leading-tight text-ink-muted">
              Robados, recuperados y abandonados · últimas {FEED_WINDOW_HOURS} h
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar radar de vehículos"
            className={cn(
              '-mr-1 -mt-0.5 grid size-8 shrink-0 place-items-center rounded-full text-ink-faint',
              'transition-colors hover:bg-hover hover:text-ink',
              'focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent',
            )}
          >
            <CloseGlyph />
          </button>
        </header>

        {hasList && (
          <div
            role="group"
            aria-label="Filtrar por estado"
            // Envuelven en vez de deslizarse: un filtro escondido fuera del
            // borde, sin barra que lo delate, es un filtro que no existe.
            className="flex flex-wrap gap-1.5 px-3.5 pb-2"
          >
            <FilterChip
              active={filter === 'todos'}
              label="Todos"
              count={feed.count}
              onClick={() => setFilter('todos')}
            />
            {VEHICLE_STATUS_ORDER.map((status) => (
              <FilterChip
                key={status}
                active={filter === status}
                label={VEHICLE_STATUS[status].plural}
                count={counts[status]}
                color={VEHICLE_STATUS[status].color}
                onClick={() => setFilter(status)}
              />
            ))}
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain border-t border-line px-1.5 py-1.5">
          {feed.status === 'loading' && (
            <>
              <p className="sr-only" role="status">
                Cargando avisos de vehículos…
              </p>
              <SkeletonRows />
            </>
          )}

          {feed.status === 'ready' && shown.length > 0 && (
            <ul aria-label="Avisos de vehículos" className="space-y-0.5">
              {shown.map((item) => (
                <VehicleCard
                  key={item.id}
                  item={item}
                  now={feed.now}
                  open={openId === item.id}
                  onToggle={toggle}
                />
              ))}
            </ul>
          )}

          {feed.status === 'ready' && shown.length === 0 && filter !== 'todos' && (
            <p className="px-2 py-6 text-center text-[11px] text-ink-muted">
              Ningún vehículo {VEHICLE_STATUS[filter].label.toLowerCase()} en las últimas{' '}
              {FEED_WINDOW_HOURS} h.
            </p>
          )}

          {feed.status === 'empty' && (
            <div className="px-3 py-7 text-center">
              <span
                aria-hidden
                className="mx-auto mb-2 grid size-10 place-items-center rounded-full bg-sunken text-ink-faint"
              >
                <RadarGlyph className="size-5" />
              </span>
              <p className="text-[12px] font-semibold text-ink">Sin avisos recientes</p>
              <p className="mx-auto mt-1 max-w-[16rem] text-[11px] leading-snug text-ink-muted">
                GBV no ha publicado vehículos robados, recuperados ni abandonados de la
                región en las últimas {FEED_WINDOW_HOURS} h.
              </p>
            </div>
          )}

          {feed.status === 'blind' && (
            <div className="p-2">
              <p className="callout callout-warn">
                <strong>Sin datos de GBV.</strong>{' '}
                {feed.sourceStatus && feed.sourceStatus !== 'ok' && (
                  <>{BLIND_TEXT[feed.sourceStatus]} </>
                )}
                Que no haya avisos no significa que no haya robos.
              </p>
            </div>
          )}

          {feed.status === 'error' && (
            <div className="space-y-2 p-2">
              <p className="callout callout-danger" role="alert">
                No se pudo cargar el radar. Revisa tu conexión.
              </p>
              <button
                type="button"
                onClick={feed.refetch}
                disabled={feed.isFetching}
                className={cn(
                  'inline-flex h-8 items-center rounded-control bg-sunken px-3 text-[12px] font-medium text-ink',
                  'inset-ring inset-ring-line transition-colors hover:bg-hover disabled:opacity-50',
                  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent',
                )}
              >
                {feed.isFetching ? 'Reintentando…' : 'Reintentar'}
              </button>
            </div>
          )}

          {feed.status === 'unavailable' && (
            <div className="p-2">
              <p className="callout callout-muted">
                El radar de vehículos todavía no está disponible en este servidor.
              </p>
            </div>
          )}
        </div>

        <footer className="border-t border-line px-3.5 pb-[max(0.625rem,env(safe-area-inset-bottom))] pt-2 text-[10px] leading-snug text-ink-faint">
          {feed.refreshFailed && (
            <p className="mb-1 text-warn-ink">
              No se pudo actualizar. Se muestra la última lista recibida.
            </p>
          )}
          {feed.truncated && (
            <p className="mb-1">Se muestran los {FEED_LIMIT} avisos más recientes.</p>
          )}
          <p>
            Fuente:{' '}
            <a
              href="https://gbvspa.cl"
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-ink-muted underline-offset-2 hover:underline"
            >
              GBV
            </a>
            {lastRun && <> · última lectura {formatRelative(lastRun, feed.now)}</>}
            {' · '}
            «visto» es cuándo lo detectó AlertaV.
          </p>
        </footer>
      </section>
    </>
  )
}
