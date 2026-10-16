import { useCallback, useSyncExternalStore } from 'react'

/**
 * Qué secciones plegables dejó abiertas la persona: «En vivo», «Hoy», «Ayer» y
 * «Noticias».
 *
 * Se recuerda en este teléfono (`localStorage`), como «Mis lugares». Es una
 * comodidad, no un dato: si el almacenamiento no está (navegación privada,
 * almacenamiento bloqueado) cada sección arranca en su valor por defecto y
 * plegar sigue funcionando mientras la página esté abierta.
 *
 * Fuera de React, como la selección: plegar una sección no tiene por qué
 * repintar nada más que esa sección.
 */

const KEY = 'alertav:secciones'

type OpenState = Readonly<Record<string, boolean>>

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

function load(): OpenState {
  try {
    const raw = storage()?.getItem(KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return {}
    return Object.fromEntries(
      Object.entries(parsed).filter(([, value]) => typeof value === 'boolean'),
    ) as OpenState
  } catch {
    return {}
  }
}

let state: OpenState = load()
const listeners = new Set<() => void>()

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

const snapshot = () => state

export function setSectionOpen(id: string, open: boolean): void {
  if (state[id] === open) return
  state = { ...state, [id]: open }
  try {
    storage()?.setItem(KEY, JSON.stringify(state))
  } catch {
    // Sin almacenamiento: queda plegada mientras la página esté abierta.
  }
  for (const listener of listeners) listener()
}

/** `[abierta, alternar]` para la sección `id`, con su valor por defecto. */
export function useSectionOpen(id: string, defaultOpen: boolean): [boolean, () => void] {
  const current = useSyncExternalStore(subscribe, snapshot, snapshot)
  const open = current[id] ?? defaultOpen
  const toggle = useCallback(() => setSectionOpen(id, !open), [id, open])
  return [open, toggle]
}

/** Para las pruebas: olvida todo, en memoria y en el almacenamiento. */
export function resetSections(): void {
  state = {}
  try {
    storage()?.removeItem(KEY)
  } catch {
    // nada que borrar
  }
  for (const listener of listeners) listener()
}
