/**
 * Imágenes de la vista previa de los enlaces compartidos (`/i/INC-…`).
 *
 *   npm run og
 *
 * Una por tipo, fijas: el backend del plan gratuito no genera imágenes. El
 * nombre de cada archivo lo usa `backend/app/api/v1/endpoints/share.py`
 * (`TYPE_IMAGE`). 1200 × 630, el tamaño que piden WhatsApp, X y Facebook.
 */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { Resvg } from '@resvg/resvg-js'

const here = dirname(fileURLToPath(import.meta.url))
const pub = resolve(here, '../public')
const out = resolve(pub, 'og')
mkdirSync(out, { recursive: true })

const source = readFileSync(resolve(pub, 'icons/favicon.svg'), 'utf8').replace(/<!--[\s\S]*?-->/g, '')
const mark = source.match(/<g id="marca">([\s\S]*?)<\/g>/)?.[1]
if (!mark) throw new Error('favicon.svg no tiene <g id="marca">')

const BG = '#0f172a'
const FONT = "'Segoe UI', 'Helvetica Neue', Arial, sans-serif"

const KINDS = [
  { file: 'incendio', label: 'Incendio', color: '#ef4444' },
  { file: 'accidente', label: 'Accidente', color: '#f59e0b' },
  { file: 'rescate', label: 'Rescate', color: '#3b82f6' },
  { file: 'inundacion', label: 'Inundación o remoción', color: '#06b6d4' },
  { file: 'servicio', label: 'Servicio interrumpido', color: '#a78bfa' },
  { file: 'emergencia', label: 'Emergencia', color: '#f97316' },
]

function card({ label, color }) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="${BG}"/>
  <rect x="0" y="0" width="24" height="630" fill="${color}"/>
  <circle cx="1040" cy="150" r="300" fill="${color}" opacity="0.10"/>
  <circle cx="1040" cy="150" r="170" fill="${color}" opacity="0.16"/>
  <g transform="translate(96 84) scale(2.2)">${mark}</g>
  <text x="250" y="160" font-family="${FONT}" font-size="64" font-weight="700" fill="#ffffff">Alerta<tspan fill="#f97316">V</tspan></text>
  <circle cx="114" cy="372" r="18" fill="${color}"/>
  <text x="152" y="392" font-family="${FONT}" font-size="76" font-weight="800" fill="#ffffff">${label}</text>
  <text x="96" y="530" font-family="${FONT}" font-size="34" font-weight="500" fill="#94a3b8">Emergencias de la Región de Valparaíso</text>
  <text x="96" y="578" font-family="${FONT}" font-size="26" fill="#64748b">No reemplaza al 132, 131, 133 ni a SENAPRED</text>
</svg>`
}

for (const kind of KINDS) {
  const png = new Resvg(card(kind), {
    fitTo: { mode: 'width', value: 1200 },
    font: { loadSystemFonts: true, defaultFontFamily: 'Arial' },
  })
    .render()
    .asPng()
  writeFileSync(resolve(out, `${kind.file}.png`), png)
  console.log(`og/${kind.file}.png`.padEnd(22), `${png.length} B`)
}
