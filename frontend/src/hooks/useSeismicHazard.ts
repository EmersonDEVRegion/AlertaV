import { useCallback, useMemo, useState } from 'react'

/**
 * Estado de la capa de amenaza sísmica.
 *
 * # El patrón: montar una vez, alternar visibilidad para siempre
 *
 * Dos banderas, y la distinción entre ambas es todo el diseño:
 *
 *   - `enabled`     — lo que el usuario pide ahora.
 *   - `hasMounted`  — si la fuente llegó a existir alguna vez.
 *
 * `hasMounted` es monótona: pasa de `false` a `true` y no vuelve. Eso produce
 * las dos propiedades que se piden:
 *
 *   * **Carga diferida** — mientras nadie encienda la capa, `hasMounted` es
 *     `false`, no se monta el `<Source>` y el archivo nunca se pide. El arranque
 *     de la aplicación no paga nada.
 *   * **Sin re-descarga** — una vez montada, la fuente se queda. Apagar la capa
 *     sólo cambia `visibility`; la geometría descargada y parseada sigue viva en
 *     el mapa. Volver a encenderla es instantáneo.
 *
 * El error clásico acá es atar el montaje a `enabled` directamente: funciona,
 * pero cada apagado destruye la fuente y cada encendido vuelve a descargar el
 * archivo. Se ve idéntico en pantalla y multiplica el tráfico.
 */

export type HazardStatus = 'idle' | 'loading' | 'ready' | 'error'

export interface SeismicHazardState {
  enabled: boolean
  /** ¿Debe existir el `<Source>` en el árbol? Monótono. */
  hasMounted: boolean
  status: HazardStatus
  toggle: () => void
  /** Lo llama el mapa cuando la fuente termina de cargar. */
  onLoaded: () => void
  onError: () => void
  /** Reintento manual tras un fallo: vuelve a montar la fuente desde cero. */
  retry: () => void
  /** Se usa como `key` del `<Source>` para forzar un remontaje al reintentar. */
  attempt: number
}

export function useSeismicHazard(): SeismicHazardState {
  const [enabled, setEnabled] = useState(false)
  const [hasMounted, setHasMounted] = useState(false)
  const [status, setStatus] = useState<HazardStatus>('idle')
  // Cambiar esta clave fuerza a React a recrear el `<Source>`, que es la única
  // manera de repetir una descarga que falló.
  const [attempt, setAttempt] = useState(0)

  const toggle = useCallback(() => {
    setEnabled((current) => {
      const next = !current
      if (next) {
        setHasMounted(true)
        // Sólo la PRIMERA vez hay carga; después la fuente ya está en el mapa.
        setStatus((s) => (s === 'idle' ? 'loading' : s))
      }
      return next
    })
  }, [])

  const onLoaded = useCallback(() => setStatus('ready'), [])
  const onError = useCallback(() => setStatus('error'), [])

  const retry = useCallback(() => {
    setAttempt((n) => n + 1)
    setStatus('loading')
    setEnabled(true)
    setHasMounted(true)
  }, [])

  return useMemo(
    () => ({ enabled, hasMounted, status, toggle, onLoaded, onError, retry, attempt }),
    [enabled, hasMounted, status, toggle, onLoaded, onError, retry, attempt],
  )
}
