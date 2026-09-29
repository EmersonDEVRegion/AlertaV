/**
 * Lo que no se ve en el primer cuadro, en chunks aparte.
 *
 * La ficha del incidente (con su auditoría de confianza, el detalle del corte y
 * el aviso de congestión), la tarjeta del sismo y el modal de reporte sólo
 * aparecen cuando alguien toca algo. Cargarlos con el resto retrasaba el primer
 * cuadro a cambio de nada.
 *
 * Los importadores viven acá, juntos, por dos motivos: `React.lazy` los usa para
 * cargar a demanda y `prefetchWhenIdle` los usa para adelantarlos apenas el
 * navegador queda libre, así que el primer toque no espera la red. Sin señal
 * funcionan igual: Workbox precachea todos los `.js` del build.
 */

export const loadIncidentSheet = () =>
  import('@/components/incident/IncidentSheet').then((m) => ({ default: m.IncidentSheet }))

export const loadSeismicCard = () =>
  import('@/components/incident/SeismicCard').then((m) => ({ default: m.SeismicCard }))

export const loadCitizenReportModal = () =>
  import('@/components/report/CitizenReportModal').then((m) => ({
    default: m.CitizenReportModal,
  }))

export const loadRadarControls = () => import('@/components/vehicles/RadarControls')

type IdleWindow = Window & {
  requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number
}

/**
 * Adelanta los chunks cuando el navegador queda libre. Safari no tiene
 * `requestIdleCallback`: ahí se espera un par de segundos, que es cuando el
 * mapa ya pintó su primer cuadro.
 */
export function prefetchWhenIdle(loaders: readonly (() => Promise<unknown>)[]): void {
  const run = () => {
    for (const load of loaders) void load().catch(() => {})
  }
  const idle = (window as IdleWindow).requestIdleCallback
  if (idle) idle(run, { timeout: 5_000 })
  else window.setTimeout(run, 2_000)
}
