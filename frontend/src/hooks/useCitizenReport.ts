import { useMutation, useQueryClient } from '@tanstack/react-query'
import { submitCitizenReport } from '@/api/citizenReports'
import { ApiError } from '@/api/client'
import type { CitizenReportPayload, RawEvent } from '@/api/types'
import { queryKeys } from '@/lib/queryClient'

/** Texto legible para el 422 de FastAPI, que llega anidado en `detail[]`. */
function readableError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return 'No se pudo enviar el reporte. Intenta de nuevo.'
  }

  if (error.status === 0) {
    return 'No hay conexión con el servidor. Tu reporte no se envió; vuelve a intentar cuando tengas señal.'
  }

  if (error.status === 422) {
    const body = error.body as { detail?: Array<{ msg?: string }> } | undefined
    const first = body?.detail?.[0]?.msg
    return first
      ? `El servidor rechazó el reporte: ${first}`
      : 'El servidor rechazó el reporte por datos inválidos.'
  }

  if (error.status === 429) {
    return 'Demasiados reportes seguidos. Espera un momento antes de volver a enviar.'
  }

  if (error.status >= 500) {
    return 'El servidor tuvo un problema al registrar el reporte. Intenta de nuevo en un momento.'
  }

  return 'No se pudo enviar el reporte. Intenta de nuevo.'
}

/**
 * Envío del reporte ciudadano.
 *
 * Al terminar invalida los incidentes activos, pero sin prometer nada: el
 * reporte entra como señal cruda y el motor de correlación corre cada 120 s.
 * Puede que el refetch no traiga todavía ningún incidente nuevo, y eso es
 * correcto — un avistamiento no es un incendio confirmado.
 */
export function useCitizenReport() {
  const queryClient = useQueryClient()

  const mutation = useMutation<RawEvent, unknown, CitizenReportPayload>({
    mutationFn: (payload) => submitCitizenReport(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.incidents.all })
    },
    // Un reporte no es idempotente: reintentar solo por un 500 puede duplicar
    // la señal. El reintento lo decide la persona, con el botón a la vista.
    retry: false,
  })

  return {
    submit: mutation.mutateAsync,
    isSubmitting: mutation.isPending,
    isSuccess: mutation.isSuccess,
    createdEvent: mutation.data ?? null,
    errorMessage: mutation.isError ? readableError(mutation.error) : null,
    reset: mutation.reset,
  }
}
