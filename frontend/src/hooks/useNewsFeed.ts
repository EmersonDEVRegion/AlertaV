import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from '@/api/client'
import { fetchNewsFeed } from '@/api/newsFeed'
import type { NewsFeedItem, NewsFeedQuery } from '@/api/newsFeedTypes'
import { shouldWarn } from '@/components/ui/LayerHealth'
import { env } from '@/config/env'
import { pollEvery } from '@/lib/polling'
import { queryKeys } from '@/lib/queryClient'

/**
 * Las noticias de las últimas 24 h (desde el 2026-10-06 fuera del mapa).
 *
 * Mismos estados que el radar de vehículos, por la misma razón: `empty` (cero
 * notas con la fuente sana) y `blind` (cero notas con la fuente caída) no
 * dicen lo mismo. `unavailable` es un backend anterior al feed (404): no se
 * sigue consultando.
 */
type NewsFeedStatus = 'loading' | 'ready' | 'empty' | 'blind' | 'error' | 'unavailable'

export interface NewsFeedState {
  status: NewsFeedStatus
  items: NewsFeedItem[]
}

const NEWS_FEED_PARAMS: NewsFeedQuery = { horas: 24, limit: 60 }

const NO_ITEMS: NewsFeedItem[] = []
const pollFeed = pollEvery(env.newsPollIntervalMs)

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}

export function useNewsFeed(enabled = true): NewsFeedState {
  const query = useQuery({
    queryKey: queryKeys.news.feed(NEWS_FEED_PARAMS),
    queryFn: ({ signal }) => fetchNewsFeed(NEWS_FEED_PARAMS, signal),
    enabled,
    staleTime: env.newsPollIntervalMs / 2,
    refetchInterval: (q) => (isNotFound(q.state.error) ? false : pollFeed(q)),
  })

  const data = query.data
  const items = data?.items ?? NO_ITEMS
  const status: NewsFeedStatus = isNotFound(query.error)
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
