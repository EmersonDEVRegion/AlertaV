import { Suspense, lazy, memo, useState } from 'react'
import { Button } from '@/components/ui/primitives'

// El formulario (categorías, geolocalización, envío) se descarga al abrirlo.
const CitizenReportModal = lazy(loadCitizenReportModal)
import { loadCitizenReportModal } from '@/lib/lazyChunks'

/**
 * «Reportar»: un botón compacto en el encabezado de la columna (escritorio) o
 * de la hoja inferior (teléfono), junto a «N en curso».
 *
 * Hasta §D, en escritorio era una píldora flotante sobre el mapa, centrada en
 * lo que dejaba libre la columna: a la vista no estaba centrada en nada, y con
 * un panel de la barra abierto quedaba encima de él. En el encabezado está
 * siempre a mano, en el mismo lugar en las dos pantallas, y nunca tapa nada.
 */
export const CitizenReportControl = memo(function CitizenReportControl() {
  const [open, setOpen] = useState(false)
  const modal = open && (
    <Suspense fallback={null}>
      <CitizenReportModal onClose={() => setOpen(false)} />
    </Suspense>
  )

  return (
    <>
      <Button
        variant="urgent"
        size="sm"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="shrink-0 gap-1.5 rounded-full px-3"
      >
        <AlertTriangle className="size-3.5" />
        Reportar
      </Button>
      {modal}
    </>
  )
})

function AlertTriangle({ className }: { className: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
    >
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
    </svg>
  )
}
