import { cn } from '@/lib/cn'

interface MapOverlayStateProps {
  title: string
  detail: string
  /**
   * ¿Se está esperando algo?
   *
   * Cambia el cartel de «informativo» a «en curso». La distinción importa: un
   * estado vacío es una conclusión —no hay incidentes— y uno de carga es una
   * promesa. Sin la señal de actividad, los dos se ven idénticos y quien mira
   * no sabe si esperar o si ya está viendo la respuesta.
   */
  busy?: boolean
}

/** Estado vacío o de carga, superpuesto al mapa sin taparlo del todo. */
export function MapOverlayState({ title, detail, busy = false }: MapOverlayStateProps) {
  return (
    <div className="pointer-events-none absolute inset-x-0 top-1/2 z-10 flex -translate-y-1/2 justify-center px-6">
      <div className="animate-rise surface-floating relative max-w-xs overflow-hidden px-4 py-3 text-center">
        {busy && (
          /*
           * Barrido en el borde superior. Es la única señal de actividad, y va
           * ahí —y no como un anillo giratorio— porque un cartel centrado sobre
           * el mapa con un spinner dentro se lee como un bloqueo modal. Una
           * línea que recorre el canto dice «trabajando» sin decir «espera».
           */
          <span aria-hidden className="shimmer absolute inset-x-0 top-0 h-0.5" />
        )}

        <p className={cn('text-sm font-semibold text-ink', busy && 'animate-fade')}>{title}</p>
        <p className="mt-1 text-xs leading-snug text-ink-muted">{detail}</p>
      </div>
    </div>
  )
}
