// @vitest-environment node
/**
 * iOS amplía la app entera al enfocar un campo con letra de menos de 16 px, y
 * no la devuelve al cerrar el teclado: pasó con el buscador de la barra en un
 * iPhone (la hoja quedó corrida y «Reportar» cortado).
 *
 * La regla: todo `<input>` de texto y todo `<textarea>` lleva
 * `pointer-coarse:text-base` (16 px sólo en pantallas táctiles; en el
 * computador conserva su tamaño). No se arregla con `maximum-scale=1`: eso le
 * quita a una persona con baja visión la posibilidad de ampliar el mapa.
 */
import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const base = resolve(process.cwd(), 'src/components')
const files = readdirSync(base, { withFileTypes: true, recursive: true })
  .filter((e) => e.isFile() && e.name.endsWith('.tsx') && !e.name.includes('.test.'))
  .map((e) => resolve(e.parentPath ?? base, e.name))

/** Los campos donde se escribe: sin `type` o con un tipo de texto. */
const TEXT_TYPES = /type="(text|search|email|tel|url|number|password)"/

describe('campos de texto en iPhone', () => {
  it('ninguno queda bajo 16 px en pantallas táctiles', () => {
    const offenders: string[] = []
    for (const file of files) {
      const src = readFileSync(file, 'utf8')
      for (const match of src.matchAll(/<(input|textarea)\b[\s\S]*?\/>/g)) {
        const tag = match[0]
        if (match[1] === 'input' && /type="/.test(tag) && !TEXT_TYPES.test(tag)) continue
        if (!tag.includes('pointer-coarse:text-base')) {
          offenders.push(`${file.slice(base.length + 1)}: ${tag.slice(0, 60).replace(/\s+/g, ' ')}`)
        }
      }
    }
    expect(offenders).toEqual([])
  })
})
