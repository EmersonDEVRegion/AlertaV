import { memo } from 'react'
import type { ReactNode } from 'react'
import { Panel } from '@/components/ui/primitives'
import { ExplorePanel, type ExplorePanelProps } from './ExplorePanel'

/**
 * La columna de escritorio: el contenido a la izquierda, el mapa libre al lado.
 *
 * El ancho es `COLUMN_WIDTH_REM` (22 rem, en `lib/cameraOffset`): la cámara
 * corre el centro útil la mitad de eso al enfocar algo, y los controles del
 * mapa que viven abajo a la izquierda se corren con él (ver `index.css`,
 * `[data-shell='column']`).
 *
 * # A la medida de su contenido (§D)
 *
 * Antes iba de arriba abajo siempre (`top-3 bottom-3`): con poco contenido, la
 * mitad inferior era un panel vacío tapando el mapa. Ahora mide lo que muestra,
 * con tope en el alto disponible (`max-h`); si no cabe, la lista se desplaza
 * adentro (`ExplorePanel` ya tiene su zona con scroll y `min-h-0`).
 */
export const DesktopColumn = memo(function DesktopColumn({
  headerAction,
  ...props
}: ExplorePanelProps & { headerAction?: ReactNode }) {
  return (
    <Panel
      aria-label="Emergencias e historial"
      role="complementary"
      className="animate-slide-in pointer-events-auto absolute left-3 top-3 z-10 flex max-h-[calc(100%-1.5rem)] w-[22rem] flex-col overflow-hidden"
    >
      <ExplorePanel {...props} headerAction={headerAction} />
    </Panel>
  )
})
