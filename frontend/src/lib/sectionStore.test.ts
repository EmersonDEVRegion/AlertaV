import { afterEach, describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { resetSections, setSectionOpen, useSectionOpen } from './sectionStore'

afterEach(() => resetSections())

describe('secciones plegables', () => {
  it('sin nada guardado usa el valor por defecto de cada sección', () => {
    const { result } = renderHook(() => [
      useSectionOpen('historial:today', true)[0],
      useSectionOpen('historial:yesterday', false)[0],
    ])
    expect(result.current).toEqual([true, false])
  })

  it('alternar cambia la sección y lo guarda en el teléfono', () => {
    const { result } = renderHook(() => useSectionOpen('historial:yesterday', false))
    act(() => result.current[1]())
    expect(result.current[0]).toBe(true)
    expect(JSON.parse(localStorage.getItem('alertav:secciones')!)).toEqual({
      'historial:yesterday': true,
    })
  })

  it('lo guardado manda sobre el valor por defecto', () => {
    act(() => setSectionOpen('noticias', true))
    const { result } = renderHook(() => useSectionOpen('noticias', false))
    expect(result.current[0]).toBe(true)
  })
})
