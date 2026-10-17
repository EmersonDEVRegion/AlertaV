import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { Theme } from '@/hooks/useTheme'
import { Switch } from '@/components/ui/primitives'
import { GLASS_LABEL, GLASS_LEVELS, setGlassLevel, useGlassLevel } from '@/lib/glassStore'
import { setExploreTab, type ExploreTab } from '@/lib/exploreStore'
import { setSheetSnap, useLowerSheetWhile } from '@/lib/sheetStore'
import { cn } from '@/lib/cn'
import type { AboutTab } from '@/components/ui/AboutSheet'
import { InstallSheetHost } from '@/components/ui/InstallSheet'
import { startInstall, useInstallPlatform } from '@/lib/install'

const AboutSheet = lazy(() =>
  import('@/components/ui/AboutSheet').then((m) => ({ default: m.AboutSheet })),
)

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
  const [about, setAbout] = useState<AboutTab | null>(null)
  const root = useRef<HTMLDivElement>(null)
  // «Instalar AlertaV» sólo mientras no esté instalada (§Q).
  const installed = useInstallPlatform() === 'installed'
  // En el teléfono la hoja baja mientras el menú está abierto. «Ver
  // leyenda» la sube a media altura, y eso manda: no se le devuelve la de antes.
  useLowerSheetWhile(open, 'menu')

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

  const showAbout = (tab: AboutTab) => {
    setAbout(tab)
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
          className="header-popover animate-rise absolute right-0 top-[calc(100%+0.5rem)] z-30 w-60 overflow-y-auto rounded-surface bg-raised p-1 shadow-[var(--shadow-raised)] ring-1 ring-line"
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
          <GlassPicker />
          <div className="my-1 h-px bg-line" />
          <Item onClick={() => show('layers')}>Capas, filtros y leyenda</Item>
          {!installed && (
            <Item
              onClick={() => {
                setOpen(false)
                void startInstall()
              }}
            >
              Instalar AlertaV
            </Item>
          )}
          <div className="my-1 h-px bg-line" />
          <Item onClick={() => showAbout('about')}>Acerca de AlertaV</Item>
          <Item onClick={() => showAbout('sources')}>Fuentes de datos</Item>
          <Item onClick={() => showAbout('privacy')}>Privacidad</Item>
          <div className="my-1 h-px bg-line" />
          <p className="px-2 pb-1.5 pt-1 text-[10px] leading-snug text-ink-faint">
            No reemplaza al 132, 131, 133 ni a SENAPRED. Código abierto, sin fines de lucro.
          </p>
        </div>
      )}

      {/* La hoja de instalación: la abren este menú y la invitación de «Ahora». */}
      <InstallSheetHost />

      {about && (
        <Suspense fallback={null}>
          <AboutSheet initialTab={about} onClose={() => setAbout(null)} />
        </Suspense>
      )}
    </div>
  )
}

/**
 * Cuánto cristal (§D): tres niveles en un control segmentado. «Equilibrado»
 * por defecto; «Sólido» quita la transparencia para quien prefiera contraste.
 */
function GlassPicker() {
  const level = useGlassLevel()
  return (
    <div className="px-2 pb-1.5 pt-1">
      <p id="glass-picker-label" className="text-xs font-medium text-ink">
        Transparencia
      </p>
      <div
        role="radiogroup"
        aria-labelledby="glass-picker-label"
        className="mt-1.5 flex rounded-control bg-sunken p-0.5"
      >
        {GLASS_LEVELS.map((option) => (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={level === option}
            onClick={() => setGlassLevel(option)}
            className={cn(
              'flex h-7 flex-1 items-center justify-center rounded-[calc(var(--radius-control)-2px)] text-[11px] font-medium transition',
              level === option ? 'bg-raised text-ink shadow-sm' : 'text-ink-muted hover:text-ink',
            )}
          >
            {GLASS_LABEL[option]}
          </button>
        ))}
      </div>
    </div>
  )
}

