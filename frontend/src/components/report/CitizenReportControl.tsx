import { Suspense, lazy, memo, useState } from 'react'
import { Button } from '@/components/ui/primitives'
import { cn } from '@/lib/cn'
import { useSelection } from '@/lib/selectionStore'

// El formulario (categorías, geolocalización, envío) se descarga al abrirlo.
const CitizenReportModal = lazy(loadCitizenReportModal)
import { loadCitizenReportModal } from '@/lib/lazyChunks'

interface CitizenReportControlProps {
  /**
   * Oculta el botón solo en teléfono, además de lo que ya lo oculta solo: la
   * ficha de un incidente o el radar abiertos (ver abajo).
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
 *
 * Sobre el ícono: el 🚨 anterior era un emoji, y un emoji lo dibuja la fuente
 * del sistema — distinto en Android, en iOS y en Windows, con su propio color
 * fijo que no responde al tema y sin alineación fiable con el texto. El
 * triángulo vectorial hereda `currentColor` y mide siempre lo mismo.
 */
export const CitizenReportControl = memo(function CitizenReportControl({
  hiddenOnMobile = false,
}: CitizenReportControlProps) {
  const [open, setOpen] = useState(false)
  /*
   * En teléfono la ficha del incidente y el radar ocupan el tercio inferior, y
   * el botón quedaría flotando encima. En `md` son paneles laterales y no hay
   * colisión. Se lee del store de selección —no llega por props— para que
   * abrir una ficha no tenga que pasar por `App`.
   */
  const selection = useSelection()
  // Lo que ocupa el borde inferior del teléfono: la ficha, el radar y la
  // tarjeta del corte de agua.
  const hidden =
    hiddenOnMobile ||
    selection.kind === 'incident' ||
    selection.kind === 'radar' ||
    selection.kind === 'water'

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
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={cn(
          'absolute bottom-[calc(2.25rem+env(safe-area-inset-bottom))] left-1/2 z-30 -translate-x-1/2',
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
          // Escondido sólo en teléfono con `max-md:hidden`, y no con
          // `hidden md:inline-flex`: la base de `Button` ya trae `inline-flex`,
          // y en la hoja que genera Tailwind v4 `.inline-flex` va DESPUÉS de
          // `.hidden`, así que a igual especificidad ganaba y el botón nunca se
          // escondía. La variante `max-md:` vive en una media query, que sale
          // al final de la hoja y gana sin pelear por el orden.
          hidden && 'max-md:hidden',
        )}
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.2}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
          className="size-[18px]"
        >
          <path d="M12 9v4" />
          <path d="M12 17h.01" />
          <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
        </svg>
        Reportar emergencia
      </Button>

      {open && (
        <Suspense fallback={null}>
          <CitizenReportModal onClose={() => setOpen(false)} />
        </Suspense>
      )}
    </>
  )
})
