import { Suspense, lazy, memo, useState } from 'react'
import type { ReactNode } from 'react'
import { Button } from '@/components/ui/primitives'
import { cn } from '@/lib/cn'
import { useRadarOpen } from '@/lib/selectionStore'

// El formulario (categorías, geolocalización, envío) se descarga al abrirlo.
const CitizenReportModal = lazy(loadCitizenReportModal)
import { loadCitizenReportModal } from '@/lib/lazyChunks'

interface CitizenReportControlProps {
  /**
   * - `floating` (escritorio): la píldora sobre el mapa, al centro de lo que
   *   deja libre la columna.
   * - `inline` (teléfono): un botón compacto en el encabezado de la hoja
   *   inferior. Flotando, chocaba con la atribución del mapa y se partía en
   *   dos líneas a 390 px; en la hoja está siempre a mano —asomada, a media
   *   pantalla o completa— y nunca tapa nada.
   */
  placement?: 'floating' | 'inline'
}

/**
 * Botón de reporte ciudadano y su modal.
 *
 * Sobre la posición en escritorio: MapLibre ya ocupa arriba a la derecha (zoom
 * y geolocalización), abajo a la izquierda (escala) y abajo a la derecha
 * (atribución), y la columna ocupa el borde izquierdo. El centro inferior del
 * mapa visible es el único borde libre.
 *
 * Sobre el `z-index`: la columna vive en `z-10`. El botón va en `z-30`, y el
 * modal se monta en un portal sobre `document.body` con `z-50`, fuera del
 * contexto de apilamiento del mapa.
 *
 * Sobre el ícono: el 🚨 anterior era un emoji, y un emoji lo dibuja la fuente
 * del sistema — distinto en Android, en iOS y en Windows, con su propio color
 * fijo que no responde al tema y sin alineación fiable con el texto. El
 * triángulo vectorial hereda `currentColor` y mide siempre lo mismo.
 */
export const CitizenReportControl = memo(function CitizenReportControl({
  placement = 'floating',
}: CitizenReportControlProps) {
  const [open, setOpen] = useState(false)
  const modal = open && (
    <Suspense fallback={null}>
      <CitizenReportModal onClose={() => setOpen(false)} />
    </Suspense>
  )

  if (placement === 'inline') {
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
  }

  return <FloatingReport open={open} onOpen={() => setOpen(true)}>{modal}</FloatingReport>
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

function FloatingReport({
  open,
  onOpen,
  children,
}: {
  open: boolean
  onOpen: () => void
  children: ReactNode
}) {
  // El radar ocupa el borde derecho y el inferior; el botón no compite con él.
  const radarOpen = useRadarOpen()

  return (
    <>
      {/*
        Sombra en dos capas y sin tinte de color.

        La versión anterior usaba una sombra roja saturada
        (`0 6px 24px rgba(220,38,38,0.45)`), que es el recurso que hace que un
        botón se vea de plantilla: el color se derrama sobre el mapa y compite
        con los propios marcadores de emergencia, que son rojos y naranjas de
        verdad. Una sombra neutra separa el botón del terreno sin teñirlo.

        Las tres capas de profundidad —reposo, hover, activo— van en la variante
        `urgent` de `Button`, no acá: la respuesta táctil tiene que ser la misma
        en toda la aplicación.
      */}
      <Button
        variant="urgent"
        size="fab"
        onClick={onOpen}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={cn(
          // Al centro del mapa visible, a la derecha de la columna de 22 rem
          // (+ 0,75 de margen): 50 % + la mitad de 22,75 rem.
          'absolute bottom-[calc(2.25rem+env(safe-area-inset-bottom))] left-[calc(50%+11.375rem)] z-30 -translate-x-1/2',
          // Tres transformaciones se cruzan en este botón y ninguna pisa a otra,
          // pero por motivos distintos según la versión de Tailwind:
          //
          //   - el centrado (`-translate-x-1/2`, acá) y la elevación al apuntar
          //     (`-translate-y-0.5`, en el tamaño `fab`) escriben LA MISMA
          //     propiedad `translate`, y conviven porque cada una sólo fija su
          //     variable —`--tw-translate-x` / `--tw-translate-y`— y la
          //     declaración las lee juntas;
          //   - la reducción al pulsar (`active:scale`) escribe `scale`, que en
          //     Tailwind v4 es una propiedad aparte y no `transform`.
          //
          // De ahí que la transición del primitivo nombre `translate` y `scale`
          // por separado: `transform` no lo declara nadie.
          //
          // Con el radar abierto se esconde con `invisible` y no con `hidden`:
          // la base de `Button` ya trae `inline-flex`, y en la hoja que genera
          // Tailwind v4 `.inline-flex` va DESPUÉS de `.hidden`, así que a igual
          // especificidad ganaba y el botón nunca se escondía.
          radarOpen && 'invisible',
        )}
      >
        <AlertTriangle className="size-[18px]" />
        Reportar emergencia
      </Button>

      {children}
    </>
  )
}
