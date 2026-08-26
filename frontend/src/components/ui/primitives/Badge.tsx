import { cva } from 'class-variance-authority'
import type { VariantProps } from 'class-variance-authority'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * Etiqueta compacta: contadores, estados, nombres de empresa.
 *
 * # El contador es el caso difícil
 *
 * Un badge con un número que cambia solo tiene que ocupar siempre lo mismo, o
 * cada actualización empuja lo que tenga al lado. La variante `count` aplica la
 * clase `.count` de `index.css`, que reserva `2ch` alineados a la derecha: el
 * número crece hacia adentro y el layout no se entera. Ver la nota extensa allí.
 *
 * `3ch` para contadores que pueden pasar de 99 — hoy sólo los sismos, que en un
 * enjambre llegan a tres cifras.
 */

const badge = cva('inline-flex items-center justify-center font-medium', {
  variants: {
    variant: {
      /** Contador de una lista. Ancho reservado, sin fondo. */
      count: 'count text-[11px] text-ink-muted',
      /** Estado o categoría. Con fondo tenue. */
      soft: 'rounded-full bg-sunken px-2 py-0.5 text-[10px] text-ink-muted',
      /** Aviso: algo requiere atención. */
      warn: 'rounded-full bg-warn-bg px-2 py-0.5 text-[10px] text-warn-ink',
    },
    width: {
      two: '',
      three: 'count-3',
    },
  },
  defaultVariants: { variant: 'count', width: 'two' },
})

export interface BadgeProps extends VariantProps<typeof badge> {
  children: ReactNode
  className?: string
  title?: string
}

export function Badge({ variant, width, className, children, title }: BadgeProps) {
  return (
    <span title={title} className={cn(badge({ variant, width }), className)}>
      {children}
    </span>
  )
}
