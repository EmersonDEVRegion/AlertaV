import type { ReactNode } from 'react'
import type { VehicleFeedItem } from '@/api/vehicleFeedTypes'
import {
  detectedAt,
  eventDateLabel,
  formatCalendarDay,
  isRecent,
  vehicleTitle,
} from '@/domain/vehicleFeed'
import { VEHICLE_STATUS } from '@/domain/vehicleSymbology'
import { cn } from '@/lib/cn'
import { formatDateTime, formatRelativeShort } from '@/lib/format'
import { LicensePlate } from './LicensePlate'

/**
 * Un vehículo del radar. Cerrada mide una fila de ~60 px.
 *
 *     ┌──────────┐  Toyota Rav4 · Negro          ● ROBADO
 *     │ LK·XV·55 │  Quilpué · visto hace 3 h    robo 22 sep
 *     └──CHILE───┘
 *
 * # La jerarquía
 *
 * La patente manda: es lo único que alguien en la calle puede comparar con lo
 * que tiene delante. Después, qué auto es, dónde y cuándo. El estado va a la
 * derecha, con color y con palabra: el color solo no alcanza (daltonismo,
 * pantalla al sol), y la palabra sola obliga a leer cada fila.
 *
 * # «Visto hace», no «robado hace»
 *
 * El tiempo relativo sale de `detectado_en`, que es cuándo lo vio AlertaV. GBV
 * publica con atraso y sin hora, así que «robado hace 3 h» sería una afirmación
 * que nadie hizo. La fecha del robo va aparte, como día.
 *
 * # Reciente
 *
 * Menos de 6 h: una franja del color del estado, un halo que late detrás del
 * punto y la hora en negrita. Son tres señales de tipo distinto (forma,
 * movimiento, peso) para que ninguna dependa de las otras. El lector de
 * pantalla oye «Nuevo».
 *
 * # Tocar
 *
 * Despliega el detalle en el mismo sitio, no navega: la persona que revisa una
 * lista no quiere perder su posición en ella. El enlace a GBV va dentro.
 */

interface VehicleCardProps {
  item: VehicleFeedItem
  now: number
  open: boolean
  onToggle: (id: string) => void
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="contents">
      <dt className="text-ink-faint">{label}</dt>
      <dd className="min-w-0 break-words text-ink">{children}</dd>
    </div>
  )
}

function StatusTag({ item, recent }: { item: VehicleFeedItem; recent: boolean }) {
  const style = VEHICLE_STATUS[item.estado]
  return (
    <span className="inline-flex items-center gap-1.5 text-[9.5px] font-bold uppercase leading-none tracking-[0.08em] text-ink-muted">
      <span aria-hidden className="relative grid size-2 place-items-center">
        {recent && (
          <span
            className="animate-halo absolute inset-0 rounded-full"
            style={{ backgroundColor: style.color }}
          />
        )}
        <span className="relative size-2 rounded-full" style={{ backgroundColor: style.color }} />
      </span>
      {style.label}
    </span>
  )
}

export function VehicleCard({ item, now, open, onToggle }: VehicleCardProps) {
  const style = VEHICLE_STATUS[item.estado]
  const recent = isRecent(item, now)
  const title = vehicleTitle(item)
  const dateLabel = eventDateLabel(item)
  const place = item.comuna ?? 'Sin ubicar'
  // Un reloj de teléfono atrasado haría que un aviso recién llegado fuera «del
  // futuro» y se leyera «visto dentro de 2 minutos». Se recorta a ahora.
  const seen = formatRelativeShort(Math.min(detectedAt(item) ?? now, now), now)
  const detailsId = `vehicle-${item.id}`

  return (
    <li className="relative">
      {recent && (
        <span
          aria-hidden
          className="absolute inset-y-2 left-0 w-[3px] rounded-full"
          style={{ backgroundColor: style.color }}
        />
      )}

      <button
        type="button"
        onClick={() => onToggle(item.id)}
        aria-expanded={open}
        aria-controls={detailsId}
        className={cn(
          'flex w-full items-center gap-2.5 rounded-control py-2 pl-2.5 pr-2 text-left',
          'transition-[background-color] duration-150',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent',
          open ? 'bg-sunken' : 'hover:bg-hover',
        )}
      >
        <LicensePlate patente={item.patente} />

        <span className="min-w-0 flex-1">
          <span className="block truncate text-[12px] font-semibold leading-tight text-ink">
            {recent && <span className="sr-only">Nuevo: </span>}
            {/* Sin patente, la marca es lo único que identifica al vehículo:
                sube un punto de tamaño para ocupar el lugar vacío. */}
            <span className={cn(!item.patente && 'text-[13px]')}>{title}</span>
            {item.color && <span className="font-normal text-ink-muted"> · {item.color}</span>}
          </span>
          <span className="mt-0.5 block truncate text-[10.5px] leading-tight text-ink-muted">
            <span className={cn(item.region === 'sin_ubicar' && 'italic')}>{place}</span>
            {' · '}
            <span className={cn(recent && 'font-semibold text-ink')}>visto {seen}</span>
          </span>
        </span>

        <span className="flex shrink-0 flex-col items-end gap-1">
          <StatusTag item={item} recent={recent} />
          {dateLabel && (
            <span className="text-[10px] leading-none text-ink-faint">{dateLabel}</span>
          )}
        </span>
      </button>

      {open && (
        <div id={detailsId} className="animate-fade px-2.5 pb-2.5 pt-1">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px] leading-snug">
            {(item.tipo_vehiculo || item.anio) && (
              <Detail label="Vehículo">
                {[item.tipo_vehiculo, item.anio].filter(Boolean).join(' · ')}
              </Detail>
            )}
            {item.delito && <Detail label="Delito">{item.delito}</Detail>}
            {item.fecha_delito && (
              <Detail label="Fecha del robo">{formatCalendarDay(item.fecha_delito, true)}</Detail>
            )}
            {item.recuperado_en && <Detail label="Recuperado en">{item.recuperado_en}</Detail>}
            {item.autoridad && <Detail label="Autoridad">{item.autoridad}</Detail>}
            {item.tiempo_abandono && (
              <Detail label="Abandonado hace">{item.tiempo_abandono}</Detail>
            )}
            {item.lugar && <Detail label="Lugar (GBV)">{item.lugar}</Detail>}
            <Detail label="Visto por AlertaV">{formatDateTime(item.detectado_en)}</Detail>
          </dl>

          {item.region === 'sin_ubicar' && (
            <p className="callout callout-muted mt-2">
              El lugar publicado no alcanza para saber la comuna. Puede no ser de la
              Región de Valparaíso.
            </p>
          )}

          {item.url_fuente && (
            <a
              href={item.url_fuente}
              target="_blank"
              rel="noopener noreferrer"
              className={cn(
                'mt-2 inline-flex items-center gap-1 text-[11px] font-semibold text-accent',
                'underline-offset-2 hover:underline',
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
                'focus-visible:outline-accent',
              )}
            >
              Ver el aviso en GBV
              {/* Vectorial: la flecha ↗ como carácter la dibuja la fuente del
                  sistema, y en varios teléfonos sale como emoji de color. */}
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2.4}
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden
                className="size-3"
              >
                <path d="M7 17 17 7M8 7h9v9" />
              </svg>
            </a>
          )}
        </div>
      )}
    </li>
  )
}
