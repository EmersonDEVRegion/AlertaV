import { memo, useCallback, useEffect, useMemo, useRef } from 'react'
import type { PointerEvent as ReactPointerEvent, ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { useSelection } from '@/lib/selectionStore'
import { getSheetSnap, setSheetSnap, useSheetSnap, type SheetSnap } from '@/lib/sheetStore'
import { ExplorePanel, type ExplorePanelProps } from './ExplorePanel'

/**
 * La hoja inferior del teléfono: el patrón de Google Maps y Watch Duty.
 *
 * Tres alturas (ver `lib/sheetStore`): asomada con el resumen y las pestañas,
 * a media pantalla con la lista o la ficha, y completa. Se arrastra desde el
 * encabezado; un toque en el asa alterna entre asomada y media.
 *
 * # Por qué `translate` y no `height`
 *
 * La hoja mide siempre el alto completo y se desliza. Animar `height` obliga
 * a recalcular el layout de toda la lista en cada cuadro; `translate` lo
 * resuelve el compositor. Los porcentajes de `translate` se refieren al alto
 * de la propia hoja, así que las tres posiciones son CSS puro.
 *
 * # Lo que desplaza
 *
 * El borde inferior del teléfono era de tres cosas —el botón de reporte, la
 * ficha y la atribución— y arriba había una barra de fichas. Ahora arriba sólo
 * quedan la barra de la app y los controles del mapa, y la ficha y el botón
 * de reporte viven dentro de la hoja.
 */

/** Alto de la hoja asomada. Lo repiten el botón de reporte y `index.css`. */
const SHEET_PEEK_REM = 7.25

const TRANSLATE: Record<SheetSnap, string> = {
  full: '0px',
  half: '46%',
  peek: `calc(100% - ${SHEET_PEEK_REM}rem)`,
}

/** Más rápido que esto (px/ms) es un gesto, no una posición. */
const FLICK = 0.45

const SNAP_LABEL: Record<SheetSnap, string> = {
  peek: 'Expandir la lista',
  half: 'Expandir la lista a pantalla completa',
  full: 'Contraer la lista',
}

export const BottomSheet = memo(function BottomSheet({
  headerAction,
  ...props
}: ExplorePanelProps & { headerAction?: ReactNode }) {
  const snap = useSheetSnap()
  const sheet = useRef<HTMLDivElement>(null)
  const drag = useRef<{ startY: number; startPx: number; lastY: number; lastT: number; v: number } | null>(
    null,
  )

  /*
   * Abrir una ficha desde el mapa sube la hoja a media pantalla: la ficha vive
   * adentro, y asomada no se vería. Cerrarla no la baja: quien estaba leyendo
   * el historial vuelve a donde estaba.
   */
  const selection = useSelection()
  useEffect(() => {
    const opensDetail =
      selection.kind === 'incident' || selection.kind === 'seismic' || selection.kind === 'water'
    if (opensDetail && getSheetSnap() === 'peek') setSheetSnap('half')
  }, [selection])

  const positions = useCallback((): Record<SheetSnap, number> => {
    const height = sheet.current?.offsetHeight ?? window.innerHeight
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
    return { full: 0, half: height * 0.46, peek: height - SHEET_PEEK_REM * rem }
  }, [])

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      // Las pestañas y los botones del encabezado se tocan, no se arrastran.
      if ((event.target as HTMLElement).closest('button, [role="tab"], a, input')) return
      if (event.pointerType === 'mouse' && event.button !== 0) return
      const node = sheet.current
      if (!node) return
      const startPx = positions()[getSheetSnap()]
      drag.current = {
        startY: event.clientY,
        startPx,
        lastY: event.clientY,
        lastT: event.timeStamp,
        v: 0,
      }
      node.style.transition = 'none'
      event.currentTarget.setPointerCapture(event.pointerId)
    },
    [positions],
  )

  const onPointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const state = drag.current
    const node = sheet.current
    if (!state || !node) return
    const dt = Math.max(event.timeStamp - state.lastT, 1)
    state.v = (event.clientY - state.lastY) / dt
    state.lastY = event.clientY
    state.lastT = event.timeStamp
    const px = Math.max(0, state.startPx + (event.clientY - state.startY))
    node.style.translate = `0 ${px}px`
  }, [])

  const onPointerUp = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const state = drag.current
      const node = sheet.current
      drag.current = null
      if (!state || !node) return
      node.style.transition = ''
      const moved = event.clientY - state.startY
      /*
       * La posición final se escribe también a mano: si el gesto termina en la
       * misma altura en que empezó, React no vuelve a aplicar el estilo (para
       * él no cambió nada) y la hoja se quedaría donde la soltó el dedo.
       */
      const settle = (next: SheetSnap) => {
        node.style.translate = `0 ${TRANSLATE[next]}`
        setSheetSnap(next)
      }

      // Un toque (sin arrastre) alterna entre asomada y media pantalla.
      if (Math.abs(moved) < 6) {
        settle(getSheetSnap() === 'peek' ? 'half' : 'peek')
        return
      }

      const order: SheetSnap[] = ['full', 'half', 'peek']
      const at = positions()
      const current = state.startPx + moved
      let next = order.reduce((best, key) =>
        Math.abs(at[key] - current) < Math.abs(at[best] - current) ? key : best,
      )
      // Un gesto rápido avanza una posición en su dirección aunque no llegue.
      if (Math.abs(state.v) > FLICK) {
        const from = order.indexOf(getSheetSnap())
        const step = state.v > 0 ? 1 : -1
        next = order[Math.min(Math.max(from + step, 0), order.length - 1)]!
      }
      settle(next)
    },
    [positions],
  )

  const cycle = useCallback(() => {
    const current = getSheetSnap()
    setSheetSnap(current === 'peek' ? 'half' : current === 'half' ? 'full' : 'peek')
  }, [])

  const expandIfPeek = useCallback(() => {
    if (getSheetSnap() === 'peek') setSheetSnap('half')
  }, [])

  const headerProps = useMemo(
    () => ({
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel: onPointerUp,
      // `touch-action: none` sólo en el encabezado: la lista de abajo tiene
      // que seguir desplazándose con el dedo.
      className: 'cursor-grab touch-none select-none active:cursor-grabbing',
    }),
    [onPointerDown, onPointerMove, onPointerUp],
  )

  const grip = useMemo(
    () => (
      // El asa se arrastra (lo maneja el encabezado) y, con teclado, alterna las
      // tres alturas. No es un <button>: un botón se tocaría, no se arrastraría.
      <div
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            cycle()
          }
        }}
        aria-label={SNAP_LABEL[snap]}
        aria-expanded={snap !== 'peek'}
        className="flex h-5 w-full items-center justify-center focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      >
        <span aria-hidden className="h-1 w-10 rounded-full bg-line-strong" />
      </div>
    ),
    [cycle, snap],
  )

  return (
    <div
      ref={sheet}
      data-snap={snap}
      role="complementary"
      aria-label="Emergencias e historial"
      className={cn(
        'pointer-events-auto absolute inset-x-0 bottom-0 top-2 z-20 flex flex-col',
        'rounded-t-[var(--radius-surface)] bg-raised shadow-[var(--shadow-raised)]',
        'transition-[translate] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] will-change-transform',
        'motion-reduce:transition-none',
      )}
      style={{ translate: `0 ${TRANSLATE[snap]}` }}
    >
      <ExplorePanel
        {...props}
        grip={grip}
        headerProps={headerProps}
        onInteract={expandIfPeek}
        headerAction={headerAction}
      />
    </div>
  )
})
