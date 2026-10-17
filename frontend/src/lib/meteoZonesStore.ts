import { useSyncExternalStore } from 'react'

/**
 * La capa «Avisos DMC» del mapa: si está encendida y qué aviso hay que mostrar.
 *
 * Vive fuera de React, como la selección y la hoja, porque la tocan piezas que
 * no son parientes: el azulejo de «Capas», el botón «Ver en el mapa» de la
 * franja de «Ahora» y la capa dentro del mapa. Así ninguna de ellas pasa por
 * `App` y encender la capa no repinta nada más.
 *
 * Arranca apagada, como las demás capas de referencia: nada se descarga sin un
 * gesto. `hasMounted` sigue la misma regla que ellas: tras el primer
 * encendido el `<Source>` se queda montado y apagar es sólo `visibility`.
 *
 * `focus` es el aviso que pidió «Ver en el mapa»: la capa resalta sus zonas y
 * encuadra la cámara. `nonce` distingue dos toques seguidos sobre el mismo
 * aviso (el segundo vuelve a encuadrar aunque el id no cambie).
 */
export interface MeteoZonesState {
  enabled: boolean
  hasMounted: boolean
  focus: { id: string; nonce: number } | null
}

let current: MeteoZonesState = { enabled: false, hasMounted: false, focus: null }
const listeners = new Set<() => void>()
let nonce = 0

function apply(next: MeteoZonesState): void {
  current = next
  for (const listener of listeners) listener()
}

export function toggleMeteoZones(): void {
  const enabled = !current.enabled
  apply({ enabled, hasMounted: current.hasMounted || enabled, focus: enabled ? current.focus : null })
}

/** «Ver en el mapa»: enciende la capa, resalta las zonas del aviso y las encuadra. */
export function showWarningOnMap(id: string): void {
  nonce += 1
  apply({ enabled: true, hasMounted: true, focus: { id, nonce } })
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

const snapshot = () => current

export function useMeteoZonesState(): MeteoZonesState {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}

/** Para los tests. */
export function resetMeteoZones(): void {
  nonce = 0
  apply({ enabled: false, hasMounted: false, focus: null })
}
