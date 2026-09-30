import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { Theme } from '@/hooks/useTheme'
import { Switch } from '@/components/ui/primitives'
import { setExploreTab, type ExploreTab } from '@/lib/exploreStore'
import { setSheetSnap } from '@/lib/sheetStore'
import { cn } from '@/lib/cn'

/**
 * Menú «⋯» de la barra: lo que se usa una vez y no merece un botón propio.
 *
 * El tema vivía como botón suelto en la barra; «Qué significan los colores»
 * no existía fuera de la pestaña Leyenda, que en el teléfono nadie encontraba.
 */
interface AppMenuProps {
  theme: Theme
  onToggleTheme: () => void
}

function Item({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className="flex w-full items-center gap-2.5 rounded-control px-2 py-2 text-left text-xs font-medium text-ink transition-colors hover:bg-hover"
    >
      {children}
    </button>
  )
}

export function AppMenu({ theme, onToggleTheme }: AppMenuProps) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const show = (tab: ExploreTab) => {
    setExploreTab(tab)
    setSheetSnap('half')
    setOpen(false)
  }

  return (
    <div ref={root} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Más opciones"
        className={cn(
          'grid size-8 place-items-center rounded-full bg-chrome-raised text-ink-on-chrome',
          'transition-[scale] duration-150 active:scale-[0.94]',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
        )}
      >
        <svg viewBox="0 0 24 24" aria-hidden className="size-4" fill="currentColor">
          <circle cx="5" cy="12" r="1.8" />
          <circle cx="12" cy="12" r="1.8" />
          <circle cx="19" cy="12" r="1.8" />
        </svg>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Más opciones"
          className="animate-rise absolute right-0 top-[calc(100%+0.5rem)] z-30 w-60 rounded-surface bg-raised p-1 shadow-[var(--shadow-raised)] ring-1 ring-line"
        >
          <div className="flex items-center gap-2.5 px-2 py-2">
            <span className="flex-1 text-xs font-medium text-ink">Modo oscuro</span>
            <Switch
              checked={theme === 'dark'}
              onCheckedChange={onToggleTheme}
              label="Modo oscuro"
              accentColor="var(--accent)"
            />
          </div>
          <div className="my-1 h-px bg-line" />
          <Item onClick={() => show('legend')}>Qué significan los colores</Item>
          <Item onClick={() => show('layers')}>Capas y filtros</Item>
          <div className="my-1 h-px bg-line" />
          <p className="px-2 pb-1.5 pt-1 text-[10px] leading-snug text-ink-faint">
            Fuentes oficiales (CONAF, Bomberos, SENAPRED, CSN, distribuidoras y Esval),
            satélites, prensa y reportes ciudadanos, cruzados por el motor de AlertaV.
          </p>
        </div>
      )}
    </div>
  )
}
