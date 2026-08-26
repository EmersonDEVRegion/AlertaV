import { Fragment } from 'react'
import type { Incident } from '@/api/types'
import { layerOf } from '@/domain/families'
import type { IncidentLayerKey } from '@/domain/families'
import { STATUS_LABEL, TYPE_LABEL } from '@/domain/labels'
import { styleFor } from '@/domain/palette'
import { isClosed } from '@/domain/symbology'
import { formatRelative, formatTime } from '@/lib/format'
import { cn } from '@/lib/cn'

/**
 * Tarjeta que se monta dentro del popup de MapLibre.
 *
 * # Qué asume
 *
 * Que el contenedor de MapLibre ya no aporta nada: sin fondo, sin relleno, sin
 * radio y sin flecha. Ese vaciado vive en `index.css`, y sin él este componente
 * se ve como una tarjeta oscura sobre un rectángulo blanco. Las dos piezas van
 * juntas.
 *
 * # Por qué `.surface-floating` y no una mezcla nueva
 *
 * El fondo semiopaco, el radio de superficie, la sombra, el borde interior y el
 * desenfoque ya están resueltos en esa clase de composición, que es la misma que
 * usan el panel lateral y la leyenda. Un popup con su propio `bg-…/90` y su
 * propio `backdrop-blur-md` sería un tono más de los doce que se quiso eliminar,
 * y además desincronizaría el desenfoque: `--blur-panel` es 8 px a propósito
 * —`backdrop-filter` obliga al compositor a remuestrear el fondo en cada cuadro
 * y debajo hay un mapa que repinta al desplazarse—, mientras que
 * `backdrop-blur-md` son 12 px fijos.
 *
 * # El acento
 *
 * Sale de `styleFor()`, que es el único lugar que decide qué paleta le toca a
 * cada familia y qué pasa cuando el incidente ya cerró. Acá no se elige color:
 * si se eligiera, un corte de Chilquinta podría quedar rojo en el pin y naranja
 * en el popup, y el mapa diría dos cosas distintas del mismo hecho.
 *
 * # Densidad
 *
 * Cabecera, cuerpo y métricas, sin un `div` de envoltura por fila: la lista de
 * datos es un `<dl>` en rejilla de dos columnas donde `dt` y `dd` son hijos
 * directos. Trece nodos en el caso más cargado.
 */

// ---------------------------------------------------------------------------
// Iconografía
// ---------------------------------------------------------------------------

/**
 * Geometría de Lucide, dibujada acá.
 *
 * Son los mismos trazos de `lucide-react` (`zap`, `flame`, `triangle-alert`,
 * `circle-alert`, `x`) con sus mismos atributos de trazo, pero sin la
 * dependencia — que es la línea que ya se trazó con `tailwind-merge` en
 * `lib/cn.ts`: en una PWA que se abre en una emergencia con red de teléfono, un
 * paquete entero por cinco glifos no se paga. El resto del proyecto dibuja sus
 * iconos igual (ver `primitives/Sheet.tsx`).
 *
 * Si más adelante se instala `lucide-react`, el reemplazo es sustituir este
 * bloque por `import { Zap, Flame, TriangleAlert, CircleAlert, X } from
 * 'lucide-react'`: los nombres y el tamaño coinciden a propósito.
 *
 * `traffic` usa el triángulo de advertencia y no un auto deliberadamente: es el
 * glifo que Waze y Google Maps usan para un incidente vial, y a 16 px un auto
 * se vuelve una mancha.
 */
const ICON: Record<IncidentLayerKey | 'close', readonly string[]> = {
  power: [
    'M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z',
  ],
  fire: [
    'M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z',
  ],
  traffic: [
    'm21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3',
    'M12 9v4',
    'M12 17h.01',
  ],
  otros: ['M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0', 'M12 8v4', 'M12 16h.01'],
  close: ['M18 6 6 18', 'M6 6l12 12'],
}

function Icon({
  name,
  className,
  color,
}: {
  name: IncidentLayerKey | 'close'
  className: string
  color?: string
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
      style={color ? { color } : undefined}
    >
      {ICON[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  )
}

// ---------------------------------------------------------------------------
// Datos del pie
// ---------------------------------------------------------------------------

const clientFmt = new Intl.NumberFormat('es-CL')

/**
 * Las métricas que se muestran, por familia.
 *
 * Nada se rellena con un cero: los feeds publican el corte antes de contar
 * clientes, y «0 clientes afectados» sería una afirmación falsa donde el dato
 * simplemente no llegó. Una fila sin valor se omite; la única que se muestra
 * vacía es la de clientes en un corte, porque su ausencia es en sí informativa.
 */
function metricsOf(incident: Incident): readonly (readonly [string, string])[] {
  const rows: [string, string][] = []
  const outage = incident.outage

  if (outage) {
    rows.push([
      'Clientes afectados',
      outage.affected_clients === null
        ? 'sin dato'
        : clientFmt.format(outage.affected_clients),
    ])
    if (outage.estimated_restoration) {
      rows.push(['Reposición estimada', formatTime(outage.estimated_restoration)])
    }
  }

  rows.push(['Hora de inicio', formatTime(incident.first_seen_at)])
  rows.push(['Última señal', formatRelative(incident.last_seen_at)])

  // Cuatro filas es el techo: más arriba de eso el popup deja de ser un vistazo
  // y compite con la ficha completa, que es donde va el detalle.
  return rows.slice(0, 4)
}

// ---------------------------------------------------------------------------

export interface EmergencyPopupProps {
  incident: Incident
  /** Cierra el popup. Lo provee quien lo montó; ver `IncidentPopup.tsx`. */
  onClose: () => void
  /** Abre la ficha completa. Opcional: sin esto el popup es sólo lectura. */
  onOpenDetail?: (code: string) => void
}

export function EmergencyPopup({
  incident,
  onClose,
  onOpenDetail,
}: EmergencyPopupProps) {
  const layer = layerOf(incident.type)
  const style = styleFor(incident)
  const closed = isClosed(incident.status)

  // Dónde. El sector del corte es más preciso que la comuna cuando existe; el
  // título del incidente lo es para el resto de las familias.
  const place =
    incident.outage?.sector ?? incident.title ?? incident.commune ?? 'Ubicación sin detallar'

  const metrics = metricsOf(incident)

  return (
    <article
      // `surface-floating` trae fondo, radio, sombra, borde interior y desenfoque.
      // Acá sólo se agrega el ancho y el relleno que el contenedor de MapLibre
      // dejó de aportar.
      className="surface-floating w-70 max-w-[calc(100vw-2rem)] p-3 text-ink"
      aria-label={`${TYPE_LABEL[incident.type]} en ${place}`}
      style={closed ? { opacity: 0.92 } : undefined}
    >
      <header className="flex items-start gap-2">
        <Icon name={layer} className="mt-px size-4 shrink-0" color={style.color} />
        <h2 className="min-w-0 flex-1 text-[13px] leading-tight font-semibold">
          {TYPE_LABEL[incident.type]}
        </h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Cerrar"
          // El margen negativo devuelve el área táctil sin abrir un hueco: el
          // botón mide 24 px de blanco a blanco pero ocupa lo que el icono.
          className="-m-1 shrink-0 cursor-pointer rounded-control p-1 text-ink-faint transition-colors hover:bg-hover hover:text-ink"
        >
          <Icon name="close" className="size-3.5" />
        </button>
      </header>

      <p className="mt-1 line-clamp-2 text-sm text-ink-muted">{place}</p>

      {/*
        Rejilla de dos columnas con `dt` y `dd` como hijos directos: una fila de
        métrica no cuesta ningún nodo de envoltura. `Fragment` agrupa el par sin
        aparecer en el DOM.

        `tabular-nums` acá no es redundante aunque `body` ya lo declare: el
        `font` de `.maplibregl-map` reinicia `font-variant-numeric` dentro del
        mapa. Ver la nota del bloque de popup en `index.css`.
      */}
      <dl className="mt-2.5 grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 border-t border-line pt-2.5 text-[11px]">
        {metrics.map(([label, value]) => (
          <Fragment key={label}>
            <dt className="text-ink-faint">{label}</dt>
            <dd className="text-right font-medium tabular-nums">{value}</dd>
          </Fragment>
        ))}
      </dl>

      <footer className="mt-2.5 flex flex-wrap items-center gap-1.5">
        <span
          className={cn(
            'rounded-full px-2 py-0.5 text-[10px] font-medium',
            // El chip de la paleta afirma QUÉ es (empresa que repone, tramo de
            // confianza). No se reinventa acá.
            style.chip,
          )}
        >
          {style.label}
        </span>
        <span
          className={cn(
            'rounded-full px-2 py-0.5 text-[10px] font-medium',
            closed ? 'bg-sunken text-ink-muted' : 'bg-danger-bg text-danger-ink',
          )}
        >
          {/* `STATUS_LABEL` y no un «En curso» escrito acá: `stale` se rotula
              «Sin señales recientes» y no «Inactivo» por una razón, y esa razón
              no puede vivir en dos lugares. */}
          {STATUS_LABEL[incident.status]}
        </span>
        {onOpenDetail && (
          <button
            type="button"
            onClick={() => onOpenDetail(incident.code)}
            className="ml-auto cursor-pointer rounded-control px-1.5 py-0.5 text-[11px] font-medium text-accent transition-colors hover:bg-accent-soft"
          >
            Ver ficha
          </button>
        )}
      </footer>
    </article>
  )
}
