/**
 * Colores de estado del radar de vehículos.
 *
 * Son colores de DATO, no de cromo: afirman algo sobre el vehículo. Por eso
 * viven acá como hex literales y no como tokens del tema, igual que el resto
 * de `*Symbology.ts` (ver `design-system.test.ts`). Se ven igual en claro y en
 * oscuro.
 *
 *   - **Robado → rojo.** Es lo que hay que buscar.
 *   - **Abandonado → ámbar.** Pide atención, no es una emergencia.
 *   - **Recuperado → verde.** Se lee como «resuelto». Se descartó el cian
 *     porque es el color de la lluvia en el mapa.
 */

import type { VehicleStatus } from '@/api/vehicleFeedTypes'

export interface VehicleStatusStyle {
  /** Singular, para la tarjeta. */
  label: string
  /** Plural, para los filtros. */
  plural: string
  color: string
}

export const VEHICLE_STATUS: Record<VehicleStatus, VehicleStatusStyle> = {
  robado: { label: 'Robado', plural: 'Robados', color: '#dc2626' },
  recuperado: { label: 'Recuperado', plural: 'Recuperados', color: '#16a34a' },
  abandonado: { label: 'Abandonado', plural: 'Abandonados', color: '#d97706' },
}

/** Orden de los filtros: primero lo que hay que buscar. */
export const VEHICLE_STATUS_ORDER: readonly VehicleStatus[] = ['robado', 'recuperado', 'abandonado']
