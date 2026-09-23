import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Button, Switch } from '@/components/ui/primitives'
import {
  usePushNotifications,
  type PushNotificationsState,
} from '@/hooks/usePushNotifications'
import { formatRelative } from '@/lib/format'
import { dismissInvite, inviteDismissed } from '@/lib/push'
import { cn } from '@/lib/cn'

/**
 * Avisos push: la campana de la barra superior y su panel.
 *
 * # Por qué en la barra y no en el panel de capas
 *
 * Los avisos no son una capa del mapa: siguen funcionando con la app cerrada.
 * Ponerlos junto a los interruptores de capas sugeriría que apagarlos esconde
 * algo del mapa, y que encenderlos muestra algo. Van en la barra, al lado del
 * tema, que es la otra preferencia del teléfono y no del mapa.
 *
 * # La invitación
 *
 * Una sola vez, pasados unos segundos de uso, aparece una burbuja bajo la
 * campana. No se piden permisos al cargar —Chrome y Safari castigan a los sitios
 * que lo hacen, y una persona que llega por un incendio no quiere un diálogo
 * antes de ver el mapa—: la burbuja sólo ofrece, y el permiso se pide cuando la
 * persona toca «Activar». «Ahora no» la retira para siempre; la campana queda.
 */

const INVITE_DELAY_MS = 20_000

export const PUSH_TEXT = {
  title: 'Avisos en este teléfono',
  on: 'Activos',
  off: 'Desactivados',
  enable: 'Activar avisos',
  disable: 'Desactivar avisos',
  incidents: 'Emergencias cercanas',
  seismic: 'Temblores',
  probe: 'Enviar aviso de prueba',
  refresh: 'Actualizar ubicación',
  inviteTitle: '¿Te avisamos de emergencias cerca de ti?',
  inviteLater: 'Ahora no',
  denied:
    'Bloqueaste las notificaciones de AlertaV. Para activarlas, habilítalas en el candado de la barra de direcciones (o en Ajustes → Notificaciones del teléfono) y vuelve a esta pantalla.',
  iosInstall:
    'En iPhone los avisos sólo funcionan con AlertaV instalada: toca Compartir → «Agregar a pantalla de inicio» y abre la app desde el ícono.',
  seismicCaveat:
    'Los temblores llegan unos minutos después del movimiento: no es una alerta temprana. Si estás en la costa y el sismo te impide mantenerte en pie, evacúa a una zona segura sin esperar ningún aviso.',
  locationNote:
    'Usamos la última ubicación que la app conoce: se actualiza cada vez que la abres. La guardamos redondeada (unos 100 m) y la borramos si desactivas los avisos.',
} as const

function km(meters: number): string {
  return `${Math.round(meters / 100) / 10}`.replace('.', ',') + ' km'
}

function magnitude(value: number): string {
  return value.toFixed(1).replace('.', ',')
}

function BellGlyph({ active, className }: { active: boolean; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
    >
      <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
      {!active && <path d="M3 3l18 18" opacity="0.55" />}
    </svg>
  )
}

function Bullet({ children }: { children: ReactNode }) {
  return (
    <li className="flex gap-1.5">
      <span aria-hidden className="mt-[5px] size-1 shrink-0 rounded-full bg-ink-faint" />
      <span>{children}</span>
    </li>
  )
}

/** Qué se avisa, con los umbrales que declara el servidor. */
function WhatWeSend({ push }: { push: PushNotificationsState }) {
  const radius = push.server?.incident_radius_m ?? 5000
  const sources = push.server?.incident_min_sources ?? 2
  const minMagnitude = push.server?.seismic_min_magnitude ?? 3.5
  return (
    <ul className="mt-2 space-y-1.5 text-[11px] leading-snug text-ink-muted">
      <Bullet>
        <span className="font-semibold text-ink">Emergencias a menos de {km(radius)}</span>:
        incendios, accidentes, cortes de luz y otras, con su distancia. Cuando lo confirma
        CONAF o Bomberos, cuando la distribuidora informa el corte, o cuando al menos{' '}
        {sources} fuentes distintas coinciden.
      </Bullet>
      <Bullet>
        <span className="font-semibold text-ink">Temblores</span> de magnitud{' '}
        {magnitude(minMagnitude)} o más que probablemente se sintieron donde estás.
      </Bullet>
    </ul>
  )
}

function stepLabel(step: PushNotificationsState['step']): string {
  switch (step) {
    case 'permission':
      return 'Esperando el permiso de notificaciones…'
    case 'location':
      return 'Obteniendo tu ubicación…'
    case 'subscribing':
      return 'Registrando este teléfono…'
    case 'unsubscribing':
      return 'Desactivando…'
    case 'saving':
      return 'Guardando…'
    default:
      return 'Un momento…'
  }
}

function PrimaryButton({
  children,
  onClick,
  disabled,
}: {
  children: ReactNode
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'inline-flex h-9 w-full items-center justify-center rounded-control px-3',
        'bg-accent text-sm font-semibold text-accent-ink',
        'transition-[filter,scale] duration-150 hover:brightness-110 active:scale-[0.97]',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
        'focus-visible:outline-accent disabled:pointer-events-none disabled:opacity-50',
      )}
    >
      {children}
    </button>
  )
}

export function NotificationPanel({ push }: { push: PushNotificationsState }) {
  const { phase } = push
  const paused = Boolean(push.server && !push.server.enabled && push.server.public_key)

  return (
    <div className="w-[19rem] max-w-[calc(100vw-1.5rem)] p-3">
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className={cn(
            'grid size-8 shrink-0 place-items-center rounded-control',
            phase === 'on' ? 'bg-accent-soft text-accent' : 'bg-sunken text-ink-faint',
          )}
        >
          <BellGlyph active={phase === 'on'} className="size-[18px]" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold leading-tight text-ink">{PUSH_TEXT.title}</p>
          <p className="mt-0.5 text-[10.5px] leading-tight text-ink-muted">
            {phase === 'on' ? PUSH_TEXT.on : phase === 'working' ? stepLabel(push.step) : PUSH_TEXT.off}
          </p>
        </div>
      </div>

      {phase === 'checking' && (
        <p className="mt-2.5 text-[11px] text-ink-muted">Consultando…</p>
      )}

      {phase === 'unavailable' && (
        <p className="mt-2.5 text-[11px] leading-snug text-ink-muted">{push.message}</p>
      )}

      {phase === 'ios-install' && (
        <>
          <WhatWeSend push={push} />
          <p className="mt-2.5 rounded-control bg-info-bg p-2 text-[11px] leading-snug text-info-ink">
            {PUSH_TEXT.iosInstall}
          </p>
        </>
      )}

      {phase === 'denied' && (
        <p className="mt-2.5 rounded-control bg-warn-bg p-2 text-[11px] leading-snug text-warn-ink">
          {PUSH_TEXT.denied}
        </p>
      )}

      {(phase === 'off' || phase === 'working') && (
        <>
          <WhatWeSend push={push} />
          <p className="mt-2 text-[10.5px] leading-snug text-ink-faint">{PUSH_TEXT.locationNote}</p>
          <div className="mt-2.5">
            <PrimaryButton onClick={() => void push.enable()} disabled={phase === 'working'}>
              {phase === 'working' ? stepLabel(push.step) : PUSH_TEXT.enable}
            </PrimaryButton>
          </div>
        </>
      )}

      {phase === 'on' && (
        <>
          <div className="mt-2.5 space-y-2 border-t border-line pt-2.5">
            <PreferenceRow
              label={PUSH_TEXT.incidents}
              hint={`A menos de ${km(push.server?.incident_radius_m ?? 5000)}`}
              checked={push.preferences.notifyIncidents}
              // No se puede apagar el último: para eso está «Desactivar», que
              // además borra la ubicación del servidor.
              disabled={push.step !== null || !push.preferences.notifySeismic}
              onChange={() =>
                void push.setPreferences({
                  ...push.preferences,
                  notifyIncidents: !push.preferences.notifyIncidents,
                })
              }
            />
            <PreferenceRow
              label={PUSH_TEXT.seismic}
              hint="Los que se sintieron donde estás"
              checked={push.preferences.notifySeismic}
              disabled={push.step !== null || !push.preferences.notifyIncidents}
              onChange={() =>
                void push.setPreferences({
                  ...push.preferences,
                  notifySeismic: !push.preferences.notifySeismic,
                })
              }
            />
          </div>

          <p className="mt-2.5 text-[10.5px] leading-snug text-ink-muted">
            {push.locationSyncedAt
              ? `Ubicación informada ${formatRelative(push.locationSyncedAt)}.`
              : 'Ubicación informada.'}{' '}
            <button
              type="button"
              onClick={() => void push.refreshLocation()}
              disabled={push.step !== null}
              className="font-semibold text-accent underline-offset-2 hover:underline disabled:opacity-50"
            >
              {push.step === 'location' ? 'Ubicando…' : PUSH_TEXT.refresh}
            </button>
          </p>

          <div className="mt-2.5 flex gap-2">
            <Button
              variant="subtle"
              size="sm"
              onClick={() => void push.sendProbe()}
              disabled={push.step !== null}
            >
              {PUSH_TEXT.probe}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void push.disable()}
              disabled={push.step !== null}
            >
              {PUSH_TEXT.disable}
            </Button>
          </div>

          {push.probeResult && (
            <p role="status" className="mt-1.5 text-[10.5px] leading-snug text-success-ink">
              {push.probeResult}
            </p>
          )}
        </>
      )}

      {paused && phase !== 'unavailable' && (
        <p className="mt-2 text-[10.5px] leading-snug text-warn-ink">
          Los avisos están pausados en el servidor. Tu suscripción se guarda y volverás a
          recibirlos cuando se reactiven.
        </p>
      )}

      {push.message && phase !== 'unavailable' && (
        <p role="alert" className="mt-2 text-[10.5px] leading-snug text-danger-ink">
          {push.message}
        </p>
      )}

      {phase !== 'unavailable' && (
        <p className="mt-2.5 border-t border-line pt-2 text-[9.5px] leading-snug text-ink-faint">
          {PUSH_TEXT.seismicCaveat}
        </p>
      )}
    </div>
  )
}

function PreferenceRow({
  label,
  hint,
  checked,
  disabled,
  onChange,
}: {
  label: string
  hint: string
  checked: boolean
  disabled: boolean
  onChange: () => void
}) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="min-w-0 flex-1">
        <span className="block text-[11px] font-semibold text-ink">{label}</span>
        <span className="block truncate text-[10px] leading-tight text-ink-muted">{hint}</span>
      </span>
      {/* El riel encendido no tiene color propio: cada capa trae el suyo. Acá
          no hay dato que codificar, así que va el acento del cromo. */}
      <Switch
        checked={checked}
        onCheckedChange={onChange}
        label={label}
        disabled={disabled}
        accentColor="var(--accent)"
      />
    </div>
  )
}

export function NotificationBell() {
  const push = usePushNotifications()
  const [open, setOpen] = useState(false)
  const [invite, setInvite] = useState(false)
  const root = useRef<HTMLDivElement>(null)

  // La invitación: una vez, a los 20 s, sólo si se puede activar y nadie la
  // descartó antes.
  useEffect(() => {
    if (push.phase !== 'off' || open || inviteDismissed()) return
    const timer = setTimeout(() => setInvite(true), INVITE_DELAY_MS)
    return () => clearTimeout(timer)
  }, [push.phase, open])

  useEffect(() => {
    if (push.phase === 'on' || push.phase === 'denied') setInvite(false)
  }, [push.phase])

  // Cierre por clic fuera y Escape, como el widget meteorológico.
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

  const active = push.phase === 'on'
  const closeInvite = () => {
    dismissInvite()
    setInvite(false)
  }

  return (
    <div ref={root} className="relative shrink-0">
      <button
        type="button"
        onClick={() => {
          setOpen((value) => !value)
          if (invite) closeInvite()
        }}
        aria-expanded={open}
        aria-label={`${PUSH_TEXT.title}: ${active ? PUSH_TEXT.on : PUSH_TEXT.off}`}
        title={PUSH_TEXT.title}
        className={cn(
          'relative grid size-8 shrink-0 place-items-center rounded-full',
          'bg-chrome-raised text-ink-on-chrome',
          'transition-[background-color,scale] duration-150 active:scale-[0.94]',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
          'focus-visible:outline-accent',
          !active && 'text-white/60',
        )}
      >
        <BellGlyph active={active} className="size-4" />
        {active && (
          <span
            aria-hidden
            className="absolute right-1.5 top-1.5 size-1.5 rounded-full bg-orange-400"
          />
        )}
      </button>

      {open && (
        <div
          role="region"
          aria-label={PUSH_TEXT.title}
          className={cn(
            'animate-rise absolute right-0 top-[calc(100%+0.5rem)] z-30',
            // Opaco, como el detalle meteorológico: cuelga de la barra oscura.
            'rounded-surface bg-raised shadow-[var(--shadow-raised)] ring-1 ring-line',
          )}
        >
          <NotificationPanel push={push} />
        </div>
      )}

      {invite && !open && (
        <div
          role="dialog"
          aria-label={PUSH_TEXT.inviteTitle}
          className={cn(
            'animate-rise absolute right-0 top-[calc(100%+0.5rem)] z-30 w-[16rem]',
            'max-w-[calc(100vw-1.5rem)] p-3',
            'rounded-surface bg-raised shadow-[var(--shadow-raised)] ring-1 ring-line',
          )}
        >
          <p className="text-xs font-semibold leading-snug text-ink">{PUSH_TEXT.inviteTitle}</p>
          <p className="mt-1 text-[11px] leading-snug text-ink-muted">
            Incendios, accidentes y cortes de luz a menos de{' '}
            {km(push.server?.incident_radius_m ?? 5000)}, y temblores que se sientan donde estás.
          </p>
          <div className="mt-2.5 flex gap-2">
            <PrimaryButton
              onClick={() => {
                closeInvite()
                setOpen(true)
                void push.enable()
              }}
            >
              Activar
            </PrimaryButton>
            <Button variant="ghost" size="md" onClick={closeInvite}>
              {PUSH_TEXT.inviteLater}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
