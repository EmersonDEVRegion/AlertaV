import { useState } from 'react'
import type { SeismicEvent } from '@/api/seismicTypes'
import type { Incident, OutageProvider } from '@/api/types'
import {
  AccordionRow,
  AccordionTrigger,
  Badge,
  Checkbox,
  Sheet,
} from '@/components/ui/primitives'
import { LAYER_LABEL } from '@/domain/families'
import type { IncidentLayerKey } from '@/domain/families'
import { OTHER_LEVEL } from '@/domain/otherSymbology'
import { PROVIDER, PROVIDER_ORDER, providerOf } from '@/domain/powerSymbology'
import { SEISMIC_FILTER_OPTIONS, type SeismicFilterKey } from '@/domain/seismicFilter'
import { MAGNITUDE, bandOf } from '@/domain/seismicSymbology'
import { LEVEL } from '@/domain/symbology'
import { TRAFFIC_LEVEL } from '@/domain/trafficSymbology'
import { cn } from '@/lib/cn'
import { formatRelative } from '@/lib/format'
import { IncidentListItem } from './IncidentListItem'

/**
 * Panel lateral del mapa: **sólo capas de emergencia**.
 *
 * # Qué se fue de acá
 *
 * Las capas de referencia —amenaza sísmica y lluvia— vivían al final de este
 * panel, tras una línea divisoria. Ahora tienen el suyo, al otro lado de la
 * pantalla: `components/ui/ReferenceDock.tsx`, donde está explicado el porqué.
 *
 * Lo que queda es homogéneo, y eso es la mitad del beneficio: **todas las filas
 * de este panel hacen lo mismo** —filtrar un conjunto ya descargado— así que
 * todas pueden tener la misma forma. Antes convivían casillas e interruptores
 * en la misma lista y la diferencia de forma tenía que cargar sola con una
 * distinción conceptual que el espacio no ayudaba a hacer.
 *
 * La separación física también resuelve el riesgo que motivó aquella
 * distinción: un vecino que veía la amenaza sísmica encendida junto a los
 * incendios activos podía leer el modelo probabilístico como un evento en
 * curso. Ahora ni siquiera comparten superficie.
 *
 * # La anatomía de una fila
 *
 *     [casilla] [muestra] [etiqueta ................] [contador]
 *
 * El `flex-1` vive en el bloque de la etiqueta, no en la fila: es lo que absorbe
 * el sobrante y mantiene el contador pegado al borde derecho aunque los nombres
 * midan distinto. `min-w-0` sobre ese mismo bloque habilita el `truncate`, que
 * sin él no recorta nada porque el ancho mínimo de un ítem flex es su contenido.
 */

export interface LayerVisibility {
  fire: boolean
  traffic: boolean
  power: boolean
  otros: boolean
  seismic: boolean
}

export const DEFAULT_LAYER_VISIBILITY: LayerVisibility = {
  fire: true,
  traffic: true,
  power: true,
  otros: true,
  seismic: true,
}

/**
 * Subfiltro de la categoría de cortes.
 *
 * Vive aparte de `LayerVisibility` porque no es una capa: es un filtro DENTRO
 * de una capa. Mezclarlos haría que apagar Chilquinta y CGE por separado
 * significara lo mismo que apagar la categoría entera, y son dos gestos con
 * intenciones distintas.
 */
export type ProviderVisibility = Record<OutageProvider, boolean>

export const DEFAULT_PROVIDER_VISIBILITY: ProviderVisibility = {
  chilquinta: true,
  cge: true,
}

export interface SidePanelProps {
  visibility: LayerVisibility
  onChange: (next: LayerVisibility) => void
  counts: Record<keyof LayerVisibility, number>
  /** Incidentes visibles, agrupados por capa, para la lista desplegable. */
  incidentsByLayer: Record<IncidentLayerKey, Incident[]>
  seismicEvents: readonly SeismicEvent[]
  selectedCode: string | null
  selectedUsgsId: string | null
  onFocusIncident: (incident: Incident) => void
  onFocusSeismic: (event: SeismicEvent) => void
  seismicFilter: SeismicFilterKey
  onSeismicFilterChange: (filter: SeismicFilterKey) => void
  providers: ProviderVisibility
  onProvidersChange: (next: ProviderVisibility) => void
}

interface Row {
  key: keyof LayerVisibility
  label: string
  swatch: string
  /** Los sismos se dibujan como círculo hueco; la muestra lo refleja. */
  hollow?: boolean
  /** Un enjambre sísmico pasa de 99: ese contador necesita tres cifras. */
  wide?: boolean
}

const ROWS: readonly Row[] = [
  { key: 'fire', label: LAYER_LABEL.fire, swatch: LEVEL.confirmed.color },
  { key: 'traffic', label: LAYER_LABEL.traffic, swatch: TRAFFIC_LEVEL.confirmed.color },
  { key: 'power', label: LAYER_LABEL.power, swatch: PROVIDER.chilquinta.color },
  { key: 'otros', label: LAYER_LABEL.otros, swatch: OTHER_LEVEL.confirmed.color },
  { key: 'seismic', label: 'Sismos', swatch: '#f97316', hollow: true, wide: true },
]

export function SidePanel({
  visibility,
  onChange,
  counts,
  incidentsByLayer,
  seismicEvents,
  selectedCode,
  selectedUsgsId,
  onFocusIncident,
  onFocusSeismic,
  seismicFilter,
  onSeismicFilterChange,
  providers,
  onProvidersChange,
}: SidePanelProps) {
  const [expanded, setExpanded] = useState<keyof LayerVisibility | null>(null)
  const toggleExpanded = (key: keyof LayerVisibility) =>
    setExpanded((current) => (current === key ? null : key))

  return (
    <Sheet>
      <fieldset>
        <legend className="sr-only">Capas del mapa</legend>

        <ul className="space-y-0.5">
          {ROWS.map((row) => {
            const isOpen = expanded === row.key
            const count = counts[row.key]

            return (
              <AccordionRow
                key={row.key}
                open={isOpen}
                header={
                  <div className="row-control text-xs text-ink">
                    <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
                      <Checkbox
                        checked={visibility[row.key]}
                        onCheckedChange={(next) =>
                          onChange({ ...visibility, [row.key]: next })
                        }
                        accentColor={row.swatch}
                      />
                      <span
                        aria-hidden
                        className="size-3 shrink-0 rounded-full"
                        style={
                          row.hollow
                            ? { border: `2px solid ${row.swatch}` }
                            : { backgroundColor: row.swatch }
                        }
                      />
                      <span className="truncate font-medium">{row.label}</span>
                    </label>

                    {/* Hermano de la casilla, no dentro de su <label>: un
                        <label> reenvía el clic a su input, así que abrir la
                        lista apagaría la capa. */}
                    <AccordionTrigger
                      open={isOpen}
                      disabled={count === 0}
                      label={`${isOpen ? 'Ocultar' : 'Ver'} los ${count} de ${row.label}`}
                      onToggle={() => toggleExpanded(row.key)}
                    >
                      <Badge variant="count" width={row.wide ? 'three' : 'two'}>
                        {count}
                      </Badge>
                    </AccordionTrigger>
                  </div>
                }
                aside={
                  <>
                    {/* ---- Sub-opciones por distribuidora ---- */}
                    {row.key === 'power' && visibility.power && (
                      <ul
                        className="mb-1 ml-6 space-y-0.5 border-l border-line pl-2"
                        aria-label="Empresas distribuidoras"
                      >
                        {PROVIDER_ORDER.map((provider) => {
                          const style = PROVIDER[provider]
                          const providerCount = incidentsByLayer.power.filter(
                            (incident) => providerOf(incident) === provider,
                          ).length

                          return (
                            <li key={provider}>
                              <label className="flex cursor-pointer items-center gap-2 rounded-control px-1.5 py-1 text-[11px] text-ink-muted hover:bg-hover">
                                <Checkbox
                                  checked={providers[provider]}
                                  onCheckedChange={(next) =>
                                    onProvidersChange({ ...providers, [provider]: next })
                                  }
                                  accentColor={style.color}
                                  className="size-3"
                                />
                                <span
                                  aria-hidden
                                  className="size-2.5 shrink-0 rounded-control"
                                  style={{ backgroundColor: style.color }}
                                />
                                <span className="min-w-0 flex-1 truncate font-medium">
                                  {style.label}
                                </span>
                                <Badge variant="count">{providerCount}</Badge>
                              </label>
                            </li>
                          )
                        })}
                      </ul>
                    )}

                    {/* ---- Filtro de relevancia sísmica ---- */}
                    {row.key === 'seismic' && visibility.seismic && (
                      <div
                        role="radiogroup"
                        aria-label="Relevancia de los sismos"
                        className="mb-1 ml-6 mt-0.5 flex rounded-control bg-sunken p-0.5"
                      >
                        {SEISMIC_FILTER_OPTIONS.map((option) => (
                          <button
                            key={option.key}
                            type="button"
                            role="radio"
                            aria-checked={seismicFilter === option.key}
                            title={option.hint}
                            onClick={() => onSeismicFilterChange(option.key)}
                            className={cn(
                              'flex-1 rounded-control px-1.5 py-1 text-[10px] font-medium transition',
                              seismicFilter === option.key
                                ? 'bg-raised text-ink shadow-sm'
                                : 'text-ink-muted hover:text-ink',
                            )}
                          >
                            {option.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                }
              >
                {row.key !== 'seismic' &&
                  incidentsByLayer[row.key as IncidentLayerKey].map((incident) => (
                    <IncidentListItem
                      key={incident.code}
                      incident={incident}
                      selected={incident.code === selectedCode}
                      onSelect={onFocusIncident}
                    />
                  ))}

                {row.key === 'seismic' &&
                  seismicEvents.map((event) => {
                    const style = MAGNITUDE[bandOf(event)]
                    return (
                      <li key={event.usgs_id}>
                        <button
                          type="button"
                          onClick={() => onFocusSeismic(event)}
                          aria-current={event.usgs_id === selectedUsgsId ? 'true' : undefined}
                          className={cn(
                            'flex w-full items-center gap-2 rounded-control px-1.5 py-1.5 text-left transition',
                            event.usgs_id === selectedUsgsId
                              ? 'bg-accent-soft'
                              : 'hover:bg-hover',
                          )}
                        >
                          <span
                            aria-hidden
                            className="size-2.5 shrink-0 rounded-full"
                            style={{ border: `2px solid ${style.color}` }}
                          />
                          <span className="min-w-0 flex-1">
                            <span className="block text-[11px] font-semibold text-ink">
                              {event.magnitude !== null
                                ? `M ${event.magnitude.toFixed(1)}`
                                : 'Sin magnitud'}
                              {event.depth_km !== null &&
                                ` · ${Math.round(event.depth_km)} km`}
                            </span>
                            <span className="block truncate text-[10px] text-ink-muted">
                              {event.commune ?? event.place ?? 'sin referencia'} ·{' '}
                              {formatRelative(event.timestamp)}
                            </span>
                          </span>
                        </button>
                      </li>
                    )
                  })}
              </AccordionRow>
            )
          })}
        </ul>
      </fieldset>

    </Sheet>
  )
}
