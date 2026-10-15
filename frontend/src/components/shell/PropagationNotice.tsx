import { memo } from 'react'
import { toggleWeatherDetail, useTacticalWeather } from '@/lib/tacticalWeatherStore'
import { cn } from '@/lib/cn'

/**
 * «Condición de propagación de incendios en 3 comunas», bajo «N en curso».
 *
 * La calcula el backend por comuna (30-30-30 y su tramo costero, ver
 * `collectors/weather/umbrales.py`) y antes sólo se veía al abrir el panel del
 * clima. Es un pronóstico, no un incendio: por eso es una línea y no un
 * incidente, y por eso dice «condición». Tocarla abre el panel del clima, que
 * explica la cifra.
 */
export const PropagationNotice = memo(function PropagationNotice() {
  const { data } = useTacticalWeather()
  const comunas = data.comunas_propagacion
  if (comunas.length === 0 || data.propagacion_severidad === 'ninguna') return null

  const critica = data.propagacion_severidad === 'critica'
  const lugar = comunas.length === 1 ? comunas[0] : `${comunas.length} comunas`

  return (
    <button
      type="button"
      onClick={toggleWeatherDetail}
      className={cn(
        'mx-3 mb-2 flex w-[calc(100%-1.5rem)] items-center gap-2 rounded-control px-2.5 py-1.5 text-left text-[11.5px] font-semibold leading-tight',
        critica ? 'bg-danger-bg text-danger-ink' : 'bg-warn-bg text-warn-ink',
      )}
    >
      <span aria-hidden className={cn('size-2 shrink-0 rounded-full', critica ? 'bg-danger-ink' : 'bg-warn-ink')} />
      <span className="min-w-0 flex-1">
        {critica ? 'Condición crítica' : 'Condición'} de propagación de incendios en {lugar}
      </span>
      <span aria-hidden className="text-[10px] font-medium opacity-80">Ver</span>
    </button>
  )
})
