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

/**
 * Límite de paneo del mapa.
 *
 * Es la UNIÓN de `region_bbox` (incendios) y `usgs_bbox` (sismos), con margen.
 * Si se usara sólo el primero, volar a un sismo del borde sur —que el backend
 * sí entrega, porque su recorte es más ancho a propósito— quedaría bloqueado
 * por `maxBounds` y la cámara se detendría a mitad de camino sin explicación.
 */
export const MAP_MAX_BOUNDS: [number, number, number, number] = [
  -73.4, -35.6, -68.6, -30.6,
]

/**
 * Zoom de aterrizaje al elegir un incidente de la lista: se ven las calles del
 * sector sin perder el entorno.
 */
export const FOCUS_ZOOM = 14.5

/**
 * Zoom para un sismo. Mucho menor a propósito: la información de un sismo es su
 * alcance regional, y a 14,5 la cámara quedaría dentro del radio de percepción
 * sin ver ni su borde.
 */
export const SEISMIC_FOCUS_ZOOM = 9.5

/**
 * Capa de referencia de amenaza sísmica.
 *
 * # Por qué ya no apunta a `/static`
 *
 * La versión anterior era `'/static/geo/amenaza_sismica_valpo.json'`, una ruta
 * relativa, con el razonamiento de que «en producción la sirve el mismo origen
 * que la API». Eso no es cierto en el despliegue real: el frontend vive en
 * Vercel y la API en Render, así que una ruta relativa se resuelve contra el
 * dominio del **frontend**, que nunca tuvo ese archivo. En desarrollo sí
 * funcionaba, porque el proxy de Vite reenvía `/static` al backend — y esa
 * asimetría es lo que hacía que el fallo sólo apareciera en producción.
 *
 * Ahora cuelga de `apiBaseUrl`, que ya resuelve las dos formas legítimas: ruta
 * relativa en desarrollo (y el proxy la lleva al backend) o URL absoluta en
 * producción. El endpoint sirve el mismo GeoJSON con `ETag`, respaldo en caché
 * y un 502 explicado cuando el artefacto no se ha generado.
 */
export const HAZARD_SOURCE_URL = `${env.apiBaseUrl}/events/seismic/hazard`

export const MAP_STYLE_URL = env.mapStyle
export const MAP_STYLE_URL_DARK = env.mapStyleDark

/** Estilo según el tema activo. */
export function mapStyleFor(theme: 'light' | 'dark'): string {
  return theme === 'dark' ? MAP_STYLE_URL_DARK : MAP_STYLE_URL
}


/** Atribucion obligatoria del mapa base. */
export const MAP_ATTRIBUTION =
  '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> | <a href="https://carto.com/attributions" target="_blank" rel="noreferrer">CARTO</a>'
