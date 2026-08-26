import { cva } from 'class-variance-authority'
import type { VariantProps } from 'class-variance-authority'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * Botón.
 *
 * Las variantes describen el ROL, no el aspecto: `urgent` es la acción que
 * interrumpe, `ghost` la que acompaña. Nombrarlas por color («red», «blue»)
 * ataría cada sitio de llamada a una decisión estética y volvería imposible
 * recolorear la interfaz sin recorrer el árbol entero.
 *
 * La respuesta táctil es la misma en todas: una reducción de escala al pulsar.
 * `active:scale-[0.97]` sólo toca el compositor —no dispara layout— así que se
 * resuelve en GPU y no compite con el repintado del mapa.
 *
 * Sobre la lista de `transition-property`: en Tailwind v4 `scale-*` y
 * `translate-*` NO compilan a `transform`, sino a las propiedades
 * independientes `scale:` y `translate:` (v3 sí las componía en `transform`).
 * Listar `transform` acá era escribir el nombre de una propiedad que nadie
 * declara: la reducción al pulsar saltaba seca, sin interpolar. Se nombran las
 * dos propiedades reales.
 */

const button = cva(
  cn(
    'inline-flex items-center justify-center gap-2 font-medium',
    'transition-[background-color,box-shadow,translate,scale,filter] duration-150',
    'active:scale-[0.97]',
    'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
    'focus-visible:outline-accent',
    'disabled:pointer-events-none disabled:opacity-50',
  ),
  {
    variants: {
      variant: {
        /**
         * Acción principal urgente. Un solo botón así por pantalla.
         *
         * El degradado es LUZ, no color: un velo blanco que se desvanece hacia
         * abajo sobre `bg-urgent`. Codificar los extremos como rojos concretos
         * (`from-red-500 to-red-600`) ataría el botón a un tema y dejaría fuera
         * al otro; un velo neutro se aplica igual sobre el `#dc2626` del tema
         * claro y el `#ef4444` del oscuro, y sigue leyéndose como «iluminado
         * desde arriba».
         *
         * El extremo va escrito a mano y no como `to-transparent` ni
         * `to-white/0`: los dos computan a NEGRO con alfa cero (`to-white/0`
         * es `color-mix(#fff 0%, transparent)`, y lo que queda del mezclado es
         * el `transparent`). Tailwind v4 interpola el degradado `in oklab`, con
         * alfa premultiplicado, y ahí el tono de una parada invisible da igual
         * — pero eso va dentro de un `@supports`, y en el navegador que no lo
         * cumple la interpolación vuelve a sRGB sin premultiplicar y el tramo
         * medio se ensucia de gris. Esta PWA se abre en una emergencia, con el
         * teléfono que haya; el camino de respaldo también tiene que verse.
         *
         * El hover ya no cambia `background-color` —el degradado lo taparía—
         * sino el brillo. `filter` se compone en GPU y, como la sombra es negro
         * puro (0,0,0), multiplicar por 110% no la altera: sólo sube el rojo.
         */
        urgent: cn(
          'bg-urgent text-urgent-ink',
          'bg-linear-to-b from-white/12 to-[rgb(255_255_255/0)]',
          'shadow-[0_2px_8px_rgb(0_0_0/0.18)]',
          'hover:brightness-110 hover:shadow-[0_4px_14px_rgb(0_0_0/0.22)]',
          'active:brightness-95 active:shadow-[0_1px_4px_rgb(0_0_0/0.20)]',
        ),
        /** Acción secundaria sobre una superficie ya existente. */
        subtle: cn(
          'bg-sunken text-ink',
          'inset-ring inset-ring-line',
          'hover:bg-hover',
        ),
        /** Sin fondo hasta que se apunta. Para filas y acciones terciarias. */
        ghost: 'text-ink-muted hover:bg-hover hover:text-ink',
      },
      size: {
        sm: 'h-7 rounded-control px-2 text-xs',
        md: 'h-9 rounded-control px-3 text-sm',
        /**
         * Flotante: píldora, y con más aire porque se toca con el pulgar.
         *
         * La elevación al apuntar vive en el TAMAÑO y no en la variante porque
         * es una propiedad de «flotar», no de «urgir»: un botón urgente dentro
         * de un formulario no debe despegarse de su superficie.
         *
         * `motion-safe:` y no la regla global de `prefers-reduced-motion`: esa
         * regla sólo acorta la duración a 0.01ms, así que el salto seguiría
         * ocurriendo, instantáneo. Acá el desplazamiento se retira del todo y
         * quedan el brillo y la sombra, que no son movimiento.
         *
         * Tailwind envuelve solo `hover:` en `@media (hover: hover)`, así que
         * en pantalla táctil no queda pegado tras el toque.
         */
        fab: cn(
          'h-12 rounded-full px-5 text-sm font-semibold',
          'motion-safe:hover:-translate-y-0.5',
        ),
      },
    },
    defaultVariants: { variant: 'ghost', size: 'md' },
  },
)

export interface ButtonProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className'>,
    VariantProps<typeof button> {
  children: ReactNode
  /** Sólo posicionamiento. Las propiedades visuales salen de las variantes. */
  className?: string
}

export function Button({ variant, size, className, children, ...rest }: ButtonProps) {
  return (
    <button type="button" className={cn(button({ variant, size }), className)} {...rest}>
      {children}
    </button>
  )
}
