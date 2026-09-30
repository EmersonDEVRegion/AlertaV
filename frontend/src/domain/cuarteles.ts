/**
 * Cuarteles de Bomberos: cercanía y símbolo.
 *
 * La distancia es **en línea recta**. No es el tiempo de respuesta ni dice qué
 * compañía despacha: eso lo decide la central según la emergencia y los carros
 * disponibles. Por eso la interfaz lo escribe siempre («en línea recta»).
 */

import type { Cuartel } from '@/api/cuarteles'
import { distanceKm } from '@/lib/geo'

export interface CuartelCercano {
  cuartel: Cuartel
  km: number
}

/** Los `n` cuarteles más cercanos a un punto, del más cercano al más lejano. */
export function cuartelesCercanos(
  cuarteles: readonly Cuartel[],
  lat: number,
  lon: number,
  n = 3,
): CuartelCercano[] {
  return cuarteles
    .map((cuartel) => ({ cuartel, km: distanceKm(lat, lon, cuartel.lat, cuartel.lon) }))
    .sort((a, b) => a.km - b.km)
    .slice(0, Math.max(0, n))
}

/** «1,2 km» o «350 m». */
export function formatDistancia(km: number): string {
  if (km < 1) return `${Math.max(10, Math.round((km * 1000) / 10) * 10)} m`
  return `${km.toLocaleString('es-CL', { maximumFractionDigits: 1 })} km`
}

/**
 * Colores de la capa. Pizarra y no rojo: un cuartel es **referencia**, y el
 * rojo del mapa es de los incendios. Un punto rojo fijo en cada compañía se
 * leería como una emergencia permanente.
 */
export const CUARTEL_PALETTE = {
  light: { fill: '#ffffff', stroke: '#334155', text: '#1e293b', halo: '#ffffff' },
  dark: { fill: '#0f172a', stroke: '#cbd5e1', text: '#e2e8f0', halo: '#0f172a' },
} as const

/** Número de emergencias, para el enlace de «Mis lugares». */
export const NUMERO_BOMBEROS = '132'
