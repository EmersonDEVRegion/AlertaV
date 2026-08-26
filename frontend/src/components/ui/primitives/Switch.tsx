import { cva } from 'class-variance-authority'
import type { VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/cn'

/**
 * Interruptor.
 *
 * # La geometría es el contrato
 *
 * El pulgar va anclado con `left-0.5`. Eso NO es una preferencia: un elemento
 * `absolute` sin `left` se coloca en su posición estática, y dentro de un
 * `<button>` esa posición está centrada porque los navegadores aplican
 * `text-align: center` y Tailwind no lo reinicia. Sin el ancla, el pulgar
 * arranca a media pista, «apagado» parece estar a la derecha y encendido se
 * sale del riel invadiendo la etiqueta contigua.
 *
 * Los números están atados por `switch-geometry.test.ts`: inset + recorrido +
 * pulgar tiene que caber exactamente en el riel.
 *
 * # Área táctil
 *
 * El riel mide 16 px de alto. El pseudo-elemento `before:-inset-1.5` extiende
 * la zona clicable 6 px en todas direcciones sin mover el layout, que es lo que
 * lo vuelve usable con el pulgar sin engordar una fila de interfaz densa.
 */

const track = cva(
  cn(
    'relative shrink-0 rounded-full transition-colors',
    'before:absolute before:-inset-1.5 before:content-[""]',
    'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
    'focus-visible:outline-accent',
    'disabled:cursor-not-allowed disabled:opacity-40',
  ),
  {
    variants: {
      size: {
        sm: 'h-4 w-7',
      },
      checked: {
        true: '',
        false: 'bg-line-strong',
      },
    },
    defaultVariants: { size: 'sm', checked: false },
  },
)

const thumb = cva(
  // `bg-white` literal y NO un token de superficie: el pulgar es una pieza
  // física que debe contrastar con el riel en ambos temas. Con `bg-raised`
  // quedaría gris pizarra sobre un riel gris pizarra y desaparecería en oscuro.
  'absolute left-0.5 top-0.5 rounded-full bg-white shadow-sm transition-transform',
  {
    variants: {
      size: { sm: 'size-3' },
      checked: { true: 'translate-x-3', false: 'translate-x-0' },
    },
    defaultVariants: { size: 'sm', checked: false },
  },
)

export interface SwitchProps
  extends Omit<VariantProps<typeof track>, 'checked'> {
  checked: boolean
  onCheckedChange: () => void
  /** Nombre accesible. El interruptor no lleva texto propio. */
  label: string
  /**
   * Color del riel encendido. Se pasa como valor y no como clase porque cada
   * capa tiene el suyo y viene de la paleta de datos, que vive en TypeScript.
   */
  accentColor?: string
  disabled?: boolean
}

export function Switch({
  checked,
  onCheckedChange,
  label,
  accentColor,
  disabled = false,
  size,
}: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onCheckedChange}
      className={track({ size, checked })}
      style={checked && accentColor ? { backgroundColor: accentColor } : undefined}
    >
      <span aria-hidden className={thumb({ size, checked })} />
    </button>
  )
}
