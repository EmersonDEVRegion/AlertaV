import { useState } from 'react'
import { CitizenReportModal } from './CitizenReportModal'

interface CitizenReportControlProps {
  /**
   * Oculta el botón solo en teléfono. Existe porque `IncidentSheet` ocupa el
   * tercio inferior en esa medida y el botón quedaría flotando sobre la ficha.
   * En `md` la ficha es un panel lateral y no hay colisión.
   */
  hiddenOnMobile?: boolean
}

/**
 * Botón flotante de reporte ciudadano y su modal.
 *
 * Sobre la posición: MapLibre ya ocupa arriba a la derecha (zoom y
 * geolocalización), abajo a la izquierda (escala) y abajo a la derecha
 * (atribución); `MapLegend` ocupa arriba a la izquierda. El centro inferior es
 * el único borde libre, y además es donde llega el pulgar.
 *
 * Sobre el `z-index`: la leyenda vive en `z-10` y la ficha del incidente en
 * `z-20`. El botón va en `z-30` para no quedar debajo de ninguno, y el modal se
 * monta en un portal sobre `document.body` con `z-50`, fuera del contexto de
 * apilamiento del mapa.
 */
export function CitizenReportControl({ hiddenOnMobile = false }: CitizenReportControlProps) {
  const [open, setOpen] = useState(false)

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={`
          absolute bottom-[calc(2.25rem_+_env(safe-area-inset-bottom))] left-1/2 z-30
          -translate-x-1/2 items-center gap-2 rounded-full bg-red-600 px-5 py-3
          text-sm font-bold text-white shadow-[0_6px_24px_rgba(220,38,38,0.45)]
          ring-1 ring-red-700/20 transition
          hover:bg-red-700 hover:shadow-[0_8px_28px_rgba(220,38,38,0.55)]
          active:scale-[0.97]
          focus:outline-none focus-visible:ring-2 focus-visible:ring-red-300 focus-visible:ring-offset-2
          ${hiddenOnMobile ? 'hidden md:flex' : 'flex'}
        `}
      >
        <span aria-hidden className="text-base leading-none">🚨</span>
        Reportar emergencia
      </button>

      {open && <CitizenReportModal onClose={() => setOpen(false)} />}
    </>
  )
}
