/**
 * El eje «Estado de alerta» de la ficha.
 *
 * SENAPRED alerta sobre incendios y fenómenos hidrometeorológicos, no sobre un
 * corte de luz o un choque. Y aun donde alerta, una barra vacía en cada ficha
 * sugiere una gravedad que nadie declaró: el eje se dibuja sólo con una alerta
 * vigente.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { Incident } from '@/api/types'
import { alertApplies } from '@/domain/families'
import { makeIncident } from '@/test/fixtures'
import { IncidentSheet } from './IncidentSheet'

vi.mock('@/hooks/useIncidentDetail', () => ({
  useIncidentDetail: () => ({ data: undefined, isLoading: false }),
}))

function montar(over: Partial<Incident>) {
  render(<IncidentSheet incident={makeIncident(over)} onClose={vi.fn()} />)
}

describe('alertApplies', () => {
  it('sólo incendios e hidrometeorológicos', () => {
    expect(alertApplies('wildfire')).toBe(true)
    expect(alertApplies('possible_fire')).toBe(true)
    expect(alertApplies('structural_fire')).toBe(true)
    expect(alertApplies('flood')).toBe(true)
    expect(alertApplies('landslide')).toBe(true)
    expect(alertApplies('power_outage')).toBe(false)
    expect(alertApplies('accident')).toBe(false)
    expect(alertApplies('rescue')).toBe(false)
    expect(alertApplies('other')).toBe(false)
  })
})

describe('estado de alerta en la ficha', () => {
  it('un corte de luz no muestra el eje de alerta', () => {
    montar({ type: 'power_outage' })
    expect(screen.queryByText('Estado de alerta')).not.toBeInTheDocument()
    expect(screen.queryByText(/Sin alerta de SENAPRED/)).not.toBeInTheDocument()
  })

  it('un accidente tampoco', () => {
    montar({ type: 'accident' })
    expect(screen.queryByText('Estado de alerta')).not.toBeInTheDocument()
    expect(screen.queryByText(/Sin alerta de SENAPRED/)).not.toBeInTheDocument()
  })

  it('un incendio sin alerta lleva una línea, no una barra vacía', () => {
    montar({ type: 'wildfire', alert_level: null, alert_confidence: 0 })
    expect(screen.queryByText('Estado de alerta')).not.toBeInTheDocument()
    expect(screen.getByText('Sin alerta de SENAPRED vigente.')).toBeInTheDocument()
  })

  it('un incendio con alerta vigente muestra la barra y el distintivo', () => {
    montar({ type: 'wildfire', alert_level: 'roja', alert_confidence: 1 })
    expect(screen.getByText('Estado de alerta')).toBeInTheDocument()
    expect(screen.getByText('SENAPRED · Alerta Roja')).toBeInTheDocument()
    expect(screen.queryByText(/Sin alerta de SENAPRED/)).not.toBeInTheDocument()
  })
})
