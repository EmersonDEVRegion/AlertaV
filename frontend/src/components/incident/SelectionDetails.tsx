import { Suspense, lazy, memo, useEffect, useMemo } from 'react'
import type { SeismicEvent } from '@/api/seismicTypes'
import type { Incident } from '@/api/types'
import type { WaterCut } from '@/api/waterCutTypes'
import { useSelectedFire } from '@/hooks/useSelectedFire'
import {
  clearIf,
  useSelectedIncidentCode,
  useSelectedSeismicId,
  useSelectedWaterCutId,
} from '@/lib/selectionStore'
import { loadIncidentSheet, loadSeismicCard, loadWaterCutCard } from '@/lib/lazyChunks'

// Chunks aparte: sólo se descargan cuando alguien toca algo (o antes, en un
// momento libre; ver `prefetchWhenIdle` en `App`).
const IncidentSheet = lazy(loadIncidentSheet)
const SeismicCard = lazy(loadSeismicCard)
const WaterCutCard = lazy(loadWaterCutCard)

interface SelectionDetailsProps {
  /** Los incidentes que se están mostrando: con las capas y empresas encendidas. */
  incidents: readonly Incident[]
  /** Los sismos que se están mostrando: con el filtro de relevancia aplicado. */
  seismic: readonly SeismicEvent[]
  /** Los cortes de agua vigentes, si su fila está encendida; si no, vacío. */
  waterCuts: readonly WaterCut[]
}

const closeIncident = () => clearIf('incident')
const closeSeismic = () => clearIf('seismic')
const closeWaterCut = () => clearIf('water')

/**
 * La ficha de lo seleccionado: un incidente, un sismo o un corte de agua.
 *
 * Se suscribe sola a la selección, así que abrir o cerrar una ficha repinta
 * esto y nada más. Busca en lo que se está MOSTRANDO: si la capa del incidente
 * está apagada, la ficha no se abre sobre un pin invisible.
 */
export const SelectionDetails = memo(function SelectionDetails({
  incidents,
  seismic,
  waterCuts,
}: SelectionDetailsProps) {
  const code = useSelectedIncidentCode()
  const usgsId = useSelectedSeismicId()
  const waterId = useSelectedWaterCutId()

  const incident = useMemo(
    () => (code === null ? null : (incidents.find((i) => i.code === code) ?? null)),
    [incidents, code],
  )
  const quake = useMemo(
    () => (usgsId === null ? null : (seismic.find((e) => e.usgs_id === usgsId) ?? null)),
    [seismic, usgsId],
  )

  const cut = useMemo(
    () => (waterId === null ? null : (waterCuts.find((c) => c.id === waterId) ?? null)),
    [waterCuts, waterId],
  )

  /*
   * Un corte de agua deja de existir sin aviso: Esval lo saca de la lista y el
   * siguiente sondeo ya no lo trae (o se apagó su fila). A diferencia de un
   * incidente, que pasa a `controlled` y sigue ahí, acá no queda nada que
   * mostrar. Se suelta la selección: si no, el botón de reporte seguiría
   * escondido en el teléfono por una tarjeta que ya no está.
   */
  useEffect(() => {
    if (waterId !== null && cut === null) clearIf('water')
  }, [waterId, cut])

  // El viento sólo existe para un incendio. Mismo pedido que el cono del mapa:
  // react-query lo resuelve con una sola llamada.
  const { wind, cone, windLoading, windError } = useSelectedFire(incidents)

  if (incident) {
    return (
      <Suspense fallback={null}>
        <IncidentSheet
          incident={incident}
          onClose={closeIncident}
          wind={wind}
          windCone={cone}
          windLoading={windLoading}
          windError={windError}
        />
      </Suspense>
    )
  }

  if (quake) {
    return (
      <Suspense fallback={null}>
        <SeismicCard event={quake} onClose={closeSeismic} />
      </Suspense>
    )
  }

  if (cut) {
    return (
      <Suspense fallback={null}>
        <WaterCutCard cut={cut} onClose={closeWaterCut} />
      </Suspense>
    )
  }

  return null
})
