import { memo } from 'react'
import { Panel } from '@/components/ui/primitives'
import { ExplorePanel, type ExplorePanelProps } from './ExplorePanel'

/**
 * La columna de escritorio: el contenido a la izquierda, el mapa libre al lado.
 *
 * El ancho es `COLUMN_WIDTH_REM` (22 rem, en `lib/cameraOffset`): la cámara
 * corre el centro útil la mitad de eso al enfocar algo, y los controles del
 * mapa que viven abajo a la izquierda se corren con él (ver `index.css`,
 * `[data-shell='column']`).
 */
export const DesktopColumn = memo(function DesktopColumn(props: ExplorePanelProps) {
  return (
    <Panel
      aria-label="Emergencias e historial"
      role="complementary"
      className="animate-slide-in pointer-events-auto absolute bottom-3 left-3 top-3 z-10 flex w-[22rem] flex-col overflow-hidden"
    >
      <ExplorePanel {...props} />
    </Panel>
  )
})
