// @vitest-environment node
/**
 * La CSP de producción vive en `vercel.json` y ni jsdom ni el preview local la
 * aplican. Por eso el mapa de calor de lluvia (#17) pasó todos los tests y
 * nunca se vio en producción: `rasterizeRainGrid` entrega un `data:` y
 * MapLibre 6 lo descarga con `fetch()`, que la CSP bloqueaba porque
 * `connect-src` no traía `data:`.
 *
 * Este test lee la cabecera real y exige lo que el código necesita.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

interface VercelHeader {
  key: string
  value: string
}
interface VercelConfig {
  headers?: { source: string; headers: VercelHeader[] }[]
}

const config = JSON.parse(
  readFileSync(resolve(process.cwd(), 'vercel.json'), 'utf8'),
) as VercelConfig

function csp(): Map<string, string[]> {
  const value = (config.headers ?? [])
    .flatMap((h) => h.headers)
    .find((h) => h.key.toLowerCase() === 'content-security-policy')?.value
  if (!value) throw new Error('vercel.json no declara Content-Security-Policy')
  return new Map(
    value
      .split(';')
      .map((d) => d.trim().split(/\s+/))
      .filter((parts) => parts[0])
      .map(([name, ...sources]) => [name!, sources]),
  )
}

describe('CSP de producción', () => {
  it('connect-src permite data: (la imagen del campo de lluvia)', () => {
    expect(csp().get('connect-src')).toContain('data:')
  })

  it('img-src permite data: y blob: (íconos rasterizados de MapLibre)', () => {
    const img = csp().get('img-src')
    expect(img).toContain('data:')
    expect(img).toContain('blob:')
  })

  it('connect-src sigue sin comodines abiertos', () => {
    const connect = csp().get('connect-src') ?? []
    expect(connect).not.toContain('*')
    expect(connect).not.toContain('https:')
  })

  it('Turnstile puede cargar su script y su marco (reporte ciudadano)', () => {
    const turnstile = 'https://challenges.cloudflare.com'
    expect(csp().get('script-src')).toContain(turnstile)
    expect(csp().get('frame-src')).toContain(turnstile)
    // Sólo ese origen: nada de comodines en los scripts.
    expect(csp().get('script-src')).not.toContain('*')
    expect(csp().get('script-src')).not.toContain('https:')
  })
})
