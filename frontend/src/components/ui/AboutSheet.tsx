import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { CONTACT_EMAIL, REPO_URL, SOURCE_GROUPS } from '@/domain/sources'
import { cn } from '@/lib/cn'

/**
 * Acerca de, Fuentes y Privacidad: lo que un sitio público le debe a quien lo
 * usa. Un solo diálogo con tres pestañas, abierto desde el menú «⋯».
 *
 * La privacidad describe lo que el código hace hoy (ver `services/ciudadanos.py`
 * y `models/push.py` en el backend). Si cambia lo que se guarda, cambia esto.
 */

export type AboutTab = 'about' | 'sources' | 'privacy'

const TABS: readonly { key: AboutTab; label: string }[] = [
  { key: 'about', label: 'Acerca de' },
  { key: 'sources', label: 'Fuentes' },
  { key: 'privacy', label: 'Privacidad' },
]

interface AboutSheetProps {
  initialTab?: AboutTab
  onClose: () => void
}

function ExternalLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="font-semibold text-accent underline-offset-2 hover:underline"
    >
      {children}
    </a>
  )
}

function H({ children }: { children: React.ReactNode }) {
  return <h3 className="mt-5 text-[11px] font-bold uppercase tracking-wide text-ink-faint first:mt-0">{children}</h3>
}

function P({ children }: { children: React.ReactNode }) {
  return <p className="mt-2 text-[13px] leading-relaxed text-ink-muted">{children}</p>
}

function About() {
  return (
    <>
      <div className="rounded-control bg-danger-bg px-3 py-2.5 text-[13px] font-semibold leading-snug text-danger-ink">
        AlertaV no reemplaza a los servicios de emergencia. Si estás en peligro, llama
        al <a href="tel:132" className="underline">132</a> (Bomberos),{' '}
        <a href="tel:131" className="underline">131</a> (SAMU) o{' '}
        <a href="tel:133" className="underline">133</a> (Carabineros), y sigue las
        instrucciones de SENAPRED.
      </div>
      <H>Qué es</H>
      <P>
        Un mapa de las emergencias de la Región de Valparaíso: incendios, rescates,
        accidentes, sismos, alertas, cortes de ruta y servicios interrumpidos. Junta
        fuentes oficiales, satélites, prensa y reportes de vecinos, y cuando varias
        hablan de lo mismo las une en un solo incidente con un nivel de confianza.
      </P>
      <H>Qué no es</H>
      <P>
        No es un canal oficial. Una detección satelital o un reporte ciudadano no es
        un incendio confirmado, y la app siempre dice de dónde viene cada dato y
        cuánto se confía en él. Puede haber atrasos o errores de las fuentes.
      </P>
      <H>Quiénes lo hacen</H>
      <P>
        Emerson y Claude (Anthropic). Es un proyecto de código abierto, sin fines de
        lucro y de uso público, posible gracias a todas las instituciones y sitios
        que publican la información que aquí se cruza.
      </P>
      <H>Código</H>
      <P>
        Licencia <ExternalLink href="https://www.gnu.org/licenses/agpl-3.0.html">AGPL-3.0</ExternalLink>.
        El código está en <ExternalLink href={REPO_URL}>GitHub</ExternalLink>.
      </P>
      <H>Contacto</H>
      <P>
        Sugerencias, errores o solicitudes de una fuente:{' '}
        <ExternalLink href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</ExternalLink>.
        Si administras una de las fuentes y no quieres aparecer, escríbenos y la
        retiramos de inmediato.
      </P>
    </>
  )
}

function Sources() {
  return (
    <>
      <P>
        Cada dato del mapa dice de qué fuente viene. Estas son todas, con lo que se
        toma de cada una. Las marcas y los datos son de sus dueños.
      </P>
      {SOURCE_GROUPS.map((group) => (
        <section key={group.title}>
          <H>{group.title}</H>
          <ul className="mt-1.5 space-y-2">
            {group.sources.map((source) => (
              <li key={source.name} className="text-[13px] leading-snug">
                <ExternalLink href={source.url}>{source.name}</ExternalLink>
                <span className="block text-ink-muted">{source.what}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  )
}

function Privacy() {
  return (
    <>
      <P>
        AlertaV no tiene cuentas, no pide tu nombre y no usa cookies de publicidad
        ni analítica. Esto es todo lo que se guarda:
      </P>
      <H>En tu teléfono o navegador</H>
      <P>
        El tema claro u oscuro, las secciones que plegaste, tus lugares guardados y un
        identificador al azar para tus reportes. No salen de tu dispositivo salvo lo
        que se indica abajo.
      </P>
      <H>Si activas los avisos</H>
      <P>
        Guardamos la dirección de avisos que entrega tu navegador, tu ubicación
        aproximada, los radios que elegiste y tus lugares guardados (nombre y punto),
        para saber qué avisarte. La ubicación se pone al día mientras usas la app,
        reemplaza a la anterior (no hay historial) y deja de usarse si pasa un día
        sin abrirla. Al desactivar los avisos se borra todo. Si tu
        navegador deja de recibirlos, se borran solos. El registro de avisos enviados
        se guarda 30 días.
      </P>
      <H>Si reportas una emergencia</H>
      <P>
        Guardamos el punto, la categoría y el texto. No guardamos tu IP: sólo dos
        huellas cifradas (de tu dispositivo y de tu red) que sirven para contar
        vecinos distintos y no permiten saber quién eres. El texto no se publica
        hasta que lo revisa un moderador automático (Gemini, de Google) o una
        persona, y se rechazan textos con teléfonos, correos, RUT o enlaces. Para
        frenar bots se puede usar Cloudflare Turnstile.
      </P>
      <H>Proveedores</H>
      <P>
        La web está en Vercel y el servidor en Render; como todo servicio web,
        registran direcciones IP por un tiempo limitado. El mapa se descarga de
        CARTO y OpenStreetMap.
      </P>
      <H>Tus derechos</H>
      <P>
        Puedes pedir que borremos un reporte tuyo o cualquier dato escribiendo a{' '}
        <ExternalLink href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</ExternalLink>.
      </P>
    </>
  )
}

export function AboutSheet({ initialTab = 'about', onClose }: AboutSheetProps) {
  const [tab, setTab] = useState<AboutTab>(initialTab)
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
        <header className="flex items-center gap-3 border-b border-line px-5 pb-2 pt-4">
          <h2 id={titleId} className="flex-1 text-base font-bold text-ink">
            AlertaV
          </h2>
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
        <div role="tablist" aria-label="Información de AlertaV" className="flex gap-1 border-b border-line px-3 py-1.5">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              onClick={() => setTab(t.key)}
              className={cn(
                'flex-1 rounded-control px-2 py-1.5 text-xs font-semibold transition-colors',
                tab === t.key ? 'bg-sunken text-ink' : 'text-ink-muted hover:bg-hover',
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div role="tabpanel" className="overflow-y-auto overscroll-contain px-5 pb-6 pt-4">
          {tab === 'about' && <About />}
          {tab === 'sources' && <Sources />}
          {tab === 'privacy' && <Privacy />}
        </div>
      </div>
    </div>,
    document.body,
  )
}
