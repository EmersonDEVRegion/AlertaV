import { memo, useCallback } from 'react'
import type { RefObject } from 'react'
import type { VehicleFeedState } from '@/hooks/useVehicleFeed'
import {
  clearIf,
  clearSelection,
  getSelection,
  select,
  useRadarOpen,
} from '@/lib/selectionStore'
import { VehicleRadarButton } from './VehicleRadarButton'
import { VehicleRadarPanel } from './VehicleRadarPanel'

/*
 * El radar conectado al store de selección.
 *
 * Abrir el radar es una selección más: cierra la ficha de un incidente o de un
 * sismo, y tocar algo en el mapa lo cierra a él. Esa exclusión antes vivía en
 * `App` (un `useState`, dos manejadores y un `useEffect`) y cada apertura
 * repintaba la aplicación entera. Acá se leen sólo el booleano: el botón y el
 * panel son lo único que se repinta.
 */

interface RadarProps {
  feed: VehicleFeedState
  /** El botón que abre el panel; al cerrarlo, el foco vuelve ahí. */
  buttonRef: RefObject<HTMLButtonElement | null>
}

function toggleRadar(): void {
  if (getSelection().kind === 'radar') clearSelection()
  else select({ kind: 'radar' })
}

export const RadarToggle = memo(function RadarToggle({ feed, buttonRef }: RadarProps) {
  const open = useRadarOpen()
  return <VehicleRadarButton ref={buttonRef} feed={feed} open={open} onToggle={toggleRadar} />
})

export const RadarPanelHost = memo(function RadarPanelHost({ feed, buttonRef }: RadarProps) {
  const open = useRadarOpen()

  // Cierre pedido por la persona (Escape, ✕, velo): el foco vuelve al botón que
  // lo abrió, o quien navega con teclado queda en ninguna parte.
  const close = useCallback(() => {
    clearIf('radar')
    buttonRef.current?.focus()
  }, [buttonRef])

  return open ? <VehicleRadarPanel feed={feed} onClose={close} /> : null
})
