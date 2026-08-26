// @vitest-environment node
/**
 * Cohesión del sistema de diseño, y la frontera que lo hace posible.
 *
 * El barrido de cromo a tokens tiene un modo de falla caro: tokenizar por error
 * un color de DATOS. El naranja de `confirmed` o el rojo de Chilquinta no son
 * preferencias estéticas — afirman algo sobre la evidencia y sobre quién repone
 * el suministro. Si un rediseño los mueve, el mapa cambia lo que dice.
 *
 * Estos tests fijan las dos mitades del trato:
 *
 *   1. `src/components/` no vuelve a la paleta cruda ni a variantes `dark:`.
 *   2. `src/domain/` conserva sus hex intactos y NO adopta tokens de cromo.
 */

import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function readAll(dir: string, filter: (n: string) => boolean): [string, string][] {
  const base = resolve(process.cwd(), dir)
  const out: [string, string][] = []
  for (const entry of readdirSync(base, { withFileTypes: true, recursive: true })) {
    if (!entry.isFile() || !filter(entry.name)) continue
    const path = resolve(entry.parentPath ?? base, entry.name)
    out.push([entry.name, readFileSync(path, 'utf8')])
  }
  return out
}

const COMPONENTS = readAll(
  'src/components',
  (n) => n.endsWith('.tsx') && !n.includes('.test.'),
)
const DOMAIN = readAll('src/domain', (n) => n.endsWith('.ts') && !n.includes('.test.'))

describe('cromo: sólo tokens', () => {
  it('ningún componente usa la paleta cruda de slate', () => {
    const ofensores = COMPONENTS.flatMap(([name, src]) => {
      const hits = src.match(/\b(bg|text|border|ring|divide)-slate-\d+/g) ?? []
      return hits.map((h) => `${name}: ${h}`)
    })
    expect(ofensores).toEqual([])
  })

  it('ningún componente resuelve el modo oscuro a mano', () => {
    // Con `@theme inline` el token se redefine en `html.dark`. Una clase
    // `dark:` sobre cromo significa que alguien eligió un color en vez de
    // usar el token, y ese color quedará fuera del próximo cambio de tema.
    const ofensores = COMPONENTS.flatMap(([name, src]) => {
      const hits = src.match(/dark:[a-z-]*(?:slate-\d+|white)/g) ?? []
      return hits.map((h) => `${name}: ${h}`)
    })
    expect(ofensores).toEqual([])
  })

  it('los radios salen de los tres roles', () => {
    const ofensores = COMPONENTS.flatMap(([name, src]) => {
      const hits = src.match(/rounded-(sm|md|lg|xl|2xl|3xl)\b/g) ?? []
      return hits.map((h) => `${name}: ${h}`)
    })
    expect(ofensores).toEqual([])
  })
})

describe('datos: intactos', () => {
  it('la paleta de datos conserva sus hex y no adopta tokens', () => {
    for (const [name, src] of DOMAIN) {
      if (!/Symbology\.ts$/.test(name)) continue
      // Un token de cromo dentro de la simbología significaría que el color de
      // un dato pasó a depender del tema, que es justo lo que no puede pasar.
      expect(`${name}: ${src.match(/var\(--(?:surface|ink|accent|urgent)[a-z-]*\)/)?.[0] ?? 'sin tokens'}`)
        .toBe(`${name}: sin tokens`)
    }
  })

  it('los hex de emergencia siguen siendo literales en TypeScript', () => {
    const sym = DOMAIN.find(([n]) => n === 'symbology.ts')?.[1] ?? ''
    const traffic = DOMAIN.find(([n]) => n === 'trafficSymbology.ts')?.[1] ?? ''
    // MapLibre necesita valores de JavaScript: no puede leer una variable CSS
    // dentro de una expresión de estilo.
    expect(sym).toContain('#ea580c')
    expect(traffic).toContain('#6b21a8')
  })
})

describe('piezas que NO son superficie', () => {
  it('el pulgar del interruptor sigue siendo blanco literal', () => {
    const src = readFileSync(
      resolve(process.cwd(), 'src/components/ui/primitives/Switch.tsx'),
      'utf8',
    )
    // Con un token de superficie quedaría gris pizarra sobre riel gris pizarra
    // y desaparecería en modo oscuro. Es contraste físico, no cromo.
    // Se mira la cadena de clases, no el archivo entero: el comentario que
    // explica la decisión menciona `bg-raised` y haría fallar una búsqueda
    // ingenua sobre todo el fuente.
    const thumbClasses = /'absolute left-0\.5 top-0\.5[^']*'/.exec(src)?.[0] ?? ''
    expect(thumbClasses).toContain('bg-white')
    expect(thumbClasses).not.toContain('bg-raised')
  })
})
