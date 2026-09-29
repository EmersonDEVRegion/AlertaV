import { useMemo } from 'react'
import type { Incident } from '@/api/types'
import { layerOf } from '@/domain/families'
import { windConeFor } from '@/domain/windCone'
import { useSelectedIncidentCode } from '@/lib/selectionStore'
import { useCurrentWind } from './useCurrentWind'

/**
 * El incendio seleccionado y el viento en su posición.
 *
 * Lo usan dos componentes que no se conocen: la ficha (que muestra el viento)
 * y el mapa (que dibuja el cono). Los dos piden el mismo viento con la misma
 * clave de caché, así que react-query hace UNA sola llamada a Open-Meteo.
 *
 * Sólo un INCENDIO tiene viento: una cuña de propagación sobre un choque no
 * significa nada, y pedirlo para cualquier selección sería una llamada por
 * toque a un servicio externo.
 *
 * `incidents` es el conjunto en el que se busca: el que se está mostrando. Si
 * la capa de incendios está apagada, el incendio no aparece y el cono no se
 * dibuja, igual que antes.
 */
export function useSelectedFire(incidents: readonly Incident[]) {
  const code = useSelectedIncidentCode()

  const fire = useMemo(() => {
    if (code === null) return null
    const incident = incidents.find((candidate) => candidate.code === code)
    return incident && layerOf(incident.type) === 'fire' ? incident : null
  }, [incidents, code])

  const { data: wind, isLoading, isError } = useCurrentWind(
    fire?.lat ?? null,
    fire?.lon ?? null,
    fire !== null,
  )

  const cone = useMemo(
    () => windConeFor(wind?.windSpeedKmh, wind?.windDirectionDeg),
    [wind],
  )

  return {
    fire,
    wind: fire ? (wind ?? null) : null,
    cone: fire ? cone : null,
    windLoading: fire !== null && isLoading,
    windError: fire !== null && isError,
  }
}
