import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { isNotFound } from '@/api/client'
import { fetchMeteoWarnings } from '@/api/meteoWarnings'
import type { MeteoWarning } from '@/api/meteoWarningTypes'
import { shouldWarn } from '@/components/ui/LayerHealth'
import { env } from '@/config/env'
import { pollEvery } from '@/lib/polling'
import { queryKeys } from '@/lib/queryClient'

/**
 * Los avisos vigentes de la DMC para la región.
 *
 * Mismos estados que el feed de noticias: `empty` (sin avisos con la fuente
 * sana: no se muestra nada) y `blind` (sin avisos con la fuente caída: se
 * dice). `unavailable` es la fuente apagada o un backend anterior (404).
 *
 * Se descarta lo que ya venció con el reloj del teléfono: sin red, la
 * respuesta guardada puede tener horas.
 */
type MeteoWarningsStatus = 'loading' | 'ready' | 'empty' | 'blind' | 'error' | 'unavailable'

export interface MeteoWarningsState {
  status: MeteoWarningsStatus
  items: MeteoWarning[]
}

const NO_ITEMS: MeteoWarning[] = []
const poll = pollEvery(env.newsPollIntervalMs)

export function vigentes(items: readonly MeteoWarning[], now: number): MeteoWarning[] {
  return items.filter((w) => w.hasta === null || Date.parse(w.hasta) > now)
}

export function useMeteoWarnings(now: number, enabled = true): MeteoWarningsState {
  const query = useQuery({
    queryKey: queryKeys.meteoWarnings.all,
    queryFn: ({ signal }) => fetchMeteoWarnings(signal),
    enabled,
    staleTime: env.newsPollIntervalMs / 2,
    refetchInterval: (q) => (isNotFound(q.state.error) ? false : poll(q)),
  })

  const data = query.data
  const items = useMemo(() => (data ? vigentes(data.items, now) : NO_ITEMS), [data, now])
  const status: MeteoWarningsStatus = isNotFound(query.error)
    ? 'unavailable'
    : data === undefined
      ? query.isError
        ? 'error'
        : 'loading'
      : items.length > 0
        ? 'ready'
        : shouldWarn(data.fuente.estado, 0)
          ? 'blind'
          : 'empty'

  return useMemo(() => ({ status, items }), [status, items])
}
