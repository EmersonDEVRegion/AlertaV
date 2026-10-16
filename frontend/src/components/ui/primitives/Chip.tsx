import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * Ficha encendible: los filtros finos de una capa (empresa, relevancia de los
 * sismos, sólo verificados).
 *
 * Reemplaza a la casilla nativa y al control segmentado que convivían en la
 * pestaña «Capas»: los dos hacían lo mismo con formas distintas. Encendida
 * lleva el acento de interfaz, el «elegido» del resto del cromo (ver `.chip`).
 *
 * `role="radio"` para opciones excluyentes (va dentro de un `radiogroup`);
 * si no, es un botón con `aria-pressed`.
 */
export interface ChipProps {
  on: boolean
  onToggle: () => void
  children: ReactNode
  /** Punto de color antes del texto: la empresa en el mapa. */
  swatch?: string
  /** Número al final, en gris: los cortes de esa empresa. */
  count?: number
  radio?: boolean
  title?: string
}

export function Chip({ on, onToggle, children, swatch, count, radio = false, title }: ChipProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      title={title}
      {...(radio ? { role: 'radio', 'aria-checked': on } : { 'aria-pressed': on })}
      className={cn('chip', on && 'chip-on')}
    >
      {swatch && (
        <span
          aria-hidden
          className="size-2 shrink-0 rounded-full"
          style={on ? { backgroundColor: swatch } : { boxShadow: `inset 0 0 0 1.5px ${swatch}` }}
        />
      )}
      <span className="truncate">{children}</span>
      {count !== undefined && <span className="count text-[10px] text-ink-faint">{count}</span>}
    </button>
  )
}
