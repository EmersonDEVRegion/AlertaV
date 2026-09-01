/**
 * Espejo TypeScript del artefacto de amenaza sísmica.
 *
 * Fuente de verdad: `backend/scripts/fetch_seismic_hazard.py`, que lo genera una
 * vez cada varios años a partir del MASCSN26 del CSN.
 *
 * # El detalle que decide toda la arquitectura de la capa
 *
 * El CSV del CSN es una **grilla de puntos**. El script los convierte en celdas
 * rectangulares para poder pintar superficie, pero **conserva el nodo original
 * en `properties.lon` / `properties.lat`**. Eso es lo que hace posible el mapa
 * de calor sin recalcular nada ni aproximar centroides: el punto que MapLibre
 * necesita para un `heatmap` ya viaja dentro de cada celda.
 *
 * Sin esa propiedad habría que promediar los cuatro vértices del polígono —lo
 * que da el mismo número, pero por un camino que se rompe el día que el script
 * emita celdas no rectangulares.
 */

import type { Feature, FeatureCollection, Point, Polygon } from 'geojson'

/**
 * Variables del modelo, tal como las renombra el script.
 *
 * Se declaran todas aunque hoy sólo se pinte `pga_475`: cambiar de variable es
 * cambiar una constante en `domain/hazardSymbology.ts`, y el tipo tiene que
 * dejar que eso siga siendo un cambio de una línea.
 */
export interface HazardValues {
  /** PGA (g), 10 % de excedencia en 50 años (~475 años). El del diseño habitual. */
  pga_475: number
  /** PGA (g), 2 % en 50 años (~2475 años). Estructuras críticas. */
  pga_2475?: number
  sa03_475?: number
  sa10_475?: number
  sa30_475?: number
}

export interface HazardCellProperties extends HazardValues {
  /** Centro del nodo original de la grilla del CSN. Ver la nota de la cabecera. */
  lon: number
  lat: number
}

/** Punto derivado que alimenta el `heatmap`. Sólo lleva lo que la capa pinta. */
export interface HazardNodeProperties {
  /** Valor de `HAZARD_VARIABLE` para este nodo. Es el peso del mapa de calor. */
  value: number
}

export type HazardCell = Feature<Polygon, HazardCellProperties>
export type HazardCells = FeatureCollection<Polygon, HazardCellProperties>
export type HazardNodes = FeatureCollection<Point, HazardNodeProperties>

/** Bloque de procedencia que el script adjunta al artefacto. */
export interface HazardMetadata {
  model?: string
  producer?: string
  generated_at?: string
  feature_count?: number
  cell_size_deg?: { lon: number; lat: number }
}

/**
 * Lo que consume el mapa: las dos representaciones del MISMO archivo.
 *
 * Van juntas en una estructura y no en dos consultas porque son una sola
 * descarga y un solo parseo. Separarlas invitaría a pedir el archivo dos veces
 * —una para las celdas y otra para los nodos— que es exactamente el error que
 * este módulo existe para no cometer.
 */
export interface HazardGrid {
  /** Celdas rectangulares. Alimentan el relleno de detalle. */
  cells: HazardCells
  /** Nodos de la grilla. Alimentan el mapa de calor. */
  nodes: HazardNodes
  /** Paso de la grilla en grados, si el artefacto lo declara. */
  cellSizeDeg: number | null
  metadata: HazardMetadata | null
}
