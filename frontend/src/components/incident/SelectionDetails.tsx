import { Suspense, lazy, memo, useMemo } from 'react'
import type { SeismicEvent } from '@/api/seismicTypes'
import type { Incident } from '@/api/types'
import { useSelectedFire } from '@/hooks/useSelectedFire'
import {
  clearIf,
  useSelectedIncidentCode,
  useSelectedSeismicId,
} from '@/lib/selectionStore'
import { loadIncidentSheet, loadSeismicCard } from '@/lib/lazyChunks'

// Chunks aparte: sólo se descargan cuando alguien toca algo (o antes, en un
// momento libre; ver `prefetchWhenIdle` en `App`).
const IncidentSheet = lazy(loadIncidentSheet)
const SeismicCard = lazy(loadSeismicCard)

interface SelectionDetailsProps {
  /** Los incidentes que se están mostrando: con las capas y empresas encendidas. */
  incidents: readonly Incident[]
  /** Los sismos que se están mostrando: con el filtro de relevancia aplicado. */
  seismic: readonly SeismicEvent[]
}

const closeIncident = () => clearIf('incident')
const closeSeismic = () => clearIf('seismic')

/**
 * La ficha de lo seleccionado: un incidente o un sismo.
 *
 * Se suscribe sola a la selección, así que abrir o cerrar una ficha repinta
 * esto y nada más. Busca en lo que se está MOSTRANDO: si la capa del incidente
 * está apagada, la ficha no se abre sobre un pin invisible.
 */
export const SelectionDetails = memo(function SelectionDetails({
  incidents,
  seismic,
}: SelectionDetailsProps) {
  const code = useSelectedIncidentCode()
  const usgsId = useSelectedSeismicId()

  const incident = useMemo(
    () => (code === null ? null : (incidents.find((i) => i.code === code) ?? null)),
    [incidents, code],
  )
  const quake = useMemo(
    () => (usgsId === null ? null : (seismic.find((e) => e.usgs_id === usgsId) ?? null)),
    [seismic, usgsId],
  )

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

  return null
})
