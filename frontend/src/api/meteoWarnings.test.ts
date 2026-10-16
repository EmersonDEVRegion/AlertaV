import { describe, expect, it } from 'vitest'
import { parseMeteoWarnings } from './meteoWarnings'
import { vigentes } from '@/hooks/useMeteoWarnings'
import type { MeteoWarning } from './meteoWarningTypes'

/** Un item como lo entrega el backend con la captura real del 16-10-2026. */
const TORMENTA = {
  id: '5852',
  tipo: 'Aviso',
  severidad: 1,
  codigo: 'A561/2026',
  fenomeno: 'Tormentas Eléctricas',
  titulo: 'Probables Tormentas Eléctricas desde la región de Coquimbo a la región del Biobío',
  emision: '2026-10-16T15:10:00+00:00',
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
}

describe('parseMeteoWarnings', () => {
  it('lee el item real', () => {
    const r = parseMeteoWarnings({ items: [TORMENTA], fuente: { estado: 'ok' } })
    expect(r.items).toHaveLength(1)
    expect(r.items[0]!.severidad).toBe(1)
    expect(r.items[0]!.alcance).toBe('region')
    expect(r.fuente.estado).toBe('ok')
  })

  it('descarta tipos desconocidos y la severidad sale del tipo, no del número', () => {
    const r = parseMeteoWarnings({
      items: [
        { ...TORMENTA, id: 'a', tipo: 'Boletín' },
        { ...TORMENTA, id: 'b', tipo: 'Alarma', severidad: 1 },
        null,
      ],
    })
    expect(r.items.map((w) => [w.id, w.severidad])).toEqual([['b', 3]])
  })

  it('sólo enlaza a la DMC', () => {
    const r = parseMeteoWarnings({ items: [{ ...TORMENTA, url: 'https://evil.example/x' }] })
    expect(r.items[0]!.url).toContain('meteochile.gob.cl')
  })

  it('zonas vacías se leen como toda la región', () => {
    const r = parseMeteoWarnings({ items: [{ ...TORMENTA, alcance: 'zonas', zonas: [] }] })
    expect(r.items[0]!.alcance).toBe('region')
  })

  it('una tabla sin filas no se muestra', () => {
    const r = parseMeteoWarnings({
      items: [{ ...TORMENTA, tabla: { titulo: 'x', columnas: [], filas: [] } }],
    })
    expect(r.items[0]!.tabla).toBeNull()
  })
})

describe('vigentes', () => {
  it('descarta lo vencido con el reloj del teléfono', () => {
    const w = parseMeteoWarnings({ items: [TORMENTA] }).items[0]!
    const antes = Date.parse('2026-10-20T12:00:00Z')
    const despues = Date.parse('2026-10-21T10:00:00Z')
    expect(vigentes([w], antes)).toHaveLength(1)
    expect(vigentes([w], despues)).toHaveLength(0)
    const sinFin: MeteoWarning = { ...w, hasta: null }
    expect(vigentes([sinFin], despues)).toHaveLength(1)
  })
})
