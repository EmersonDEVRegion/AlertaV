import { useEffect, useRef } from 'react'

/**
 * Cloudflare Turnstile: la verificación anti-bots del reporte ciudadano.
 *
 * Gratis y, en modo `interaction-only`, invisible para casi todos: sólo muestra
 * una casilla si Cloudflare duda. Entrega un token de un solo uso que el backend
 * confirma (`app/services/turnstile.py`). Sin `VITE_TURNSTILE_SITE_KEY` este
 * componente no se monta y el backend, sin su secreto, no lo pide.
 *
 * El script se carga una sola vez, al abrir el formulario por primera vez, y
 * no antes: quien nunca reporta no descarga nada de Cloudflare.
 */

const SCRIPT_URL = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'

interface TurnstileOptions {
  sitekey: string
  action?: string
  appearance?: 'always' | 'execute' | 'interaction-only'
  language?: string
  theme?: 'auto' | 'light' | 'dark'
  callback?: (token: string) => void
  'expired-callback'?: () => void
  'error-callback'?: () => void
}

interface TurnstileApi {
  render: (container: HTMLElement, options: TurnstileOptions) => string
  remove: (widgetId: string) => void
}

declare global {
  interface Window {
    turnstile?: TurnstileApi
  }
}

let cargando: Promise<TurnstileApi> | null = null

function cargarTurnstile(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile)
  cargando ??= new Promise<TurnstileApi>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = SCRIPT_URL
    script.async = true
    script.defer = true
    script.onload = () =>
      window.turnstile ? resolve(window.turnstile) : reject(new Error('turnstile ausente'))
    script.onerror = () => {
      cargando = null
      reject(new Error('no se pudo cargar Turnstile'))
    }
    document.head.appendChild(script)
  })
  return cargando
}

interface TurnstileWidgetProps {
  siteKey: string
  /**
   * Token nuevo, o `null` cuando vence o falla. Tiene que ser estable (un
   * `setState`, por ejemplo): si cambia, el widget se rehace.
   */
  onToken: (token: string | null) => void
}

export function TurnstileWidget({ siteKey, onToken }: TurnstileWidgetProps) {
  const contenedor = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let widgetId: string | null = null
    let vivo = true

    cargarTurnstile()
      .then((api) => {
        if (!vivo || !contenedor.current) return
        widgetId = api.render(contenedor.current, {
          sitekey: siteKey,
          action: 'reporte',
          appearance: 'interaction-only',
          language: 'es',
          theme: 'auto',
          callback: (token) => onToken(token),
          'expired-callback': () => onToken(null),
          'error-callback': () => onToken(null),
        })
      })
      .catch(() => {
        // Sin Turnstile el backend responde 403 y el formulario lo explica. No
        // se bloquea acá: si Cloudflare no carga, el servidor decide.
        onToken(null)
      })

    return () => {
      vivo = false
      if (widgetId && window.turnstile) window.turnstile.remove(widgetId)
    }
  }, [siteKey, onToken])

  return <div ref={contenedor} className="mt-3 flex justify-center empty:hidden" />
}
