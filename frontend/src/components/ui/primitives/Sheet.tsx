import { useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { Panel } from './Panel'

/**
 * Hoja lateral colapsable, anclada al borde derecho sobre el mapa.
 *
 * # Tres capas, cada una con una sola responsabilidad
 *
 *   1. **contenedor** — ancla la posición. NO se mueve.
 *   2. **deslizador** — el único que se transforma. Lleva dentro la pestaña.
 *   3. **panel**      — la superficie con el contenido.
 *
 * La pestaña va DENTRO del deslizador, anclada a su borde izquierdo con
 * `right-full`. Así, al desplazar el deslizador por (ancho + inset), el panel
 * sale completo de la pantalla y la pestaña queda justo en el borde, que es lo
 * único que debe seguir asomando. Si la pestaña viviera fuera habría que
 * animarla por separado y las dos piezas se desincronizarían a mitad de camino.
 *
 * # Por qué `translateX` y no `width` ni `display`
 *
 * Ambos disparan layout en cada cuadro y el navegador no puede componer la
 * animación en el hilo del compositor. Debajo hay un mapa repintando teselas:
 * con `transform` la transición se resuelve en GPU y sobrevive a eso.
 *
 * El panel queda `inert` al cerrarse. Sigue en el DOM —es lo que permite
 * animarlo— pero fuera de la pantalla: sin eso, el tabulador seguiría entrando
 * en controles invisibles y un lector de pantalla los anunciaría.
 */

/** Ancho del panel. Lo usan el contenedor y el desplazamiento de cierre. */
export const SHEET_WIDTH = 'w-60'

/**
 * Techo de altura.
 *
 * `dvh` y no `vh`: en móvil la barra del navegador se retrae al desplazarse y
 * `vh` conserva el valor de la ventana expandida, así que el panel se cortaría
 * fuera del área visible justo en los teléfonos donde más molesta.
 */
export const SHEET_MAX_H = 'max-h-[calc(100dvh-10rem)] md:max-h-[calc(100dvh-11rem)]'

export const SHEET_PANEL_ID = 'map-layer-panel'

function Chevron({ collapsed }: { collapsed: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={cn('size-4 transition-transform duration-300', collapsed && 'rotate-180')}
    >
      <path d="m9 18 6-6-6-6" />
    </svg>
  )
}

export interface SheetProps {
  children: ReactNode
  /** Texto del control de apertura, para lector de pantalla. */
  label?: string
}

export function Sheet({ children, label = 'filtros del mapa' }: SheetProps) {
  // Abierto por defecto: el panel es el índice del mapa, y esconderlo de
  // entrada dejaría la pantalla sin pistas de qué se está viendo.
  const [collapsed, setCollapsed] = useState(false)

  return (
    /*
      La entrada llega desde el borde derecho —de ahí el `--slide-from`
      positivo—, en espejo del riel izquierdo. Va en el CONTENEDOR y no en el
      deslizador: el deslizador ya anima `translate` para plegarse, y dos
      animaciones escribiendo la misma propiedad se pisan.
    */
    <div
      className="animate-slide-in pointer-events-none absolute right-3 top-[7.5rem] z-10 md:top-[8.5rem]"
      style={{ '--slide-from': '10px' } as CSSProperties}
    >
      <div
        className={cn(
          'pointer-events-auto relative transition-transform duration-300',
          'ease-[cubic-bezier(0.22,1,0.36,1)] will-change-transform',
          collapsed ? 'translate-x-[calc(100%+0.75rem)]' : 'translate-x-0',
        )}
      >
        <button
          type="button"
          onClick={() => setCollapsed((value) => !value)}
          aria-expanded={!collapsed}
          aria-controls={SHEET_PANEL_ID}
          aria-label={collapsed ? `Mostrar ${label}` : `Ocultar ${label}`}
          title={collapsed ? 'Mostrar filtros' : 'Ocultar filtros'}
          className={cn(
            'absolute right-full top-3 grid h-12 w-7 place-items-center',
            // Sólo el costado visible se redondea: el otro se funde con el panel.
            'rounded-l-surface rounded-r-none',
            'surface-floating text-ink-faint transition-colors hover:text-ink',
            'focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent',
          )}
          style={{
            // El anillo del panel dibujaría una línea entre ambos; recortarla
            // en el costado que se tocan es lo que los hace ver como una pieza.
            clipPath: 'inset(-8px 0 -8px -8px)',
          }}
        >
          <Chevron collapsed={collapsed} />
        </button>

        <Panel
          id={SHEET_PANEL_ID}
          inert={collapsed}
          aria-hidden={collapsed}
          className={cn(SHEET_WIDTH, SHEET_MAX_H, 'overflow-y-auto overscroll-contain p-2.5')}
        >
          {children}
        </Panel>
      </div>
    </div>
  )
}
