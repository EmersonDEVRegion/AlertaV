import type { Incident } from '@/api/types'
import type { ExploreArea } from '@/lib/exploreStore'
import { distanceKm } from '@/lib/geo'
import { NEAR_PLACE_KM } from '@/lib/placesStore'
import { communeLabel, sameCommune } from './displayWindow'

/**
 * ¿El incidente cae en el área?
 *
 * Una comuna se compara por nombre, como la escribe el feed: es lo que se
 * puede afirmar sin geometría (un incendio en el límite es «de Quilpué» si así
 * lo informaron). Un lugar guardado se compara por distancia, con el mismo
 * radio que los avisos: «cerca de Casa» dice lo mismo en la lista y en el push.
 */
export function inArea(incident: Incident, area: ExploreArea): boolean {
  if (area.kind === 'commune') return sameCommune(incident.commune, area.name)
  return distanceKm(area.lat, area.lon, incident.lat, incident.lon) <= NEAR_PLACE_KM
}

/** Cómo se nombra el área en el chip del historial. */
export function areaLabel(area: ExploreArea): string {
  return area.kind === 'commune'
    ? (communeLabel(area.name) ?? area.name)
    : `Cerca de ${area.name} · ${NEAR_PLACE_KM} km`
}
