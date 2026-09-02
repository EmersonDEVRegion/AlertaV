import { useState } from 'react'
import type { ReactNode } from 'react'
import { LegendBody } from '@/components/map/MapLegend'
import { Panel } from '@/components/ui/primitives'
import { IncidentFilters } from './SidePanel'
import type { SidePanelProps } from './SidePanel'
import { ReferenceLayers } from './ReferenceDock'
import type { ReferenceDockProps } from './ReferenceDock'
import { cn } from '@/lib/cn'

/**
 * Controles del mapa en pantalla estrecha.
 *
 * ===========================================================================
 * EL PROBLEMA
 * ===========================================================================
 *
 * En escritorio hay dos superficies flotantes ancladas a bordes opuestos: el
 * riel izquierdo (capas de referencia + leyenda) y la hoja derecha
 * (emergencias). Miden 15,5 rem y 15 rem, o sea **488 px de cromo más márgenes**
 * sobre un mapa que en un iPhone 15 Pro Max tiene 430 px de ancho. No se
 * solapaban por un error de posicionamiento: no cabían.
 *
 * ---------------------------------------------------------------------------
 * POR QUÉ NO SE ARREGLÓ MOVIÉNDOLOS
 * ---------------------------------------------------------------------------
 *
 * Las tres salidas evidentes fallan por el mismo motivo — el ancho no da:
 *
 *   - **Apilarlos en vertical.** El de arriba come el alto que el de abajo
 *     necesita para su lista, y la hoja derecha ya tiene `max-h` calculado
 *     contra la ventana. Se convierte en dos columnas de scroll compitiendo.
 *   - **Encogerlos.** A 40 % del ancho, la fila de una capa deja de tener sitio
 *     para casilla, muestra de color, nombre y contador — que es exactamente la
 *     anatomía que `SidePanel` documenta como no negociable.
 *   - **Esconder uno.** Deja el teléfono sin la mitad de los controles.
 *
 * ---------------------------------------------------------------------------
 * LA DECISIÓN: una barra de fichas, un panel a la vez
 * ---------------------------------------------------------------------------
 *
 * En vez de repartir el ancho, se reparte el TIEMPO: una barra compacta con
 * tres fichas —Emergencias, Referencia, Leyenda— y **como máximo un panel
 * abierto**, a todo el ancho disponible. Abrir una cierra la otra, así que la
 * colisión deja de ser posible por construcción y no por aritmética de
 * márgenes.
 *
 * Tres decisiones de posición que no son arbitrarias:
 *
 *   1. **Anclado arriba y no abajo.** El borde inferior ya está ocupado por el
 *      botón de reporte, la ficha del incidente seleccionado y la atribución del
 *      mapa. Una hoja inferior —el patrón habitual— tendría que negociar con
 *      los tres.
 *   2. **Ningún panel abierto al arrancar.** El mapa es el contenido; en un
 *      teléfono, un panel abierto de entrada tapa medio territorio. Los
 *      contadores de cada ficha dan el resumen sin ocupar nada.
 *   3. **Techo de 55 dvh.** Deja siempre visible el tercio inferior del mapa,
 *      que es donde el pulgar arrastra. `dvh` y no `vh`: la barra del navegador
 *      móvil se retrae al desplazarse y `vh` conserva el valor expandido.
 *
 * `pointer-events` viaja hijo a hijo: el contenedor los deja pasar para que el
 * hueco entre la barra y el panel no sea una franja muerta sobre el mapa.
 */

export interface MobileMapControlsProps {
  incidents: SidePanelProps
  reference: ReferenceDockProps
  /** Total de incidentes visibles. Va en la ficha, sin abrir nada. */
  incidentCount: number
}

type PanelKey = 'incidents' | 'reference' | 'legend'

interface Tab {
  key: PanelKey
  label: string
  /**
   * Etiqueta larga para el lector de pantalla. **Empieza por la palabra visible
   * de la ficha, y eso no es estilo: es WCAG 2.5.3 (*Label in Name*).** Quien
   * navega por voz dice «pulsa Leyenda», y el reconocedor compara contra el
   * nombre accesible, no contra el texto pintado. Un `aria-label` que describa
   * mejor pero no contenga la etiqueta visible deja el control inalcanzable
   * para ese usuario.
   */
  description: string
}

const TABS: readonly Tab[] = [
  { key: 'incidents', label: 'Emergencias', description: 'emergencias: capas y lista' },
  {
    key: 'reference',
    label: 'Referencia',
    description: 'referencia: amenaza sísmica y cortes de ruta',
  },
  { key: 'legend', label: 'Leyenda', description: 'leyenda: qué significan los colores' },
]

const PANEL_ID = 'mobile-map-panel'

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={cn('size-3 transition-transform duration-300', open && 'rotate-180')}
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  )
}

export function MobileMapControls({
  incidents,
  reference,
  incidentCount,
}: MobileMapControlsProps) {
  const [open, setOpen] = useState<PanelKey | null>(null)

  // Volver a tocar la ficha abierta cierra: es el gesto que devuelve el mapa
  // completo sin buscar una «×» en una esquina.
  const toggle = (key: PanelKey) => setOpen((current) => (current === key ? null : key))

  // La lluvia ya no se cuenta acá: su interruptor se mudó al widget
  // meteorológico de la barra superior, que está siempre visible y dice su
  // propio estado. Sumarla a este contador anunciaría, desde una ficha cerrada,
  // una capa que no se puede encender desde dentro de esa ficha.
  const activeReference =
    Number(reference.hazardEnabled) + Number(reference.closureEnabled)

  /** Contador de la ficha. `null` cuando no hay nada que contar. */
  const badgeFor = (key: PanelKey): number | null => {
    if (key === 'incidents') return incidentCount
    if (key === 'reference') return activeReference > 0 ? activeReference : null
    return null
  }

  const body: Record<PanelKey, ReactNode> = {
    incidents: <IncidentFilters {...incidents} />,
    reference: <ReferenceLayers {...reference} />,
    legend: <LegendBody />,
  }

  const openTab = TABS.find((tab) => tab.key === open) ?? null

  return (
    <div className="pointer-events-none absolute inset-x-3 top-3 z-10 flex flex-col gap-2">
      <Panel className="animate-slide-in pointer-events-auto p-1">
        <div
          role="group"
          aria-label="Controles del mapa"
          className="flex items-center gap-1"
        >
          {TABS.map((tab) => {
            const isOpen = open === tab.key
            const badge = badgeFor(tab.key)

            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => toggle(tab.key)}
                aria-expanded={isOpen}
                aria-controls={PANEL_ID}
                aria-label={`${isOpen ? 'Ocultar' : 'Mostrar'} ${tab.description}`}
                className={cn(
                  // `flex-1` y no un ancho por contenido: tres objetivos del
                  // mismo tamaño son más fáciles de acertar con el pulgar que
                  // tres de anchos distintos, y el reparto no cambia al variar
                  // un contador.
                  'flex min-w-0 flex-1 items-center justify-center gap-1.5',
                  'h-9 rounded-control px-2 text-[11px] font-medium',
                  'transition-[background-color,color] duration-150',
                  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent',
                  isOpen ? 'bg-accent-soft text-accent' : 'text-ink-muted hover:bg-hover',
                )}
              >
                <span className="truncate">{tab.label}</span>
                {badge !== null && (
                  <span
                    aria-hidden
                    className={cn(
                      'shrink-0 rounded-full px-1.5 text-[10px] font-semibold',
                      isOpen ? 'bg-accent-soft text-accent' : 'bg-sunken text-ink-muted',
                    )}
                  >
                    {badge}
                  </span>
                )}
                <Chevron open={isOpen} />
              </button>
            )
          })}
        </div>
      </Panel>

      {/*
        El panel se monta y desmonta en vez de animar su altura. Es la excepción
        deliberada a la regla de `grid-template-rows` que usa el resto del cromo:
        acá dentro va la lista completa de incidentes, y mantener tres cuerpos en
        el DOM para poder interpolarlos costaría más que la transición que se
        gana. La entrada sigue siendo `opacity` + `translate`, que es lo que el
        compositor resuelve sin tocar layout.
      */}
      {openTab && (
        <Panel
          id={PANEL_ID}
          role="region"
          aria-label={openTab.description}
          className={cn(
            'animate-rise pointer-events-auto p-2.5',
            'max-h-[55dvh] overflow-y-auto overscroll-contain',
          )}
        >
          {body[openTab.key]}
        </Panel>
      )}
    </div>
  )
}
