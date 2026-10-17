import { useState } from 'react'
import { Button } from '@/components/ui/primitives'
import {
  countVisit,
  dismissInstallInvite,
  inviteDismissed,
  shouldInvite,
  startInstall,
  useInstallPlatform,
} from '@/lib/install'

/** La visita se cuenta una vez, al cargar el módulo: los remontajes no suman. */
const visits = typeof window === 'undefined' ? 0 : countVisit()

/**
 * La invitación a instalar (§Q): una tarjeta delgada arriba de «Ahora».
 *
 * Una sola vez y desde la segunda visita. No flota sobre el mapa: está en la
 * lista, se va con el desplazamiento y «✕» la retira para siempre. El ítem
 * «Instalar AlertaV» del menú «⋯» queda para quien la cerró.
 */
export function InstallInvite() {
  const platform = useInstallPlatform()
  const [dismissed, setDismissed] = useState(inviteDismissed)

  if (!shouldInvite(platform, visits, dismissed)) return null

  return (
    <section
      aria-label="Instalar AlertaV"
      className="flex items-center gap-2.5 rounded-control bg-accent-soft px-2.5 py-2 ring-1 ring-line"
    >
      <img src="/icons/pwa-192.png" alt="" className="size-8 shrink-0 rounded-[8px]" />
      <p className="min-w-0 flex-1 text-[11.5px] leading-snug text-ink">
        <b>Instala AlertaV:</b> se abre en un toque y te avisa de emergencias cerca.
      </p>
      <Button variant="subtle" size="sm" className="shrink-0" onClick={() => void startInstall()}>
        Instalar
      </Button>
      <button
        type="button"
        aria-label="No mostrar más"
        onClick={() => {
          dismissInstallInvite()
          setDismissed(true)
        }}
        className="-mr-1 grid size-7 shrink-0 place-items-center rounded-full text-ink-faint transition hover:bg-hover hover:text-ink-muted"
      >
        <span aria-hidden className="text-sm leading-none">✕</span>
      </button>
    </section>
  )
}
