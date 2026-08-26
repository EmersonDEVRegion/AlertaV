import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * Acordeón de una sola sección abierta.
 *
 * # Por qué el disparador NO envuelve la fila entera
 *
 * En este panel cada fila hace **dos** cosas independientes: una casilla que
 * enciende la capa y un contador que despliega su lista. Si el acordeón
 * envolviera la fila completa —que es lo que hace la receta habitual— tocar la
 * casilla también abriría la sección, y peor: un `<label>` reenvía su clic al
 * input que contiene, así que abrir la lista APAGARÍA la capa.
 *
 * Por eso `AccordionRow` recibe el encabezado ya armado por el llamador y sólo
 * aporta el panel desplegable y el estado. Es menos «componente» y más honesto
 * con lo que la interfaz necesita.
 */

interface AccordionRowProps {
  /** Encabezado completo, con sus propios controles. */
  header: ReactNode
  /** Contenido desplegable. No se monta si está cerrado. */
  children?: ReactNode
  open: boolean
  /** Contenido extra que se muestra bajo el encabezado con la capa encendida. */
  aside?: ReactNode
}

export function AccordionRow({ header, children, open, aside }: AccordionRowProps) {
  return (
    <li>
      {header}
      {open && children && (
        /*
         * Sin `max-h` ni scroll propio: el desplazamiento es del panel entero.
         * Dos barras dentro de 240 px de ancho se pelean por el gesto.
         */
        <ul className="mb-1 ml-1 space-y-0.5 border-l border-line pl-1.5">{children}</ul>
      )}
      {aside}
    </li>
  )
}

/**
 * Disparador del desplegable: el contador.
 *
 * Se deshabilita cuando no hay nada que mostrar en vez de ocultarse, para que
 * la fila no cambie de forma según el dato — un control que aparece y
 * desaparece obliga a releer la fila entera cada vez.
 */
export function AccordionTrigger({
  open,
  disabled,
  label,
  onToggle,
  children,
}: {
  open: boolean
  disabled: boolean
  label: string
  onToggle: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      aria-expanded={open}
      aria-label={label}
      className={cn(
        'flex shrink-0 items-center gap-1 rounded-control px-1 py-0.5 transition',
        'hover:bg-hover disabled:pointer-events-none disabled:opacity-40',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent',
      )}
    >
      {children}
      <span
        aria-hidden
        className={cn('text-ink-faint transition-transform', open && 'rotate-90')}
      >
        ›
      </span>
    </button>
  )
}
