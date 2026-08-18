import { useSyncExternalStore } from 'react'

function subscribe(callback: () => void): () => void {
  window.addEventListener('online', callback)
  window.addEventListener('offline', callback)
  return () => {
    window.removeEventListener('online', callback)
    window.removeEventListener('offline', callback)
  }
}

/**
 * `navigator.onLine` solo garantiza el negativo: si dice false, no hay red. Un
 * true no prueba que el backend responda, por eso la UI cruza esto con la edad
 * real del último dato (ver `useFreshness`).
 */
export function useOnlineStatus(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => navigator.onLine,
    () => true,
  )
}
