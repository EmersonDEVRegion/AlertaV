/** Encuadre inicial y limites del mapa. */

import { env } from './env'

/** Centro pedido para el MVP: Región de Valparaíso. */
export const INITIAL_VIEW_STATE = {
  longitude: -71.5,
  latitude: -33.0,
  zoom: 9.4,
  bearing: 0,
  pitch: 0,
} as const

/**
 * Espejo de REGION_{WEST,SOUTH,EAST,NORTH} en `backend/app/core/config.py`, con
 * un margen para poder ver el borde. Se usa para acotar el paneo: mostrar un
 * mapa mundial invita a buscar incidentes donde el backend no recolecta nada.
 *
 * Orden: oeste, sur, este, norte — el mismo de `BoundingBox.as_firms_param()`.
 */
export const REGION_BOUNDS: [number, number, number, number] = [
  -72.6, -34.4, -69.2, -31.4,
]

export const MAP_STYLE_URL = env.mapStyle

/** Atribucion obligatoria del mapa base. */
export const MAP_ATTRIBUTION =
  '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> | <a href="https://carto.com/attributions" target="_blank" rel="noreferrer">CARTO</a>'
