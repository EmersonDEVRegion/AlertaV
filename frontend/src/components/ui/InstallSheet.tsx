import { useEffect, useId, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Button } from '@/components/ui/primitives'
import {
  promptInstall,
  setInstallSheetOpen,
  useInstallPlatform,
  useInstallSheetOpen,
  type InstallPlatform,
} from '@/lib/install'
import { cn } from '@/lib/cn'

/**
 * La hoja «Instalar AlertaV» (§Q): los pasos de este dispositivo, con dibujos
 * simples en vez de capturas (las capturas quedan viejas con cada iOS), y una
 * fila para ver los de otro dispositivo.
 *
 * Se monta una vez (en el menú «⋯») y se abre desde el menú o la invitación
 * con `setInstallSheetOpen`.
 */
export function InstallSheetHost() {
  const open = useInstallSheetOpen()
  return open ? <InstallSheet onClose={() => setInstallSheetOpen(false)} /> : null
}

type Device = 'iphone' | 'android' | 'pc' | 'mac'

const DEVICES: readonly { key: Device; label: string }[] = [
  { key: 'iphone', label: 'iPhone' },
  { key: 'android', label: 'Android' },
  { key: 'pc', label: 'Computador' },
  { key: 'mac', label: 'Mac' },
]

function deviceOf(platform: InstallPlatform): Device {
  switch (platform) {
    case 'ios-safari':
    case 'ios-other':
      return 'iphone'
    case 'android':
    case 'android-firefox':
      return 'android'
    case 'mac-safari':
      return 'mac'
    default:
      return 'pc'
  }
}

export function InstallSheet({ onClose }: { onClose: () => void }) {
  const platform = useInstallPlatform()
  const own = deviceOf(platform)
  const [device, setDevice] = useState<Device>(own)
  const titleId = useId()
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    closeRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [onClose])

  // Se instaló mientras la hoja estaba abierta (o desde el diálogo nativo).
  useEffect(() => {
    if (platform === 'installed') onClose()
  }, [platform, onClose])

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-scrim p-0 backdrop-blur-sm sm:items-center sm:p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex max-h-[92dvh] w-full flex-col overflow-hidden rounded-t-2xl bg-raised shadow-2xl ring-1 ring-line sm:max-w-md sm:rounded-surface"
      >
        <header className="flex items-start gap-3 px-5 pb-3 pt-4">
          <img src="/icons/pwa-192.png" alt="" className="size-11 shrink-0 rounded-[10px]" />
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-base font-bold text-ink">
              Instalar AlertaV
            </h2>
            <p className="mt-0.5 text-xs leading-snug text-ink-muted">
              Se abre en un toque, a pantalla completa, y te avisa de emergencias cerca.
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Cerrar"
            className="-mr-1 grid size-9 shrink-0 place-items-center rounded-full text-ink-faint transition hover:bg-sunken hover:text-ink-muted"
          >
            <span aria-hidden className="text-lg leading-none">✕</span>
          </button>
        </header>

        <div className="overflow-y-auto overscroll-contain px-5 pb-5">
          {device === own ? <OwnSteps platform={platform} /> : <Steps device={device} />}

          <div className="mt-5 border-t border-line pt-3">
            <p className="text-[11px] font-medium text-ink-muted">¿En otro dispositivo?</p>
            <div role="radiogroup" aria-label="Dispositivo" className="mt-1.5 flex rounded-control bg-sunken p-0.5">
              {DEVICES.map((d) => (
                <button
                  key={d.key}
                  type="button"
                  role="radio"
                  aria-checked={device === d.key}
                  onClick={() => setDevice(d.key)}
                  className={cn(
                    'flex h-7 flex-1 items-center justify-center rounded-[calc(var(--radius-control)-2px)] text-[11px] font-medium transition',
                    device === d.key ? 'bg-raised text-ink shadow-sm' : 'text-ink-muted hover:text-ink',
                  )}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}

/** Lo de este navegador, que puede no ser una lista de pasos. */
function OwnSteps({ platform }: { platform: InstallPlatform }) {
  const [busy, setBusy] = useState(false)
  switch (platform) {
    case 'prompt':
      return (
        <div className="rounded-control bg-sunken p-3">
          <p className="text-xs leading-snug text-ink">
            Tu navegador puede instalarla directamente.
          </p>
          <Button
            variant="urgent"
            size="md"
            className="mt-2.5 w-full"
            disabled={busy}
            onClick={() => {
              setBusy(true)
              void promptInstall().finally(() => setBusy(false))
            }}
          >
            Instalar
          </Button>
        </div>
      )
    case 'ios-other':
      return (
        <StepList>
          <Step icon={<ShareGlyph />}>
            Toca <b>Compartir</b> en la barra de direcciones.
          </Step>
          <Step icon={<AddGlyph />}>
            Toca <b>Agregar a pantalla de inicio</b>.
          </Step>
          <Step>
            Toca <b>Agregar</b>.
          </Step>
        </StepList>
      )
    case 'android-firefox':
      return (
        <StepList>
          <Step icon={<DotsGlyph vertical />}>
            Toca el menú <b>⋮</b>.
          </Step>
          <Step icon={<AddGlyph />}>
            Toca <b>Instalar</b> (o <b>Agregar a pantalla de inicio</b>).
          </Step>
        </StepList>
      )
    case 'firefox-desktop':
      return (
        <Note>
          Firefox no instala apps web. Abre <b>alertav.vercel.app</b> en Chrome o Edge y
          instálala desde ahí.
        </Note>
      )
    case 'in-app':
      return (
        <Note>
          Estás dentro de otra app (WhatsApp, Instagram…), que no permite instalar. Toca{' '}
          <b>⋯</b> y <b>Abrir en el navegador</b> (Safari o Chrome), y sigue desde ahí.
        </Note>
      )
    case 'installed':
      return <Note>AlertaV ya está instalada en este dispositivo.</Note>
    default:
      return <Steps device={deviceOf(platform)} />
  }
}

/** Los pasos de cada tipo de dispositivo, con su navegador de siempre. */
function Steps({ device }: { device: Device }) {
  switch (device) {
    case 'iphone':
      return (
        <StepList title="Safari">
          <Step icon={<DotsGlyph />}>
            Toca <b>⋯</b> junto a la barra de direcciones. (En iOS 18 o anterior, el botón{' '}
            <b>Compartir</b> está directo en la barra.)
          </Step>
          <Step icon={<ShareGlyph />}>
            Toca <b>Compartir</b>.
          </Step>
          <Step icon={<AddGlyph />}>
            Desliza y toca <b>Agregar a pantalla de inicio</b>.
          </Step>
          <Step>
            Deja encendido <b>Abrir como app web</b> y toca <b>Agregar</b>.
          </Step>
        </StepList>
      )
    case 'android':
      return (
        <StepList title="Chrome">
          <Step icon={<DotsGlyph vertical />}>
            Toca el menú <b>⋮</b>, arriba a la derecha.
          </Step>
          <Step icon={<AddGlyph />}>
            Toca <b>Instalar app</b> (o <b>Agregar a pantalla principal</b>).
          </Step>
          <Step>
            Confirma con <b>Instalar</b>.
          </Step>
        </StepList>
      )
    case 'mac':
      return (
        <StepList title="Safari (macOS Sonoma o más nuevo)">
          <Step>
            En el menú <b>Archivo</b>, elige <b>Agregar al Dock</b>.
          </Step>
          <Step>
            Toca <b>Agregar</b>. AlertaV queda en el Dock y en Aplicaciones.
          </Step>
        </StepList>
      )
    default:
      return (
        <StepList title="Chrome o Edge">
          <Step icon={<InstallGlyph />}>
            En la barra de direcciones, haz clic en el ícono <b>Instalar</b>.
          </Step>
          <Step>
            Si no aparece: en Chrome, menú <b>⋮</b> → <b>Transmitir, guardar y compartir</b> →{' '}
            <b>Instalar página como app</b>; en Edge, menú <b>…</b> → <b>Aplicaciones</b> →{' '}
            <b>Instalar este sitio como una aplicación</b>.
          </Step>
          <Step>
            Confirma con <b>Instalar</b>. Queda en el menú Inicio y puedes anclarla a la barra de
            tareas.
          </Step>
        </StepList>
      )
  }
}

function StepList({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div>
      {title && (
        <p className="mb-2 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
          {title}
        </p>
      )}
      <ol className="space-y-2.5 [counter-reset:paso]">{children}</ol>
    </div>
  )
}

function Step({ icon, children }: { icon?: ReactNode; children: ReactNode }) {
  return (
    <li className="flex items-start gap-3 text-[13px] leading-snug text-ink [counter-increment:paso]">
      <span
        aria-hidden
        className="grid size-6 shrink-0 place-items-center rounded-full bg-accent-soft text-[11px] font-bold text-accent before:content-[counter(paso)]"
      />
      <span className="min-w-0 flex-1 pt-0.5">{children}</span>
      {icon && (
        <span
          aria-hidden
          className="grid size-8 shrink-0 place-items-center rounded-control bg-sunken text-ink-muted ring-1 ring-line"
        >
          {icon}
        </span>
      )}
    </li>
  )
}

function Note({ children }: { children: ReactNode }) {
  return <p className="rounded-control bg-sunken p-3 text-xs leading-snug text-ink">{children}</p>
}

/* Dibujos de los botones del sistema, para reconocerlos de un vistazo. */

const glyph = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  className: 'size-4',
}

function ShareGlyph() {
  return (
    <svg {...glyph}>
      <path d="M12 3v12" />
      <path d="m8 7 4-4 4 4" />
      <path d="M6 11H5a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-8a1 1 0 0 0-1-1h-1" />
    </svg>
  )
}

function AddGlyph() {
  return (
    <svg {...glyph}>
      <rect x="4" y="4" width="16" height="16" rx="3" />
      <path d="M12 8v8M8 12h8" />
    </svg>
  )
}

function DotsGlyph({ vertical = false }: { vertical?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="size-4">
      {(vertical ? [5, 12, 19].map((y) => [12, y]) : [5, 12, 19].map((x) => [x, 12])).map(
        ([cx, cy]) => (
          <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="1.8" />
        ),
      )}
    </svg>
  )
}

function InstallGlyph() {
  return (
    <svg {...glyph}>
      <rect x="3" y="4" width="18" height="13" rx="2" />
      <path d="M12 8v5M9.5 10.5 12 13l2.5-2.5" />
      <path d="M8 21h8" />
    </svg>
  )
}
