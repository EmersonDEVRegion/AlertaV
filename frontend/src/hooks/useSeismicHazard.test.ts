/**
 * Tests del ciclo de vida de la capa de amenaza.
 *
 * Lo que fijan es exactamente lo pedido: que el archivo no se pida al arrancar,
 * y que apagar y encender no lo vuelva a pedir. Como el montaje del `<Source>`
 * es lo que dispara la descarga, comprobar `hasMounted` equivale a comprobar el
 * tráfico de red — sin necesidad de un mapa real.
 */

import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useSeismicHazard } from './useSeismicHazard'

describe('useSeismicHazard', () => {
  it('no monta nada al arrancar: el archivo no se descarga', () => {
    const { result } = renderHook(() => useSeismicHazard())

    expect(result.current.enabled).toBe(false)
    expect(result.current.hasMounted).toBe(false)
    expect(result.current.status).toBe('idle')
  })

  it('monta la fuente al primer encendido y marca la carga', () => {
    const { result } = renderHook(() => useSeismicHazard())

    act(() => result.current.toggle())

    expect(result.current.enabled).toBe(true)
    expect(result.current.hasMounted).toBe(true)
    expect(result.current.status).toBe('loading')
  })

  it('mantiene la fuente montada al apagar: no se vuelve a descargar', () => {
    const { result } = renderHook(() => useSeismicHazard())

    act(() => result.current.toggle())
    act(() => result.current.onLoaded())
    act(() => result.current.toggle())

    // Apagada para el usuario…
    expect(result.current.enabled).toBe(false)
    // …pero la fuente sigue en el mapa, así que no hay descarga nueva.
    expect(result.current.hasMounted).toBe(true)
    expect(result.current.status).toBe('ready')
  })

  it('no vuelve a "loading" al reencender una capa ya cargada', () => {
    const { result } = renderHook(() => useSeismicHazard())

    act(() => result.current.toggle())
    act(() => result.current.onLoaded())
    act(() => result.current.toggle())
    act(() => result.current.toggle())

    // Un spinner acá mentiría: no hay nada que esperar.
    expect(result.current.status).toBe('ready')
    expect(result.current.enabled).toBe(true)
  })

  it('`hasMounted` es monótona: nunca vuelve a false', () => {
    const { result } = renderHook(() => useSeismicHazard())

    for (let i = 0; i < 6; i += 1) {
      act(() => result.current.toggle())
      expect(result.current.hasMounted).toBe(true)
    }
  })

  it('registra el error sin desmontar la capa', () => {
    const { result } = renderHook(() => useSeismicHazard())

    act(() => result.current.toggle())
    act(() => result.current.onError())

    expect(result.current.status).toBe('error')
    expect(result.current.hasMounted).toBe(true)
  })

  it('el reintento cambia `attempt` para forzar un remontaje', () => {
    const { result } = renderHook(() => useSeismicHazard())

    act(() => result.current.toggle())
    act(() => result.current.onError())
    const before = result.current.attempt

    act(() => result.current.retry())

    // `attempt` es la `key` del <Source>: cambiarla es la única forma de
    // repetir una descarga que falló.
    expect(result.current.attempt).toBe(before + 1)
    expect(result.current.status).toBe('loading')
    expect(result.current.enabled).toBe(true)
  })
})
