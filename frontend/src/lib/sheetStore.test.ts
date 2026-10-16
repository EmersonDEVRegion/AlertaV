import { afterEach, describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import {
  getSheetSnap,
  lowerSheet,
  releaseSheet,
  resetSheet,
  setSheetSnap,
  useLowerSheetWhile,
} from './sheetStore'

afterEach(() => resetSheet())

describe('bajar la hoja mientras un panel de la barra está abierto (§T)', () => {
  it('baja a asomada y vuelve a la altura que tenía', () => {
    setSheetSnap('half')
    lowerSheet('avisos')
    expect(getSheetSnap()).toBe('peek')
    releaseSheet('avisos')
    expect(getSheetSnap()).toBe('half')
  })

  it('con dos paneles abiertos vuelve sólo al cerrar el último', () => {
    setSheetSnap('full')
    lowerSheet('menu')
    lowerSheet('clima')
    releaseSheet('menu')
    expect(getSheetSnap()).toBe('peek')
    releaseSheet('clima')
    expect(getSheetSnap()).toBe('full')
  })

  it('si alguien la movió entremedio, no la devuelve', () => {
    setSheetSnap('half')
    lowerSheet('menu')
    // «Ver leyenda» del menú, un arrastre o una ficha que se abre.
    setSheetSnap('full')
    releaseSheet('menu')
    expect(getSheetSnap()).toBe('full')
  })

  it('soltar sin haber bajado no hace nada', () => {
    setSheetSnap('half')
    releaseSheet('avisos')
    expect(getSheetSnap()).toBe('half')
  })

  it('el hook la baja al abrir y la devuelve al cerrar', () => {
    setSheetSnap('half')
    const { rerender } = renderHook(({ open }) => useLowerSheetWhile(open, 'avisos'), {
      initialProps: { open: false },
    })
    expect(getSheetSnap()).toBe('half')
    rerender({ open: true })
    expect(getSheetSnap()).toBe('peek')
    act(() => rerender({ open: false }))
    expect(getSheetSnap()).toBe('half')
  })
})
