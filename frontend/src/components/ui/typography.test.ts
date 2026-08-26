// @vitest-environment node
/**
 * Base tipográfica.
 *
 * El caso que motiva casi todo esto: un contador que pasa de 9 a 10 y empuja lo
 * que tiene al lado. `tabular-nums` por sí solo NO lo arregla —iguala el ancho
 * ENTRE dígitos, pero 10 tiene un glifo más que 9—, así que hace falta además
 * reservar el ancho. Estos tests fijan las dos mitades.
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const CSS = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8')
const BADGE = readFileSync(
  resolve(process.cwd(), 'src/components/ui/primitives/Badge.tsx'),
  'utf8',
)

describe('cifras que no desplazan el layout', () => {
  it('activa cifras tabulares en toda la aplicación, no caso por caso', () => {
    expect(CSS).toContain("font-feature-settings: 'tnum' 1, 'lnum' 1")
    expect(CSS).toContain('font-variant-numeric: tabular-nums lining-nums')
  })

  it('reserva el ancho en `ch`, que es lo que evita el salto de 9 a 10', () => {
    const count = /\.count \{([^}]*)\}/.exec(CSS)?.[1] ?? ''
    // `ch` es el ancho del carácter «0»; con cifras tabulares equivale al de
    // cualquier dígito, así que `2ch` reserva exactamente dos sin depender del
    // tamaño de fuente.
    expect(count).toContain('min-width: 2ch')
    // Alineado a la derecha: el número crece hacia adentro de su propia caja.
    expect(count).toContain('text-align: right')
  })

  it('ofrece tres cifras para los contadores que pasan de 99', () => {
    expect(CSS).toMatch(/\.count-3 \{\s*min-width: 3ch;\s*\}/)
    // Un enjambre sísmico llega a tres dígitos; el resto de las capas no.
    expect(BADGE).toContain("three: 'count-3'")
  })

  it('el badge de contador aplica la clase, no reimplementa el ancho', () => {
    expect(BADGE).toContain("count: 'count ")
  })
})

describe('suavizado según el tema', () => {
  it('usa subpíxel en claro y escala de grises en oscuro', () => {
    // Texto oscuro sobre claro: el subpíxel usa los tres canales y da el trazo
    // más nítido. Texto claro sobre oscuro sufre halación y se ve más grueso,
    // así que `antialiased` lo adelgaza y compensa.
    expect(CSS).toMatch(/body \{[^}]*-webkit-font-smoothing: subpixel-antialiased/s)
    expect(CSS).toMatch(/html\.dark body \{[^}]*-webkit-font-smoothing: antialiased/s)
    expect(CSS).toMatch(/html\.dark body \{[^}]*-moz-osx-font-smoothing: grayscale/s)
  })

  it('no aplica el suavizado de oscuro en claro', () => {
    const light = /^body \{[^}]*-webkit-font-smoothing[^}]*\}/m.exec(CSS)?.[0] ?? ''
    expect(light).not.toContain('font-smoothing: antialiased')
  })
})

describe('pila de fuentes', () => {
  it('arranca por `system-ui` y no depende de ninguna descarga', () => {
    const stack = /--font-ui:\s*([^;]*);/s.exec(CSS)?.[1] ?? ''
    expect(stack.trim().startsWith('system-ui')).toBe(true)
    // Una fuente web obligaría a esperar una petición antes de que el texto sea
    // legible, y esta PWA se abre en la calle durante una emergencia.
    expect(CSS).not.toContain('@import url(')
    expect(CSS).not.toContain('fonts.googleapis.com')
  })

  it('incluye respaldo para Safari viejo y para Windows 11', () => {
    const stack = /--font-ui:\s*([^;]*);/s.exec(CSS)?.[1] ?? ''
    expect(stack).toContain('-apple-system')
    expect(stack).toContain('Segoe UI Variable')
  })
})
