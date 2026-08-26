// @vitest-environment node
/**
 * Geometría del interruptor.
 *
 * Los tres síntomas reportados —pulgar fuera del riel, "apagado" a la derecha,
 * y el pulgar invadiendo la etiqueta— tenían una sola causa: el pulgar estaba
 * en `absolute` sin `left`, así que heredaba la posición estática CENTRADA que
 * los navegadores le dan al contenido de un `<button>`.
 *
 * Este test lee las clases reales y comprueba que el pulgar quepa en el riel en
 * ambos estados. Es aritmética, no píxeles, pero es exactamente la aritmética
 * que estaba mal.
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const SOURCE = readFileSync(
  resolve(process.cwd(), 'src/components/ui/LayerToggles.tsx'),
  'utf8',
)

/** Tailwind: 1 unidad = 0,25 rem = 4 px. */
const px = (units: number) => units * 4

/**
 * Devuelve `NaN` en vez de lanzar.
 *
 * Si lanzara, una regresión en las clases haría que el archivo entero fallara
 * al cargarse y vitest reportaría «no tests» con un error de módulo. Eso
 * detecta el problema pero no dice cuál es. Con `NaN`, cada `it` falla con su
 * propio nombre y se lee de inmediato qué se rompió.
 */
function unit(pattern: RegExp): number {
  const m = pattern.exec(SOURCE)
  return m ? Number(m[1]) : Number.NaN
}

describe('interruptor de capas de referencia', () => {
  const railW = px(unit(/'relative h-4 w-(\d+) shrink-0 rounded-full/))
  const railH = px(unit(/'relative h-(\d+) w-\d+ shrink-0 rounded-full/))
  const thumb = px(unit(/absolute left-0\.5 top-0\.5 size-(\d+) rounded-full/))
  const inset = px(0.5)
  const onShift = px(unit(/checked \? 'translate-x-(\d+)' : 'translate-x-0'/))

  it('las clases del riel y del pulgar siguen siendo legibles', () => {
    // Guardia de la propia prueba: si esto falla, los números de abajo son NaN
    // y sus fallos no significarían nada.
    expect(Number.isNaN(railW)).toBe(false)
    expect(Number.isNaN(railH)).toBe(false)
    expect(Number.isNaN(thumb)).toBe(false)
    expect(Number.isNaN(onShift)).toBe(false)
  })

  it('ancla el pulgar con `left`, sin depender de la posición estática', () => {
    // La regresión que rompía todo: `absolute` sin `left` dentro de un <button>
    // centrado. Si vuelve, este test cae.
    expect(SOURCE).toContain('absolute left-0.5 top-0.5')
  })

  it('apagado deja el pulgar pegado al borde izquierdo', () => {
    expect(SOURCE).toContain("'translate-x-0'")
    expect(inset).toBeGreaterThan(0)
    expect(inset).toBeLessThan(railW / 2)
  })

  it('encendido mueve el pulgar hacia la DERECHA y sin desbordar', () => {
    const leftOff = inset
    const leftOn = inset + onShift

    expect(leftOn).toBeGreaterThan(leftOff)
    // El borde derecho del pulgar tiene que caber en el riel.
    expect(leftOn + thumb).toBeLessThanOrEqual(railW)
    // Y con margen simétrico: 2 px a cada lado.
    expect(railW - (leftOn + thumb)).toBeCloseTo(inset, 5)
  })

  it('el pulgar cabe verticalmente en el riel', () => {
    expect(inset + thumb).toBeLessThanOrEqual(railH)
  })

  it('no invade el espacio de la etiqueta', () => {
    // `gap-2` = 8 px entre el riel y el texto. Si el pulgar se sale del riel,
    // se come ese espacio y toca la etiqueta.
    const overflow = Math.max(0, inset + onShift + thumb - railW)
    expect(overflow).toBe(0)
  })

  it('da un área táctil mayor que el riel visible', () => {
    // 16 px de alto es un objetivo hostil en un teléfono; el pseudo-elemento
    // extiende la zona clicable sin mover el layout.
    expect(SOURCE).toContain('before:-inset-1.5')
  })
})

describe('altura del panel', () => {
  it('acota con `dvh`, no con `vh`', () => {
    // En móvil la barra del navegador se retrae y `vh` conserva el valor de la
    // ventana expandida: el panel se cortaría fuera del área visible.
    expect(SOURCE).toMatch(/max-h-\[calc\(100dvh-[\d.]+rem\)\]/)
    expect(SOURCE).not.toMatch(/max-h-\[calc\(100vh-/)
  })

  it('pone el scroll en el panel y no anida barras en las listas', () => {
    expect(SOURCE).toContain('overflow-y-auto overscroll-contain')
    // Las listas del acordeón ya no tienen su propio scroll.
    expect(SOURCE).not.toContain('max-h-56 space-y-0.5 overflow-y-auto')
  })
})
