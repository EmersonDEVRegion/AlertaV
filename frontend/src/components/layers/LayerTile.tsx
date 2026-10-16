import type { CSSProperties, ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * Azulejo de una capa: un toque la enciende o la apaga.
 *
 * # Por qué un azulejo y no una casilla
 *
 * Una casilla dice «seleccionar»; una capa es «ver u ocultar». Además la casilla
 * nativa la dibuja el sistema —distinta en Android, iOS y Windows— y teñida con
 * el color de cada capa daba seis casillas de seis colores. El azulejo muestra
 * el mismo glifo y el mismo color que el mapa, y el estado se lee de lejos:
 * encendido se tiñe de la capa, apagado queda en contorno gris.
 *
 * Es un `<button aria-pressed>`: el nombre accesible sale de su texto, y el
 * contador o la marca de salud van dentro.
 */
export interface LayerTileProps {
  label: string
  icon: ReactNode
  /** Color de la capa en el mapa (hex). */
  color: string
  on: boolean
  onToggle: () => void
  /** Arriba a la derecha: el contador, o la marca de «sin datos». */
  corner?: ReactNode
  /** Una línea bajo el nombre: el estado de una capa de referencia. */
  detail?: ReactNode
  /** El dato todavía no llega: el ícono late, el azulejo sigue firme. */
  busy?: boolean
  failed?: boolean
}

export function LayerTile({
  label,
  icon,
  color,
  on,
  onToggle,
  corner,
  detail,
  busy = false,
  failed = false,
}: LayerTileProps) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onToggle}
      className="layer-tile"
      style={{ '--layer': color } as CSSProperties}
    >
      <span className="flex w-full items-start justify-between gap-1">
        <span aria-hidden className={cn('layer-tile-icon', busy && 'animate-pulse')}>
          {icon}
        </span>
        {corner}
      </span>
      <span className="layer-tile-label">{label}</span>
      {detail && (
        <span
          className={cn(
            'block w-full truncate text-[10px] leading-tight',
            failed ? 'text-danger-ink' : 'text-ink-muted',
          )}
        >
          {detail}
        </span>
      )}
    </button>
  )
}

/** Rótulo de grupo de la pestaña, con una acción opcional a la derecha. */
export function LayerGroupHeading({
  id,
  children,
  action,
}: {
  id: string
  children: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="flex items-center gap-2 px-1.5 pb-1.5 pt-1">
      <h3
        id={id}
        className="min-w-0 flex-1 truncate text-[9.5px] font-semibold uppercase tracking-[0.08em] text-ink-faint"
      >
        {children}
      </h3>
      {action}
    </div>
  )
}
