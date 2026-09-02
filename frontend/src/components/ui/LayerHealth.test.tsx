/**
 * La marca que separa los dos ceros.
 *
 * Existe por el 2026-09-02: el Actor de Instagram detenido dos horas, un choque
 * publicado en Avenida España, el diagnóstico escrito en `collector_runs`, y el
 * mapa mostrando «Accidentes viales · 0» igual que en una tarde tranquila.
 */

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { LayerHealth } from './LayerHealth'

describe('LayerHealth', () => {
  it('marca un cero cuya fuente está ciega', () => {
    render(<LayerHealth status="degraded" count={0} />)

    expect(screen.getByText('Sin datos')).toBeInTheDocument()
  })

  it('NO marca un cero cuando la fuente está sana', () => {
    // Este es el caso mayoritario: un día tranquilo de verdad. Si la marca
    // apareciera acá, sería el canal que grita siempre.
    render(<LayerHealth status="ok" count={0} />)

    expect(screen.queryByText('Sin datos')).toBeNull()
  })

  it('NO marca una capa ciega que SÍ tiene incidentes', () => {
    /*
     * El aviso no dice «esta fuente tiene un problema», dice «no leas este cero
     * como calma». Con incidentes en pantalla no hay cero que malinterpretar, y
     * un ícono compitiendo con una emergencia real es ruido.
     */
    render(<LayerHealth status="degraded" count={3} />)

    expect(screen.queryByText('Sin datos')).toBeNull()
  })

  it('no afirma nada cuando la salud todavía no llegó', () => {
    // No saber si una capa ve no autoriza a decir que está ciega. Si la
    // consulta de salud falla, la interfaz se comporta como antes.
    render(<LayerHealth status={undefined} count={0} />)

    expect(screen.queryByText('Sin datos')).toBeNull()
  })

  it('explica en el título que el cero no significa calma', () => {
    render(<LayerHealth status="stale" count={0} />)

    expect(screen.getByTitle(/no significa que no haya ocurrido nada/i)).toBeInTheDocument()
  })

  it('adjunta el motivo que dio la propia fuente', () => {
    // El texto que el collector escribió en `collector_runs.error`: es lo que
    // convierte «sin datos» en algo accionable.
    render(
      <LayerHealth
        status="degraded"
        count={0}
        detail="datos rancios: la última corrida exitosa del Actor terminó hace 136 min"
      />,
    )

    expect(screen.getByTitle(/136 min/)).toBeInTheDocument()
  })
})
