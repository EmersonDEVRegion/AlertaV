/**
 * Espejo TypeScript del artefacto de amenaza sísmica.
 *
 * Fuente de verdad: `backend/scripts/fetch_seismic_hazard.py`, que lo genera una
 * vez cada varios años a partir del MASCSN26 del CSN.
 *
 * # El detalle que decide toda la arquitectura de la capa
 *
 * El CSV del CSN es una **grilla de puntos**. El script los convierte en celdas
 * rectangulares para poder pintar superficie —media celda a cada lado del nodo,
 * usando el paso inferido de los propios datos— y **conserva el nodo original en
 * `properties.lon` / `properties.lat`**.
 *
 * Que las celdas **teselen** es la propiedad de la que depende todo lo demás:
 * es lo que permite que un relleno por polígono se lea como una superficie
 * continua en vez de como una nube de rectángulos sueltos. Durante un tiempo no
 * teselaron —el generador infería mal el paso en longitud— y la capa necesitó un
 * mapa de calor encima para disimularlo. Ese remiendo ya no existe; la historia
 * completa está en `domain/hazardSymbology.ts` y en `infer_row_step`, en el
 * script.
 *
 * El centro del nodo se conserva igual, aunque hoy ninguna capa lo pinte: sirve
 * para etiquetar, para consultar el valor bajo el cursor y para volver a cruzar
 * con el CSV del CSN sin recalcular centroides.
 */

import type { Feature, FeatureCollection, Polygon } from 'geojson'

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

export type HazardCell = Feature<Polygon, HazardCellProperties>
export type HazardCells = FeatureCollection<Polygon, HazardCellProperties>

/** Bloque de procedencia que el script adjunta al artefacto. */
export interface HazardMetadata {
  model?: string
  producer?: string
  generated_at?: string
  feature_count?: number
  cell_size_deg?: { lon: number; lat: number }
}

/** Lo que consume el mapa: una descarga, un parseo, una representación. */
export interface HazardGrid {
  /** Celdas rectangulares contiguas. Son la superficie de intensidad. */
  cells: HazardCells
  /** Paso de la grilla en grados, si el artefacto lo declara. */
  cellSizeDeg: number | null
  metadata: HazardMetadata | null
}
