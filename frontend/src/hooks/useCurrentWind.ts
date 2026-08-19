import { useQuery } from '@tanstack/react-query'
import { fetchCurrentWind } from '@/api/weather'

/**
 * Viento actual en la posición de un incidente.
 *
 * Se consulta **sólo cuando hay un incendio seleccionado**, no en cada refresco
 * del mapa: son decenas de incidentes y sería una llamada por cada uno a un
 * servicio externo gratuito. `enabled` es lo que lo garantiza.
 *
 * `staleTime` de 10 minutos porque el viento no cambia de minuto a minuto y
 * Open-Meteo actualiza en esa escala; abrir y cerrar la misma ficha no vuelve a
 * pedir nada.
 *
 * Las coordenadas se redondean en la clave de caché: dos incidentes a 50 m
 * comparten el mismo viento y no tiene sentido pedirlo dos veces.
 */
export function useCurrentWind(
  lat: number | null,
  lon: number | null,
  enabled = true,
) {
  const active = enabled && lat !== null && lon !== null

  return useQuery({
    queryKey: [
      'weather',
      'current-wind',
      lat === null ? null : Number(lat.toFixed(2)),
      lon === null ? null : Number(lon.toFixed(2)),
    ],
    queryFn: ({ signal }) => fetchCurrentWind(lat as number, lon as number, signal),
    enabled: active,
    staleTime: 10 * 60_000,
    gcTime: 30 * 60_000,
    // Un tercero caído no debe reintentar en bucle: el cono simplemente no se
    // dibuja y la ficha lo dice.
    retry: 1,
  })
}
