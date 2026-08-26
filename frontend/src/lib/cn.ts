import { clsx } from 'clsx'
import type { ClassValue } from 'clsx'

/**
 * Compone clases condicionales.
 *
 * # Por qué `clsx` a secas y no `clsx` + `tailwind-merge`
 *
 * La receta de shadcn/ui envuelve `clsx` en `twMerge` para que una clase pasada
 * desde fuera gane sobre la del componente (`p-2` + `p-4` → `p-4`). Eso importa
 * en una librería publicada, donde consumidores desconocidos sobrescriben
 * estilos. Acá no hay consumidores externos: todos los sitios de llamada están
 * en este repositorio.
 *
 * Y no es gratis. Medido sobre este bundle:
 *
 *     clsx + cva                    +0,47 KB gz
 *     clsx + cva + tailwind-merge   +8,99 KB gz
 *
 * `tailwind-merge` lleva dentro el mapa de conflictos de todo Tailwind, así que
 * pesa dieciocho veces lo que resuelve para nosotros. En una PWA que se abre en
 * una emergencia, con red de teléfono, ese es el tipo de kilobyte que no se
 * regala.
 *
 * La contrapartida es una regla de disciplina: los componentes de `ui/`
 * declaran sus variantes con `cva` y **no** aceptan `className` que pise
 * propiedades ya definidas. Para posicionar, se envuelve; no se sobrescribe.
 */
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs)
}
