/**
 * Simbología del tránsito del MTT (@TTIValparaiso).
 *
 * Una línea sobre la calle por cada tramo, con el color de lo que pasa en él;
 * un punto cuando el MTT nombró sólo un lugar (un semáforo, una esquina).
 *
 *   congestión       rosa      lo que más cambia qué hacer
 *   tránsito lento   ámbar     lo más frecuente: se ve, pero no grita
 *   semáforo         violeta   un cruce sin control
 *   pista ocupada    naranja   un vehículo en panne, una faena
 *   marcha           cian      gente en la calzada, se mueve
 *   tránsito normal  verde     el MTT confirmó que se despejó
 *
 * El rosa es el de «ruta cortada» (`roadClosureSymbology`): los dos responden
 * «¿puedo pasar?». El verde es el único verde del mapa: es la buena noticia, y
 * dura poco (45 min) para no dejar la calle «verde» cuando ya no se sabe.
 *
 * Contexto, no emergencia: ninguna de estas líneas abre ficha. El detalle está
 * en la lista de «Ahora».
 */

import type { TransitoSubtipo } from '@/api/transito'

export type Theme = 'light' | 'dark'

export const TRANSITO_PALETTE: Record<Theme, Record<TransitoSubtipo, string>> = {
  light: {
    congestion: '#e11d48',
    lento: '#d97706',
    semaforo: '#7c3aed',
    pista_ocupada: '#ea580c',
    marcha: '#0891b2',
    normalidad: '#16a34a',
  },
  dark: {
    congestion: '#fb7185',
    lento: '#fbbf24',
    semaforo: '#a78bfa',
    pista_ocupada: '#fb923c',
    marcha: '#22d3ee',
    normalidad: '#4ade80',
  },
}

/** Etiquetas cortas de los chips de «Capas» y de la lista. */
export const TRANSITO_ETIQUETA: Record<TransitoSubtipo, string> = {
  congestion: 'Congestión',
  lento: 'Lento',
  semaforo: 'Semáforos',
  pista_ocupada: 'Pista ocupada',
  marcha: 'Marchas',
  normalidad: 'Normal',
}

/** En la lista, lo que cambia qué hacer va primero; lo normal, al final. */
export const TRANSITO_ORDEN: Record<TransitoSubtipo, number> = {
  pista_ocupada: 0,
  marcha: 1,
  congestion: 2,
  semaforo: 3,
  lento: 4,
  normalidad: 5,
}
