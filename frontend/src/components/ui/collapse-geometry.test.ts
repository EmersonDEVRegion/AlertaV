/**
 * Geometría del cierre.
 *
 * jsdom no calcula layout, así que en vez de medir píxeles se comprueba la
 * aritmética sobre las clases reales del componente: se leen del archivo fuente
 * y se verifica que el desplazamiento saque el panel COMPLETO de la pantalla.
 *
 * Existe por un modo de falla concreto y silencioso: alguien ensancha el panel
 * de `w-60` a `w-72` para que quepa una capa nueva, no toca el
 * `translate-x-[calc(100%+0.75rem)]`… y como el desplazamiento es porcentual
 * sobre el propio ancho, sigue funcionando. Pero si en cambio cambia el
 * `right-3` del contenedor y no el `0.75rem` del cálculo, el panel queda
 * asomando una franja por el borde. Este test ata esas dos constantes.
 */

// @vitest-environment node
// No necesita DOM: lee el archivo y hace aritmética.

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// Ruta desde la raíz del proyecto: `import.meta.url` apunta al módulo
// transformado por Vite, no a un archivo en disco.
const SOURCE = readFileSync(
  resolve(process.cwd(), 'src/components/ui/primitives/Sheet.tsx'),
  'utf8',
)

/** Escala de espaciado de Tailwind: 1 unidad = 0,25 rem. */
const rem = (units: number) => units * 0.25

function tailwindUnits(pattern: RegExp, label: string): number {
  const match = pattern.exec(SOURCE)
  if (!match) throw new Error(`no se encontró ${label} en LayerToggles.tsx`)
  return Number(match[1])
}

describe('geometría del panel colapsable', () => {
  const panelWidth = rem(tailwindUnits(/SHEET_WIDTH = 'w-(\d+)'/, 'el ancho del panel'))
  const containerInset = rem(
    tailwindUnits(/absolute right-(\d+) top-\[8\.5rem\]/, 'el inset del contenedor'),
  )
  const tabWidth = rem(tailwindUnits(/right-full top-3 grid h-12 w-(\d+)/, 'el ancho de la pestaña'))

  const offsetRem = (() => {
    const match = /translate-x-\[calc\(100%\+([\d.]+)rem\)\]/.exec(SOURCE)
    if (!match) throw new Error('no se encontró el desplazamiento de cierre')
    return Number(match[1])
  })()

  it('el desplazamiento cancela exactamente el inset del contenedor', () => {
    // Si difieren, el panel queda asomando o se pasa de largo.
    expect(offsetRem).toBe(containerInset)
  })

  it('deja el panel completamente fuera de la pantalla', () => {
    const viewport = 100 // borde derecho, en unidades arbitrarias de "rem desde 0"
    // Abierto: el borde derecho del panel queda a `inset` del borde de pantalla.
    const rightEdgeOpen = viewport - containerInset
    const leftEdgeOpen = rightEdgeOpen - panelWidth

    // Cerrado: se desplaza (ancho del panel + inset).
    const shift = panelWidth + offsetRem
    const leftEdgeClosed = leftEdgeOpen + shift

    expect(leftEdgeClosed).toBeGreaterThanOrEqual(viewport)
  })

  it('deja la pestaña asomando y no la empuja fuera con el panel', () => {
    const viewport = 100
    const leftEdgeClosed = viewport - containerInset - panelWidth + (panelWidth + offsetRem)
    // La pestaña va anclada con `right-full`: su borde derecho toca el borde
    // izquierdo del panel, así que asoma hacia adentro por su propio ancho.
    const tabLeftEdge = leftEdgeClosed - tabWidth

    expect(tabLeftEdge).toBeLessThan(viewport)
    expect(viewport - tabLeftEdge).toBeCloseTo(tabWidth, 5)
    // Y tiene que ser tocable: por debajo de ~24 px es un objetivo hostil.
    expect(tabWidth * 16).toBeGreaterThanOrEqual(24)
  })

  it('la pestaña sólo redondea el costado que queda a la vista', () => {
    // `rounded-l-surface` es el radio por rol del sistema de diseño: la
    // pestaña y el panel comparten curvatura porque son la misma pieza.
    expect(SOURCE).toContain('rounded-l-surface')
    expect(SOURCE).toContain('rounded-r-none')
  })

  it('anima sólo la transformación', () => {
    expect(SOURCE).toContain('transition-transform')
    expect(SOURCE).toContain('will-change-transform')
    // `transition-all` arrastraría layout en cada frame.
    expect(SOURCE).not.toContain('transition-all')
  })
})
