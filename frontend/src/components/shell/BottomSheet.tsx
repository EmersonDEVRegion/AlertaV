import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent, ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { useSelection } from '@/lib/selectionStore'
import { getSheetSnap, setSheetSnap, useSheetSnap, type SheetSnap } from '@/lib/sheetStore'
import { ExplorePanel, type ExplorePanelProps } from './ExplorePanel'

/**
 * La hoja inferior del teléfono: el patrón de Apple Maps, Google Maps y Watch Duty.
 *
 * Tres alturas (ver `lib/sheetStore`): asomada con el resumen y las pestañas,
 * a media pantalla con la lista o la ficha, y completa.
 *
 * # Por qué `height` y no `translate`
 *
 * La primera versión medía siempre el alto completo y se deslizaba con
 * `translate`. Se animaba barato, pero a media pantalla la mitad de la lista
 * quedaba bajo el borde: el área con scroll creía medir 764 px y se veían 361.
 * En el iPhone el scroll «se acababa» antes del final del historial (medido en
 * producción a 430 × 932: 403 px inalcanzables). Ahora la hoja mide lo que se
 * ve, así que su lista también. Animar `height` recalcula el layout de unas
 * decenas de filas por cuadro, y eso un teléfono lo hace sin esfuerzo.
 *
 * # Los gestos
 *
 * - **Encabezado** (asa, resumen): arrastrar mueve la hoja; un toque alterna
 *   entre asomada y media.
 * - **Lista**, como en Apple Maps: a media pantalla, empujar hacia arriba
 *   primero abre la hoja completa; con la lista arriba de todo, tirar hacia
 *   abajo la baja. En el resto de los casos la lista se desplaza sola, con la
 *   inercia nativa. Esos listeners van con `passive: false`, porque para tomar
 *   el gesto hay que cancelar el scroll del navegador.
 */

/** Alto de la hoja asomada. Lo repite `index.css` (controles del mapa). */
const SHEET_PEEK_REM = 7.25

/** Alto por posición, relativo al `main` que la contiene. */
const HEIGHT: Record<SheetSnap, string> = {
  peek: `${SHEET_PEEK_REM}rem`,
  half: '54%',
  full: 'calc(100% - 0.5rem)',
}

/** Más rápido que esto (px/ms) es un gesto, no una posición. */
const FLICK = 0.45
/** Lo que tiene que moverse el dedo antes de que la lista ceda el gesto. */
const SLOP = 6

const ORDER: readonly SheetSnap[] = ['full', 'half', 'peek']

const SNAP_LABEL: Record<SheetSnap, string> = {
  peek: 'Expandir la lista',
  half: 'Expandir la lista a pantalla completa',
  full: 'Contraer la lista',
}

interface Drag {
  startY: number
  startH: number
  lastY: number
  lastT: number
  /** Velocidad del dedo, en px/ms; positiva hacia abajo. */
  v: number
}

export const BottomSheet = memo(function BottomSheet({
  headerAction,
  ...props
}: ExplorePanelProps & { headerAction?: ReactNode }) {
  const snap = useSheetSnap()
  const sheet = useRef<HTMLDivElement>(null)
  /*
   * La lista en estado y no en un ref: se desmonta cuando se abre una ficha y
   * vuelve a montarse al cerrarla, y los listeners tienen que ir al elemento
   * nuevo.
   */
  const [listEl, setListEl] = useState<HTMLDivElement | null>(null)
  const drag = useRef<Drag | null>(null)

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

  /** Las tres alturas en píxeles, contra el `main` de hoy (gira el teléfono). */
  const heights = useCallback((): Record<SheetSnap, number> => {
    const host = sheet.current?.parentElement?.clientHeight || window.innerHeight
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
    return { peek: SHEET_PEEK_REM * rem, half: host * 0.54, full: host - 0.5 * rem }
  }, [])

  const begin = useCallback((y: number, t: number) => {
    const node = sheet.current
    if (!node) return
    drag.current = { startY: y, startH: node.offsetHeight, lastY: y, lastT: t, v: 0 }
    node.style.transition = 'none'
  }, [])

  const move = useCallback(
    (y: number, t: number) => {
      const state = drag.current
      const node = sheet.current
      if (!state || !node) return
      state.v = (y - state.lastY) / Math.max(t - state.lastT, 1)
      state.lastY = y
      state.lastT = t
      const at = heights()
      const h = Math.min(Math.max(state.startH - (y - state.startY), at.peek), at.full)
      node.style.height = `${h}px`
    },
    [heights],
  )

  /**
   * Suelta la hoja en la altura más cercana, o en la siguiente si fue un
   * gesto rápido. La altura final se escribe también a mano: si termina donde
   * empezó, React no vuelve a aplicar el estilo (para él no cambió nada) y la
   * hoja se quedaría donde la soltó el dedo.
   */
  const end = useCallback(
    (y: number, { tapToggles }: { tapToggles: boolean }) => {
      const state = drag.current
      const node = sheet.current
      drag.current = null
      if (!state || !node) return
      node.style.transition = ''
      const settle = (next: SheetSnap) => {
        node.style.height = HEIGHT[next]
        setSheetSnap(next)
      }
      const moved = y - state.startY
      if (Math.abs(moved) < SLOP) {
        settle(tapToggles ? (getSheetSnap() === 'peek' ? 'half' : 'peek') : getSheetSnap())
        return
      }
      const at = heights()
      const h = state.startH - moved
      let next = ORDER.reduce((best, key) =>
        Math.abs(at[key] - h) < Math.abs(at[best] - h) ? key : best,
      )
      if (Math.abs(state.v) > FLICK) {
        const from = ORDER.indexOf(getSheetSnap())
        next = ORDER[Math.min(Math.max(from + (state.v > 0 ? 1 : -1), 0), ORDER.length - 1)]!
      }
      settle(next)
    },
    [heights],
  )

  // --- Encabezado: arrastre con puntero (dedo o mouse) ---------------------
  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      // Las pestañas y los botones del encabezado se tocan, no se arrastran.
      if ((event.target as HTMLElement).closest('button, [role="tab"], a, input')) return
      if (event.pointerType === 'mouse' && event.button !== 0) return
      begin(event.clientY, event.timeStamp)
      event.currentTarget.setPointerCapture?.(event.pointerId)
    },
    [begin],
  )
  const onPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => move(event.clientY, event.timeStamp),
    [move],
  )
  const onPointerUp = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => end(event.clientY, { tapToggles: true }),
    [end],
  )

  // --- Lista: el gesto de Apple Maps ---------------------------------------
  useEffect(() => {
    const el = listEl
    if (!el) return
    let start: { y: number; scrollTop: number; snap: SheetSnap } | null = null
    let taking = false
    let lastY = 0

    const onStart = (event: TouchEvent) => {
      if (event.touches.length !== 1 || drag.current) return
      const y = event.touches[0]!.clientY
      start = { y, scrollTop: el.scrollTop, snap: getSheetSnap() }
      taking = false
      lastY = y
    }
    const onMove = (event: TouchEvent) => {
      if (!start || event.touches.length !== 1) return
      const y = event.touches[0]!.clientY
      lastY = y
      if (!taking) {
        const dy = y - start.y
        // A media pantalla, empujar hacia arriba abre la hoja antes de desplazar.
        const expand = start.snap === 'half' && dy < -SLOP
        // Con la lista arriba de todo, tirar hacia abajo baja la hoja.
        const collapse =
          start.snap !== 'peek' && start.scrollTop <= 0 && el.scrollTop <= 0 && dy > SLOP
        if (!expand && !collapse) return
        taking = true
        begin(start.y, event.timeStamp)
      }
      event.preventDefault()
      move(y, event.timeStamp)
    }
    const onEnd = () => {
      if (taking) end(lastY, { tapToggles: false })
      start = null
      taking = false
    }

    el.addEventListener('touchstart', onStart, { passive: true })
    el.addEventListener('touchmove', onMove, { passive: false })
    el.addEventListener('touchend', onEnd)
    el.addEventListener('touchcancel', onEnd)
    return () => {
      el.removeEventListener('touchstart', onStart)
      el.removeEventListener('touchmove', onMove)
      el.removeEventListener('touchend', onEnd)
      el.removeEventListener('touchcancel', onEnd)
    }
  }, [listEl, begin, move, end])

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
        // Anclada abajo y con alto propio: nada de `top`, o el alto no manda.
        'pointer-events-auto absolute inset-x-0 bottom-0 z-20 flex flex-col',
        'rounded-t-[var(--radius-surface)] bg-raised shadow-[var(--shadow-raised)]',
        'transition-[height] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]',
        'motion-reduce:transition-none',
      )}
      style={{ height: HEIGHT[snap] }}
    >
      <ExplorePanel
        {...props}
        grip={grip}
        headerProps={headerProps}
        onInteract={expandIfPeek}
        headerAction={headerAction}
        scrollRef={setListEl}
      />
    </div>
  )
})
