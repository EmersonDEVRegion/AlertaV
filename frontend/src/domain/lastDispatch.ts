import type { Incident } from '@/api/types'

/**
 * El último despacho de Bomberos que conoce la app, en ms.
 *
 * # Para qué
 *
 * Un mapa sin emergencias nuevas se ve igual si el día está tranquilo que si el
 * motor dejó de recibir despachos (§H, 30-09-2026: «aparecen las mismas 3
 * emergencias»). La línea «Último despacho de Bomberos: 10:01 · hace 2 h»
 * separa las dos lecturas sin abrir `/collectors/health`.
 *
 * # Qué hora se toma
 *
 * `first_seen_at` de los incidentes que tienen a `bomberos` entre sus fuentes:
 * es la hora que muestra el historial en cada fila, así que las dos cifras
 * calzan. Un despacho que se suma a un incidente previo (otra fuente llegó
 * antes) no mueve esta hora; es un caso raro y la cifra sigue siendo una cota
 * honesta de «desde cuándo no llega nada nuevo».
 *
 * Recibe TODOS los incidentes de la consulta (48 h), sin los filtros de capa:
 * apagar la capa de incendios no cambia cuándo despachó Bomberos.
 */
export function lastBomberosDispatch(incidents: readonly Incident[]): number | null {
  let last: number | null = null
  for (const incident of incidents) {
    if (!incident.sources.includes('bomberos')) continue
    const at = Date.parse(incident.first_seen_at)
    if (Number.isNaN(at)) continue
    if (last === null || at > last) last = at
  }
  return last
}
