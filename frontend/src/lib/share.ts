/**
 * Enlace compartible de un incidente.
 *
 * `/i/INC-…` lo responde el backend con las etiquetas Open Graph que leen
 * WhatsApp y compañía, y de ahí redirige a `/?incidente=INC-…` (ver
 * `backend/app/api/v1/endpoints/share.py`). Con la PWA instalada, el service
 * worker sirve la app directo en `/i/INC-…`: `parseDeepLink` también lee esa
 * ruta.
 */

export function shareUrl(code: string, origin = window.location.origin): string {
  return `${origin}/i/${encodeURIComponent(code)}`
}

export type ShareOutcome = 'shared' | 'copied' | 'cancelled' | 'failed'

interface ShareInput {
  code: string
  title: string
  text?: string
}

/**
 * Hoja nativa de compartir si existe (teléfonos); si no, copiar al
 * portapapeles. Que la persona cierre la hoja nativa no es un error.
 */
export async function shareIncident(
  { code, title, text }: ShareInput,
  nav: Pick<Navigator, 'share' | 'clipboard'> = navigator,
  origin?: string,
): Promise<ShareOutcome> {
  const url = shareUrl(code, origin)
  if (typeof nav.share === 'function') {
    try {
      await nav.share({ title, text, url })
      return 'shared'
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return 'cancelled'
      // Algunos navegadores de escritorio exponen `share` y fallan: se copia.
    }
  }
  try {
    await nav.clipboard.writeText(url)
    return 'copied'
  } catch {
    return 'failed'
  }
}
