import { memo } from 'react'
import { Marker } from 'react-map-gl/maplibre'
import type { Incident } from '@/api/types'
import { providerOf, providerStyle } from '@/domain/powerSymbology'
import { isClosed } from '@/domain/symbology'

/**
 * Pin de un corte de suministro.
 *
 * # Por qué `<Marker>` y no una capa GeoJSON
 *
 * El resto del mapa dibuja círculos con capas de MapLibre, que escalan a miles
 * de puntos sin costo. Los cortes usan `<Marker>` —un nodo DOM real por pin,
 * el equivalente en MapLibre del `divIcon` de Leaflet— porque necesitan lo que
 * una capa no da: forma de gota, un glifo dentro, y estilos de Tailwind con su
 * variante `dark:`. Con una capa habría que registrar imágenes en el estilo y
 * volver a hacerlo en cada `setStyle`, que es justo lo que ocurre al cambiar de
 * tema.
 *
 * El precio es que cada pin es DOM: cientos de marcadores degradan el paneo.
 * Por eso `OutagePinLayer` impone un tope y avisa cuando lo aplica.
 *
 * # La forma
 *
 * Una gota de 26×26 rotada 45°, con el glifo contrarrotado para que quede
 * derecho, y `anchor="bottom"` para que la punta —no el centro— apoye en las
 * coordenadas. Un marcador centrado señalaría un punto desplazado media altura
 * hacia arriba, que en zoom de calle son varias cuadras.
 */

interface OutagePinProps {
  incident: Incident
  selected: boolean
  onSelect: (code: string) => void
}

function OutagePinComponent({ incident, selected, onSelect }: OutagePinProps) {
  const style = providerStyle(providerOf(incident))
  const closed = isClosed(incident.status)

  return (
    <Marker
      longitude={incident.lon}
      latitude={incident.lat}
      anchor="bottom"
      onClick={(event) => {
        // Sin esto el clic sigue hasta el lienzo y el handler del mapa,
        // que no ve ninguna feature bajo el cursor, deselecciona en el acto.
        event.originalEvent.stopPropagation()
        onSelect(incident.code)
      }}
    >
      <button
        type="button"
        aria-label={`Corte de suministro de ${style.label} en ${incident.commune ?? 'la región'}`}
        className={
          'group relative block cursor-pointer transition-transform ' +
          (selected ? 'scale-115' : 'hover:scale-110')
        }
        style={{ opacity: closed ? 0.55 : 1 }}
      >
        <span
          aria-hidden
          className={
            // El borde recorta el pin contra el terreno, así que sigue al fondo de la
            // aplicación —claro sobre mapa claro, oscuro sobre mapa oscuro—, no a una
            // superficie de panel.
            'block size-[26px] rounded-full rounded-bl-none border-2 border-app shadow-md ' +
            // `ring-ink` es el color de máximo contraste del tema: oscuro sobre claro
            // y claro sobre oscuro. Un `slate-900` fijo se perdía en el mapa nocturno.
            (selected ? 'ring-2 ring-ink' : '')
          }
          style={{ backgroundColor: style.color, transform: 'rotate(45deg)' }}
        />
        {/* El glifo se contrarrota para quedar derecho dentro de la gota. */}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-0 grid place-items-center pb-0.5 text-[13px] leading-none"
          style={{ color: style.onColor }}
        >
          ⚡
        </span>
      </button>
    </Marker>
  )
}

/**
 * `memo` importa de verdad acá: el polling reemplaza el arreglo de incidentes
 * cada minuto y sin esto se recrearía cada nodo DOM del mapa. La comparación
 * mira sólo lo que afecta al render.
 */
export const OutagePin = memo(
  OutagePinComponent,
  (a, b) =>
    a.incident.code === b.incident.code &&
    a.incident.lat === b.incident.lat &&
    a.incident.lon === b.incident.lon &&
    a.incident.status === b.incident.status &&
    a.incident.outage?.provider === b.incident.outage?.provider &&
    a.selected === b.selected,
)
