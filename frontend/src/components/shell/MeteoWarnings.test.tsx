import { afterEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { MeteoWarning } from '@/api/meteoWarningTypes'
import { shortMoment, vigenciaLabel, zonasLabel } from '@/domain/meteoWarnings'
import { weatherGlyphFor } from '@/domain/weatherGlyphs'
import { resetSections } from '@/lib/sectionStore'
import { resetMeteoZones, useMeteoZonesState } from '@/lib/meteoZonesStore'
import { getSheetSnap, resetSheet, setSheetSnap } from '@/lib/sheetStore'
import { renderHook } from '@testing-library/react'
import { MeteoWarningsView } from './MeteoWarnings'

function aviso(overrides: Partial<MeteoWarning> = {}): MeteoWarning {
  return {
    id: '5852',
    tipo: 'Aviso',
    severidad: 1,
    codigo: 'A561/2026',
    fenomeno: 'Tormentas Eléctricas',
    titulo: 'Probables Tormentas Eléctricas desde la región de Coquimbo a la región del Biobío',
    emision: null,
    desde: '2026-10-19T15:00:00+00:00',
    desde_texto: 'la tarde del lunes 19 de octubre del 2026',
    hasta: '2026-10-21T09:00:00+00:00',
    hasta_texto: 'la noche del martes 20 de octubre del 2026',
    en_curso: false,
    observacion: 'Probabilidad de granizos y fuertes rachas de vientos locales',
    alcance: 'region',
    zonas: [],
    tabla: null,
    url: 'https://archivos.meteochile.gob.cl/portaldmc/AAA/aaa_mapa.php',
    ...overrides,
  }
}

afterEach(() => {
  resetSections()
  resetMeteoZones()
  resetSheet()
})

describe('textos', () => {
  it('acorta el tramo sin inventar horas', () => {
    expect(shortMoment('la tarde del lunes 19 de octubre del 2026')).toBe('lun 19 tarde')
    expect(shortMoment('la mañana del miércoles 21')).toBe('mié 21 mañana')
    expect(shortMoment('el jueves 22 de octubre')).toBe('jue 22')
    expect(shortMoment('hasta nuevo aviso')).toBe('hasta nuevo aviso')
  })

  it('vigencia futura y en curso', () => {
    expect(vigenciaLabel(aviso())).toBe('lun 19 tarde → mar 20 noche')
    expect(vigenciaLabel(aviso({ en_curso: true }))).toBe('En curso · hasta mar 20 noche')
  })

  it('zonas en lenguaje claro', () => {
    expect(zonasLabel(aviso())).toBe('Toda la región')
    expect(
      zonasLabel(
        aviso({
          alcance: 'zonas',
          zonas: [
            { clave: 'Litoral', nombre: 'Litoral' },
            { clave: 'Cordillera_Costa', nombre: 'Cordillera de la Costa' },
            { clave: 'Precordillera', nombre: 'Precordillera' },
          ],
        }),
      ),
    ).toBe('Litoral, Cordillera de la Costa y Precordillera')
  })

  it('glifo por fenómeno', () => {
    expect(weatherGlyphFor('Tormentas Eléctricas')).toBe('lightning')
    expect(weatherGlyphFor('Precipitaciones')).toBe('rain')
    expect(weatherGlyphFor('Viento')).toBe('wind')
    expect(weatherGlyphFor('Nevadas')).toBe('snow')
    expect(weatherGlyphFor('Altas Temperaturas')).toBe('heat')
    expect(weatherGlyphFor('Marejadas')).toBeNull()
  })
})

describe('MeteoWarningsView', () => {
  it('sin avisos y con la fuente sana no dibuja nada', () => {
    const { container } = render(<MeteoWarningsView status="empty" items={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('apagada (404) no dibuja nada', () => {
    const { container } = render(<MeteoWarningsView status="unavailable" items={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('sin avisos y con la fuente caída lo dice', () => {
    render(<MeteoWarningsView status="blind" items={[]} />)
    expect(screen.getByText(/lectura de la DMC no está respondiendo/)).toBeInTheDocument()
  })

  it('nombra la institución y no la confunde con SENAPRED', async () => {
    render(<MeteoWarningsView status="ready" items={[aviso()]} />)
    expect(screen.getByText('DMC · Aviso')).toBeInTheDocument()
    expect(screen.getByText('Tormentas Eléctricas')).toBeInTheDocument()
    expect(screen.getByText('lun 19 tarde → mar 20 noche')).toBeInTheDocument()
    expect(screen.getByText('Toda la región')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /DMC · Aviso/ }))
    expect(screen.getByText(/No es una alerta de SENAPRED/)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /meteochile\.gob\.cl/ })
    expect(link).toHaveAttribute('href', expect.stringContaining('meteochile.gob.cl'))
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
  })

  it('muestra la tabla de valores al abrir', async () => {
    const lluvia = aviso({
      id: '5853',
      fenomeno: 'Precipitaciones',
      tabla: {
        titulo: 'Montos estimados de precipitaciones en milímetros (mm)',
        columnas: ['martes 20'],
        filas: [{ zona: 'Litoral', valores: ['25-35'] }],
      },
    })
    render(<MeteoWarningsView status="ready" items={[lluvia]} />)
    await userEvent.click(screen.getByRole('button', { name: /Precipitaciones/ }))
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByText('25-35')).toBeInTheDocument()
  })

  it('la severidad se distingue por estilo, no sólo por texto', () => {
    render(
      <MeteoWarningsView
        status="ready"
        items={[aviso({ id: 'x', tipo: 'Alarma', severidad: 3 }), aviso({ id: 'y' })]}
      />,
    )
    const cards = screen.getAllByRole('listitem')
    expect(cards[0]!.className).toContain('bg-dmc-3-bg')
    expect(cards[1]!.className).toContain('bg-dmc-1-bg')
  })
})


describe('ver en el mapa', () => {
  it('enciende la capa, enfoca el aviso y baja la hoja', async () => {
    setSheetSnap('half')
    const { result } = renderHook(() => useMeteoZonesState())
    render(<MeteoWarningsView status="ready" items={[aviso()]} />)
    await userEvent.click(screen.getByRole('button', { name: /Tormentas Eléctricas/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Ver en el mapa' }))
    expect(result.current.enabled).toBe(true)
    expect(result.current.focus?.id).toBe('5852')
    expect(getSheetSnap()).toBe('peek')
  })

  it('dos toques sobre el mismo aviso piden encuadrar dos veces', async () => {
    const { result } = renderHook(() => useMeteoZonesState())
    render(<MeteoWarningsView status="ready" items={[aviso()]} />)
    await userEvent.click(screen.getByRole('button', { name: /Tormentas Eléctricas/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Ver en el mapa' }))
    const first = result.current.focus?.nonce
    await userEvent.click(screen.getByRole('button', { name: 'Ver en el mapa' }))
    expect(result.current.focus?.nonce).not.toBe(first)
  })
})
