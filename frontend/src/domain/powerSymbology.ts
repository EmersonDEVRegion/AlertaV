/**
 * Simbología de los cortes de suministro.
 *
 * # Por qué el color lo decide el proveedor y no la confianza
 *
 * En el resto de las familias el color codifica `confidence_level`, porque hay
 * una duda real sobre si el fenómeno existe. Acá no la hay: la señal viene de
 * la propia distribuidora, que sabe qué circuitos tiene caídos, y el collector
 * le asigna `POWER_OUTAGE_CONFIDENCE = 1.0`. Un tramo de confianza que siempre
 * vale lo mismo no informa nada, así que el canal del color se usa para lo que
 * sí varía y sí le importa a un vecino: **qué empresa** tiene que reponer.
 *
 * # Los acentos
 *
 *   chilquinta  rojo profundo  #b91c1c
 *   cge         azul oscuro    #1e3a8a
 *
 * Ninguno coincide exactamente con un hex de las otras cuatro paletas, y
 * además los cortes se dibujan como **pines DOM**, no como discos: la forma ya
 * los separa antes que el tono.
 */

import type { OutageProvider } from '@/api/types'

export interface ProviderStyle {
  /** Acento de la marca, usado en el pin, el chip y la casilla del panel. */
  color: string
  /** Color de texto legible sobre `color`. */
  onColor: string
  label: string
  /** Clases Tailwind del chip, con su variante oscura. */
  chip: string
}

export const PROVIDER: Record<OutageProvider, ProviderStyle> = {
  chilquinta: {
    color: '#b91c1c',
    onColor: '#ffffff',
    label: 'Chilquinta',
    chip: 'bg-red-700 text-white dark:bg-red-800',
  },
  cge: {
    color: '#1e3a8a',
    onColor: '#ffffff',
    label: 'CGE',
    chip: 'bg-blue-900 text-white dark:bg-blue-950',
  },
}

export const PROVIDER_ORDER: readonly OutageProvider[] = ['chilquinta', 'cge']

/** Acento de un proveedor desconocido: gris, nunca el de otra empresa. */
export const UNKNOWN_PROVIDER: ProviderStyle = {
  color: '#475569',
  onColor: '#ffffff',
  label: 'Distribuidora no identificada',
  chip: 'bg-slate-600 text-white dark:bg-slate-700',
}

export function isOutageProvider(value: string | null | undefined): value is OutageProvider {
  return value === 'chilquinta' || value === 'cge'
}

export function providerStyle(value: string | null | undefined): ProviderStyle {
  return isOutageProvider(value) ? PROVIDER[value] : UNKNOWN_PROVIDER
}

/**
 * Proveedor de un incidente de corte.
 *
 * Se lee de `outage.provider` cuando el backend lo entrega, y si no se deduce
 * de `sources`, donde `chilquinta` y `cge` ya viajan como `EventSource`. El
 * respaldo importa: si el incidente llega desde una respuesta cacheada previa
 * al campo `outage`, el pin igual se pinta bien.
 */
export function providerOf(incident: {
  outage?: { provider: string } | null
  sources: readonly string[]
}): OutageProvider | null {
  const declared = incident.outage?.provider
  if (isOutageProvider(declared)) return declared

  if (incident.sources.includes('chilquinta')) return 'chilquinta'
  if (incident.sources.includes('cge')) return 'cge'
  return null
}
