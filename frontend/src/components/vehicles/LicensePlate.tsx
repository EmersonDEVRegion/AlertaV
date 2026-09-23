import { formatPatente } from '@/domain/vehicleFeed'
import { cn } from '@/lib/cn'

/**
 * La patente, dibujada como placa.
 *
 * # Por qué colores literales y no tokens
 *
 * Una placa chilena es blanca con caracteres negros en cualquier tema, igual
 * que el pulgar del `Switch` es blanco sobre cualquier riel: es contraste
 * físico, no cromo. Con tokens, en oscuro saldría una placa gris pizarra con
 * letras claras, que ya no se lee como placa. El leve degradado y el anillo
 * interior son lo que la hacen ver de metal estampado y no como una etiqueta.
 *
 * El radio también es literal. Los tres roles de `index.css` (control,
 * superficie, píldora) son para cromo. Con 8 px, una pieza de 34 px de alto se
 * lee como botón; una placa tiene esquinas apenas redondeadas.
 *
 * # Sin patente
 *
 * Todos los abandonados y unos pocos robados llegan sin patente (GBV no la
 * publicó, o publicó algo que no es una patente chilena). Se dibuja el hueco
 * punteado y no se inventa nada: la tarjeta pasa la jerarquía a la marca.
 *
 * El ancho es fijo en los dos casos, para que las placas formen una columna y
 * el ojo recorra la lista hacia abajo sin buscar dónde empieza cada una.
 */

interface LicensePlateProps {
  patente: string | null
  className?: string
}

const BOX = 'inline-flex w-[5.25rem] shrink-0 flex-col items-center justify-center rounded-[4px] py-[3px]'

export function LicensePlate({ patente, className }: LicensePlateProps) {
  if (!patente) {
    return (
      <span
        className={cn(BOX, 'h-[2.125rem] border border-dashed border-line-strong text-ink-faint', className)}
        title="GBV no publicó una patente válida para este aviso"
      >
        <span className="text-[9px] font-semibold leading-none tracking-[0.12em]">S/PATENTE</span>
      </span>
    )
  }

  const formatted = formatPatente(patente)

  return (
    <span
      className={cn(
        BOX,
        'h-[2.125rem] bg-linear-to-b from-white to-[#e7e7ea] text-[#0a0a0a]',
        'shadow-[inset_0_0_0_1px_rgb(0_0_0/0.28),inset_0_0_0_2.5px_rgb(255_255_255/0.9),0_1px_2px_rgb(0_0_0/0.25)]',
        className,
      )}
    >
      {/* El lector de pantalla lee la patente de corrido, sin los puntos. */}
      <span className="sr-only">Patente {patente.split('').join(' ')}</span>
      <span
        aria-hidden
        className="font-mono text-[13px] font-bold leading-none tracking-[0.06em]"
      >
        {formatted}
      </span>
      <span aria-hidden className="mt-[3px] text-[6px] font-semibold leading-none tracking-[0.32em] opacity-55">
        CHILE
      </span>
    </span>
  )
}
