import type { ReactNode, Ref } from 'react'
import { cn } from '@/lib/cn'

/**
 * Contenedor flotante sobre el mapa.
 *
 * Toma su fondo, radio, sombra, hairline y desenfoque de la clase
 * `.surface-floating` de `index.css`. La receta está allí y no acá para que un
 * panel nuevo no pueda inventarse su propia mezcla — que es exactamente cómo se
 * llegó a doce tonos de slate distintos repartidos a mano.
 *
 * `backdrop-filter` es lo único caro del conjunto: obliga al compositor a
 * remuestrear el fondo en cada cuadro y debajo hay un mapa repintando. Por eso
 * el valor es único y bajo, y por eso este componente existe: para que el
 * desenfoque se aplique en los pocos sitios donde separa la interfaz del
 * terreno, y no en cada caja que alguien flote.
 */

export interface PanelProps {
  children: ReactNode
  /** Sólo posicionamiento y tamaño. Nunca fondo, radio ni sombra. */
  className?: string
  id?: string
  ref?: Ref<HTMLDivElement>
  /** Saca el contenido del foco y del lector cuando está fuera de pantalla. */
  inert?: boolean
  'aria-hidden'?: boolean
  /*
   * Semántica, no estilo.
   *
   * Un panel es una caja visual y por sí solo no significa nada para un lector
   * de pantalla. Cuando la superficie SÍ delimita una región navegable —el
   * panel que abre una ficha en la interfaz compacta, por ejemplo— hace falta
   * decirlo, y el sitio donde eso se sabe es quien lo usa. La alternativa era
   * envolver el `Panel` en un `<section>`, que añade un nodo al DOM para
   * transportar dos atributos.
   */
  role?: string
  'aria-label'?: string
  'aria-labelledby'?: string
}

export function Panel({ children, className, id, ref, inert, ...rest }: PanelProps) {
  return (
    <div ref={ref} id={id} inert={inert} className={cn('surface-floating', className)} {...rest}>
      {children}
    </div>
  )
}
