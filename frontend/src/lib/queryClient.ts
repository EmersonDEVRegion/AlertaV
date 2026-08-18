import { QueryClient } from '@tanstack/react-query'
import { ApiError } from '@/api/client'
import { env } from '@/config/env'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Un dato más viejo que media cadencia de polling ya no se considera
      // fresco: en una emergencia el costo de mostrar algo viejo es alto.
      staleTime: env.pollIntervalMs / 2,
      gcTime: 30 * 60_000,
      // El polling se detiene solo cuando la pestana pierde el foco y se
      // reanuda al volver. Es la diferencia entre una app util y una que se
      // come la batería en el bolsillo.
      refetchIntervalInBackground: false,
      refetchOnWindowFocus: true,
      refetchOnReconnect: true,
      retry: (failureCount, error) => {
        if (error instanceof ApiError && !error.isRetryable) return false
        return failureCount < 3
      },
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 15_000),
    },
  },
})

export const queryKeys = {
  incidents: {
    all: ['incidents'] as const,
    active: (params: unknown) => ['incidents', 'active', params] as const,
    detail: (code: string) => ['incidents', 'detail', code] as const,
    stats: (hours?: number) => ['incidents', 'stats', hours ?? null] as const,
  },
} as const
