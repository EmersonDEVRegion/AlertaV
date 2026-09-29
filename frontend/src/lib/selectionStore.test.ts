import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import {
  clearIf,
  clearSelection,
  getSelection,
  resetSelection,
  select,
  selectIncident,
  selectSeismic,
  useRadarOpen,
  useSelectedIncidentCode,
  useSelectedSeismicId,
} from './selectionStore'

afterEach(() => resetSelection())

describe('selectionStore', () => {
  it('una sola cosa seleccionada a la vez', () => {
    selectIncident('INC-1')
    selectSeismic('us7000abcd')
    expect(getSelection()).toEqual({ kind: 'seismic', usgsId: 'us7000abcd' })

    select({ kind: 'radar' })
    expect(getSelection()).toEqual({ kind: 'radar' })
  })

  it('cada lector recibe un primitivo y sólo se repinta si cambia lo suyo', () => {
    let incidentRenders = 0
    let radarRenders = 0
    const incident = renderHook(() => {
      incidentRenders += 1
      return useSelectedIncidentCode()
    })
    const radar = renderHook(() => {
      radarRenders += 1
      return useRadarOpen()
    })
    const before = { incidentRenders, radarRenders }

    act(() => selectIncident('INC-7'))
    expect(incident.result.current).toBe('INC-7')
    expect(radar.result.current).toBe(false)
    // El radar sigue cerrado: su lector no tenía nada que repintar.
    expect(radarRenders).toBe(before.radarRenders)

    act(() => select({ kind: 'radar' }))
    expect(incident.result.current).toBeNull()
    expect(radar.result.current).toBe(true)
  })

  it('volver a seleccionar lo mismo no avisa a nadie', () => {
    let renders = 0
    renderHook(() => {
      renders += 1
      return useSelectedSeismicId()
    })
    act(() => selectSeismic('ci40'))
    const after = renders
    act(() => selectSeismic('ci40'))
    expect(renders).toBe(after)
  })

  it('clearIf sólo cierra lo que es de ese tipo', () => {
    selectIncident('INC-2')
    clearIf('radar')
    expect(getSelection()).toEqual({ kind: 'incident', code: 'INC-2' })
    clearIf('incident')
    expect(getSelection()).toEqual({ kind: 'none' })

    select({ kind: 'radar' })
    clearSelection()
    expect(getSelection().kind).toBe('none')
  })
})
