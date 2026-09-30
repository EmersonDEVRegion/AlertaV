/**
 * Genera todos los íconos de la app desde `public/icons/favicon.svg`.
 *
 *   npm run icons
 *
 * Antes los PNG se hacían a mano y podían divergir del SVG (el acceso directo de
 * Windows mostraba el fondo del escritorio por las esquinas transparentes). Ahora
 * hay una sola fuente y estas reglas:
 *
 *   - `pwa-192`, `pwa-512` (purpose `any`) y `apple-touch-icon`: fondo a sangre,
 *     sin esquinas transparentes. Cada sistema recorta con su propia máscara;
 *     un PNG con transparencia deja ver lo que haya detrás.
 *   - `maskable-512`: fondo a sangre y la marca reducida para caber en la zona
 *     segura (un círculo del 80 % del lado).
 *   - `badge-96`: el badge de las notificaciones de Android. Sólo cuenta el
 *     canal alfa: la marca en blanco con la V calada (transparente).
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { Resvg } from '@resvg/resvg-js'

const here = dirname(fileURLToPath(import.meta.url))
const icons = resolve(here, '../public/icons')
// Sin comentarios: el encabezado de favicon.svg menciona `<g id="marca">`.
const source = readFileSync(resolve(icons, 'favicon.svg'), 'utf8').replace(/<!--[\s\S]*?-->/g, '')

const mark = source.match(/<g id="marca">([\s\S]*?)<\/g>/)?.[1]
if (!mark) throw new Error('favicon.svg no tiene <g id="marca">')
const BG = '#0f172a'

/** La marca sobre fondo a sangre, escalada alrededor del centro. */
function square(scale = 1) {
  const t = `translate(32 32) scale(${scale}) translate(-32 -32)`
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="${BG}"/><g transform="${t}">${mark}</g></svg>`
}

/** Badge: sólo alfa. Triángulo blanco y la V recortada con una máscara. */
function badge() {
  const [triangle, v] = mark.match(/<path[^>]*\/>/g) ?? []
  if (!triangle || !v) throw new Error('la marca debe tener el triángulo y la V')
  const white = triangle.replace(/#f59e0b/g, '#fff')
  const cut = v.replace(/#0f172a/g, '#000')
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><mask id="m"><rect width="64" height="64" fill="#fff"/>${cut}</mask></defs><g mask="url(#m)" transform="translate(32 32) scale(1.2) translate(-32 -31)">${white}</g></svg>`
}

function png(svg, size, file) {
  const out = new Resvg(svg, { fitTo: { mode: 'width', value: size } }).render().asPng()
  writeFileSync(resolve(icons, file), out)
  console.log(`${file.padEnd(22)} ${size}×${size}  ${out.length} B`)
}

png(square(), 192, 'pwa-192.png')
png(square(), 512, 'pwa-512.png')
png(square(), 180, 'apple-touch-icon.png')
// La esquina más alejada del triángulo queda a ~0,45 del lado desde el centro;
// la zona segura es un radio de 0,40. 0,82 la deja dentro con margen.
png(square(0.82), 512, 'maskable-512.png')
png(badge(), 96, 'badge-96.png')
