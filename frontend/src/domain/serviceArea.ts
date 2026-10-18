/**
 * Área afectada de un corte de luz o de agua: la zona sin servicio.
 *
 * El backend la entrega como MultiPolygon GeoJSON (`[lon, lat]`):
 *
 * - **Chilquinta**: la mancha de los clientes y transformadores que lista la
 *   orden, con ~60 m de margen (`core/areas.py`). No es el polígono
 *   que dibuja su visor —la envolvente convexa de los mismos puntos—, que
 *   exagera el área al doble o más.
 * - **CGE**: el polígono del sector que publica en su KMZ.
 * - **Esval**: los polígonos de cada sector del visor oficial.
 *
 * El pin sigue siendo el punto que publica la empresa (donde ubica la falla);
 * el área dice dónde está la gente sin servicio.
 */

import type { MultiPolygon, Polygon, Position } from 'geojson'
import type { Theme } from '@/hooks/useTheme'

export type AreaGeometry = Polygon | MultiPolygon

/** Desde este zoom se ven todas las áreas; antes, sólo la del corte abierto. */
export const AREA_MIN_ZOOM = 13

function isPosition(value: unknown): value is Position {
  return (
    Array.isArray(value) &&
    value.length >= 2 &&
    typeof value[0] === 'number' &&
    typeof value[1] === 'number' &&
    Number.isFinite(value[0]) &&
    Number.isFinite(value[1]) &&
    Math.abs(value[0]) <= 180 &&
    Math.abs(value[1]) <= 90
  )
}

function isRing(value: unknown): value is Position[] {
  return Array.isArray(value) && value.length >= 4 && value.every(isPosition)
}

function isPolygonCoords(value: unknown): value is Position[][] {
  return Array.isArray(value) && value.length >= 1 && value.every(isRing)
}

/**
 * Un área bien formada, o `null`. Un polígono roto no puede tumbar la capa: se
 * descarta y el corte queda como pin.
 */
export function asAreaGeometry(value: unknown): AreaGeometry | null {
  if (typeof value !== 'object' || value === null) return null
  const { type, coordinates } = value as { type?: unknown; coordinates?: unknown }
  if (type === 'Polygon' && isPolygonCoords(coordinates)) {
    return { type: 'Polygon', coordinates }
  }
  if (type === 'MultiPolygon' && Array.isArray(coordinates)) {
    const polygons = coordinates.filter(isPolygonCoords)
    return polygons.length > 0 ? { type: 'MultiPolygon', coordinates: polygons } : null
  }
  return null
}

/**
 * Contorno del área según el tema. El relleno usa el color de la capa (el de
 * la empresa o el cian del agua); en oscuro el contorno se aclara, porque el
 * rojo y el azul profundos se pierden sobre el mapa negro.
 */
export const AREA_LINE: Record<'chilquinta' | 'cge' | 'unknown' | 'water', Record<Theme, string>> =
  {
    chilquinta: { light: '#b91c1c', dark: '#f87171' },
    cge: { light: '#1e3a8a', dark: '#93c5fd' },
    unknown: { light: '#475569', dark: '#cbd5e1' },
    water: { light: '#0e7490', dark: '#22d3ee' },
  }

/** Qué dice la ficha sobre de dónde sale el área. */
export function areaOrigin(provider: string | null | undefined): string {
  if (provider === 'chilquinta') {
    return 'Área calculada por AlertaV con las ubicaciones de clientes que publica Chilquinta.'
  }
  if (provider === 'cge') return 'Área informada por CGE.'
  return 'Área informada por la empresa.'
}
