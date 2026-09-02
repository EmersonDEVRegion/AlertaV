/**
 * Cuándo un cero miente.
 *
 * Separado del test de render a propósito: esta es la regla de la
 * funcionalidad, es aritmética pura y no necesita jsdom. El archivo `.tsx`
 * hermano cubre lo que se dibuja.
 *
 * La funcionalidad existe por el 2026-09-02: el Actor de Instagram estuvo
 * detenido dos horas, se publicó un choque en Avenida España, `collector_runs`
 * tenía el diagnóstico redactado, y el mapa mostró «Accidentes viales · 0»
 * indistinguible de una tarde tranquila.
 */

import { describe, expect, it } from 'vitest'
import type { HealthStatus } from '@/api/health'
import { shouldWarn } from './LayerHealth'

const CIEGOS: HealthStatus[] = ['degraded', 'failing', 'stale', 'never']

describe('shouldWarn', () => {
  it('avisa cuando el contador está en cero y la fuente no ve', () => {
    for (const estado of CIEGOS) {
      expect(shouldWarn(estado, 0), estado).toBe(true)
    }
  })

  it('calla cuando la fuente está sana', () => {
    // El caso mayoritario. Marcarlo sería el canal que grita siempre, que es
    // exactamente cómo `partial` dejó de servir como señal en este proyecto.
    expect(shouldWarn('ok', 0)).toBe(false)
  })

  it('calla cuando hay incidentes, aunque la fuente esté ciega', () => {
    // El aviso no dice «esta fuente tiene un problema», dice «no leas este cero
    // como calma». Con incidentes en pantalla no hay cero que malinterpretar.
    for (const estado of CIEGOS) {
      expect(shouldWarn(estado, 3), estado).toBe(false)
    }
  })

  it('calla mientras no sabe', () => {
    // No saber si una capa ve no autoriza a afirmar que está ciega: si la
    // consulta de salud falla, la interfaz se comporta como antes.
    expect(shouldWarn(undefined, 0)).toBe(false)
  })
})
