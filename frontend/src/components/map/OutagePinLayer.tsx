import { useMemo } from 'react'
import type { Incident } from '@/api/types'
import { OutagePin } from './OutagePin'

/**
 * Tope de marcadores DOM simultáneos.
 *
 * Un temporal deja cientos de circuitos caídos a la vez. Cada `<Marker>` es un
 * nodo del DOM que MapLibre reposiciona en cada frame del paneo, así que pasado
 * cierto número el mapa se vuelve pesado justo cuando más se necesita fluido.
 * Se priorizan los cortes con más clientes afectados: si hay que recortar, que
 * sobrevivan los que afectan a más gente.
 */
export const MAX_OUTAGE_PINS = 150

interface OutagePinLayerProps {
  outages: readonly Incident[]
  selectedCode: string | null
  onSelect: (code: string) => void
}

export function OutagePinLayer({
  outages,
  selectedCode,
  onSelect,
}: OutagePinLayerProps) {
  const visible = useMemo(() => {
    if (outages.length <= MAX_OUTAGE_PINS) return outages

    const ranked = [...outages].sort(
      (a, b) => (b.outage?.affected_clients ?? 0) - (a.outage?.affected_clients ?? 0),
    )
    const top = ranked.slice(0, MAX_OUTAGE_PINS)

    // El seleccionado no puede desaparecer por el recorte: su ficha está
    // abierta y el pin es lo que la ancla al mapa.
    if (selectedCode && !top.some((i) => i.code === selectedCode)) {
      const chosen = outages.find((i) => i.code === selectedCode)
      if (chosen) top[top.length - 1] = chosen
    }
    return top
  }, [outages, selectedCode])

  return (
    <>
      {visible.map((incident) => (
        <OutagePin
          key={incident.code}
          incident={incident}
          selected={incident.code === selectedCode}
          onSelect={onSelect}
        />
      ))}
    </>
  )
}
