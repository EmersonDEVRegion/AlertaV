import { useSyncExternalStore } from 'react'
import type { TransitoSubtipo } from '@/api/transito'

/**
 * La capa de tránsito del mapa: si está encendida, qué subtipos se ocultaron y
 * qué aviso pidió «Ver en el mapa».
 *
 * Fuera de React, como `meteoZonesStore`: la tocan el azulejo de «Capas», la
 * lista de «Ahora», el enlace de una notificación y la capa dentro del mapa,
 * que no son parientes.
 *
 * Arranca apagada, como las demás capas de referencia. `hasMounted` sigue la
 * misma regla: tras el primer encendido el `<Source>` se queda montado.
 */
export interface TransitoState {
  enabled: boolean
  hasMounted: boolean
  /** Los subtipos apagados con sus chips en «Capas». */
  ocultos: readonly TransitoSubtipo[]
  focus: { id: string; nonce: number } | null
}

const INICIAL: TransitoState = { enabled: false, hasMounted: false, ocultos: [], focus: null }

let current: TransitoState = INICIAL
const listeners = new Set<() => void>()
let nonce = 0

function apply(next: TransitoState): void {
  current = next
  for (const listener of listeners) listener()
}

export function setTransitoEnabled(enabled: boolean): void {
  if (enabled === current.enabled) return
  apply({
    ...current,
    enabled,
    hasMounted: current.hasMounted || enabled,
    focus: enabled ? current.focus : null,
  })
}

/** Un chip de «Capas»: muestra u oculta un subtipo en el mapa. */
export function toggleTransitoSubtipo(subtipo: TransitoSubtipo): void {
  const ocultos = current.ocultos.includes(subtipo)
    ? current.ocultos.filter((s) => s !== subtipo)
    : [...current.ocultos, subtipo]
  apply({ ...current, ocultos })
}

/**
 * «Ver en el mapa» (y el enlace de una notificación): enciende la capa, vuelve
 * a mostrar el subtipo si estaba oculto, resalta el tramo y lo encuadra.
 */
export function showTransitoOnMap(id: string, subtipo?: TransitoSubtipo): void {
  nonce += 1
  apply({
    enabled: true,
    hasMounted: true,
    ocultos: subtipo ? current.ocultos.filter((s) => s !== subtipo) : current.ocultos,
    focus: { id, nonce },
  })
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

const snapshot = () => current

export function useTransitoState(): TransitoState {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}

/** Para los tests. */
export function resetTransito(): void {
  nonce = 0
  apply(INICIAL)
}
