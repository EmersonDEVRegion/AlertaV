import { useEffect, useMemo, useState } from 'react'
import type { Incident } from '@/api/types'
import { splitForDisplay, type DisplaySplit } from '@/domain/displayWindow'

/**
 * Tope del temporizador. `setTimeout` desborda pasados ~24,8 días y dispara al
 * instante; además, un teléfono que durmió puede despertar con el reloj
 * corrido. Una revisión por hora como máximo no cuesta nada.
 */
const MAX_WAIT_MS = 3_600_000

/**
 * Qué va en el mapa y qué en el historial, al día sin un reloj en `App`.
 *
 * Un reloj de un minuto acá repintaría `App` —y con él, el mapa— sesenta veces
 * por hora aunque no cambie nada (ver `App.renders.test.tsx`). En cambio se
 * agenda UN temporizador para el instante exacto en que algo sale del mapa o
 * del historial, y sólo entonces se recalcula. Con la app quieta y nada por
 * vencer, no hay temporizador.
 *
 * Los arreglos conservan su identidad mientras no cambie su contenido, así que
 * el `memo` del mapa no se entera de un recálculo que dio lo mismo.
 */
export function useDisplaySplit(incidents: readonly Incident[]): DisplaySplit {
  const [tick, setTick] = useState(0)

  // `tick` sólo está para forzar el recálculo cuando vence el temporizador.
  const fresh = useMemo(
    () => splitForDisplay(incidents, Date.now()),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `tick` es el disparador
    [incidents, tick],
  )

  const stable = useStable(fresh)

  useEffect(() => {
    if (!Number.isFinite(stable.nextChange)) return
    const wait = Math.min(Math.max(stable.nextChange - Date.now(), 0) + 50, MAX_WAIT_MS)
    const id = setTimeout(() => setTick((t) => t + 1), wait)
    return () => clearTimeout(id)
  }, [stable.nextChange])

  return stable
}

function sameList(a: readonly Incident[], b: readonly Incident[]): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false
  return true
}

/** Devuelve los arreglos anteriores si el contenido es el mismo. */
function useStable(next: DisplaySplit): DisplaySplit {
  const [previous, setPrevious] = useState(next)
  const onMap = sameList(previous.onMap, next.onMap) ? previous.onMap : next.onMap
  const history = sameList(previous.history, next.history) ? previous.history : next.history
  const result =
    onMap === previous.onMap &&
    history === previous.history &&
    next.nextChange === previous.nextChange
      ? previous
      : { onMap, history, nextChange: next.nextChange }
  // Patrón de React para «estado derivado del render anterior»: se ajusta
  // durante el render, sin un efecto que provoque un segundo pase.
  if (result !== previous) setPrevious(result)
  return result
}
