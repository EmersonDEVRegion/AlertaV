import { cn } from '@/lib/cn'

/**
 * Casilla.
 *
 * Se mantiene el `<input type="checkbox">` nativo en vez de dibujar uno propio:
 * trae gratis el estado indeterminado, el foco del sistema, la semántica para
 * lectores de pantalla y el comportamiento correcto en móvil. `accent-color`
 * permite teñirlo sin reimplementar nada.
 *
 * Va envuelto en `<label>` por el llamador, no acá: en este panel la etiqueta a
 * veces contiene un contador y un punto de color, y encapsularla obligaría a
 * abrir un hueco para cada caso.
 */

export interface CheckboxProps {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  /** Tinte del control marcado. Viene de la paleta de datos. */
  accentColor?: string
  disabled?: boolean
  /** Sólo si no hay `<label>` alrededor. */
  label?: string
  className?: string
}

export function Checkbox({
  checked,
  onCheckedChange,
  accentColor,
  disabled = false,
  label,
  className,
}: CheckboxProps) {
  return (
    <input
      type="checkbox"
      checked={checked}
      disabled={disabled}
      aria-label={label}
      onChange={(event) => onCheckedChange(event.target.checked)}
      className={cn(
        'size-3.5 shrink-0 cursor-pointer',
        'disabled:cursor-not-allowed disabled:opacity-40',
        className,
      )}
      style={{ accentColor: accentColor ?? 'var(--accent)' }}
    />
  )
}
