import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { isNotFound } from '@/api/client'
import {
  EMPTY_TRANSITO,
  fetchTransito,
  type TransitoCollection,
  type TransitoFeature,
} from '@/api/transito'
import { env } from '@/config/env'
import { TRANSITO_ORDEN } from '@/domain/transitoSymbology'
import { pollEvery } from '@/lib/polling'
import { queryKeys } from '@/lib/queryClient'

/**
 * El tránsito del MTT vigente, para la capa y para la lista de «Ahora».
 *
 * Siempre consultado, como los cortes de agua: la lista de «Ahora» tiene que
 * estar sin abrir nada. Son unas decenas de líneas cada dos minutos.
 *
 * Se descarta lo vencido con el reloj del teléfono (`vence`): sin red, la
 * respuesta guardada puede tener una hora y un taco de hace una hora ya no es
 * noticia.
 *
 *   - `loading`     — todavía no llegó nada.
 *   - `ready`       — hay avisos vigentes.
 *   - `empty`       — nada vigente.
 *   - `error`       — la consulta falló y no hay nada en caché.
 *   - `unavailable` — el backend no tiene la ruta (404): se deja de consultar.
 */
type TransitoStatus = 'loading' | 'ready' | 'empty' | 'error' | 'unavailable'

export interface TransitoData {
  status: TransitoStatus
  /** Todo lo vigente, para el mapa (los sin geometría no se dibujan solos). */
  data: TransitoCollection
  /** Lo mismo ordenado para la lista: lo que cambia qué hacer arriba. */
  items: readonly TransitoFeature[]
}

const poll = pollEvery(env.transitoPollIntervalMs)

export function vigentesTransito(data: TransitoCollection, now: number): TransitoCollection {
  const features = data.features.filter((f) => Date.parse(f.properties.vence) > now)
  if (features.length === data.features.length) return data
  return features.length === 0 ? EMPTY_TRANSITO : { type: 'FeatureCollection', features }
}

export function ordenarTransito(features: readonly TransitoFeature[]): TransitoFeature[] {
  return [...features].sort(
    (a, b) =>
      TRANSITO_ORDEN[a.properties.subtipo] - TRANSITO_ORDEN[b.properties.subtipo] ||
      b.properties.timestamp.localeCompare(a.properties.timestamp),
  )
}

export function useTransito(now: number, enabled = env.transitoEnabled): TransitoData {
  const query = useQuery({
    queryKey: queryKeys.transito.geojson(),
    queryFn: ({ signal }) => fetchTransito(signal),
    enabled,
    staleTime: env.transitoPollIntervalMs / 2,
    refetchInterval: (q) => (isNotFound(q.state.error) ? false : poll(q)),
  })

  const raw = query.data
  const data = useMemo(() => (raw ? vigentesTransito(raw, now) : EMPTY_TRANSITO), [raw, now])
  const items = useMemo(() => ordenarTransito(data.features), [data])

  const status: TransitoStatus =
    !enabled || isNotFound(query.error)
      ? 'unavailable'
      : raw === undefined
        ? query.isError
          ? 'error'
          : 'loading'
        : data.features.length > 0
          ? 'ready'
          : 'empty'

  return useMemo(() => ({ status, data, items }), [status, data, items])
}
