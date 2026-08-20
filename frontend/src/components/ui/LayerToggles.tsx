import { useState } from 'react'
import type { SeismicEvent } from '@/api/seismicTypes'
import type { Incident } from '@/api/types'
import { LAYER_LABEL } from '@/domain/families'
import type { IncidentLayerKey } from '@/domain/families'
import { OTHER_LEVEL } from '@/domain/otherSymbology'
import {
  PROVIDER,
  PROVIDER_ORDER,
  providerOf,
} from '@/domain/powerSymbology'
import type { OutageProvider } from '@/api/types'
import {
  SEISMIC_FILTER_OPTIONS,
  type SeismicFilterKey,
} from '@/domain/seismicFilter'
import { MAGNITUDE, bandOf } from '@/domain/seismicSymbology'
import { LEVEL } from '@/domain/symbology'
import { TRAFFIC_LEVEL } from '@/domain/trafficSymbology'
import { formatRelative } from '@/lib/format'
import { IncidentListItem } from './IncidentListItem'

/**
 * Control de capas, ahora también índice navegable.
 *
 * Cada fila hace dos cosas distintas y las separa en dos controles:
 *
 *   - la **casilla** enciende o apaga la capa;
 *   - el **contador** despliega la lista de lo que hay en ella.
 *
 * Están separados a propósito. Si el `<label>` de la casilla envolviera también
 * el botón del acordeón, hacer clic para ver la lista apagaría la capa: un
 * `<label>` reenvía el clic a su input. Son dos elementos hermanos dentro de la
 * fila, no anidados.
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
 * de una capa. Mezclarlos obligaría a que apagar Chilquinta y CGE por separado
 * significara lo mismo que apagar la categoría entera, y son dos gestos con
 * intenciones distintas.
 */
export type ProviderVisibility = Record<OutageProvider, boolean>

export const DEFAULT_PROVIDER_VISIBILITY: ProviderVisibility = {
  chilquinta: true,
  cge: true,
}

interface LayerTogglesProps {
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
  hollow?: boolean
}

const ROWS: readonly Row[] = [
  { key: 'fire', label: LAYER_LABEL.fire, swatch: LEVEL.confirmed.color },
  { key: 'traffic', label: LAYER_LABEL.traffic, swatch: TRAFFIC_LEVEL.confirmed.color },
  { key: 'power', label: LAYER_LABEL.power, swatch: PROVIDER.chilquinta.color },
  { key: 'otros', label: LAYER_LABEL.otros, swatch: OTHER_LEVEL.confirmed.color },
  { key: 'seismic', label: 'Sismos', swatch: '#f97316', hollow: true },
]

export function LayerToggles({
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
}: LayerTogglesProps) {
  const [expanded, setExpanded] = useState<keyof LayerVisibility | null>(null)

  const toggleExpanded = (key: keyof LayerVisibility) =>
    setExpanded((current) => (current === key ? null : key))

  return (
    <div className="pointer-events-auto absolute right-3 top-[8.5rem] z-10 w-60 rounded-2xl bg-white/95 p-2.5 shadow-lg ring-1 ring-slate-900/10 backdrop-blur md:top-[9.5rem] dark:bg-slate-900/95 dark:ring-white/10">
      <fieldset>
        <legend className="sr-only">Capas del mapa</legend>

        <ul className="space-y-0.5">
          {ROWS.map((row) => {
            const isOpen = expanded === row.key
            const count = counts[row.key]

            return (
              <li key={row.key}>
                <div className="flex items-center gap-2 rounded-lg px-1.5 py-1 text-xs text-slate-800 dark:text-slate-200">
                  {/* Casilla: enciende y apaga la capa. */}
                  <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
                    <input
                      type="checkbox"
                      checked={visibility[row.key]}
                      onChange={(event) =>
                        onChange({ ...visibility, [row.key]: event.target.checked })
                      }
                      className="size-3.5 shrink-0 accent-orange-500"
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

                  {/* Contador: despliega la lista. Hermano de la casilla, no
                      dentro de su <label>, o el clic la apagaría. */}
                  <button
                    type="button"
                    onClick={() => toggleExpanded(row.key)}
                    disabled={count === 0}
                    aria-expanded={isOpen}
                    aria-label={`${isOpen ? 'Ocultar' : 'Ver'} los ${count} de ${row.label}`}
                    className="flex shrink-0 items-center gap-1 rounded px-1 py-0.5 text-[11px] tabular-nums text-slate-500 transition hover:bg-slate-100 disabled:pointer-events-none disabled:opacity-40 dark:text-slate-400 dark:hover:bg-slate-800"
                  >
                    {count}
                    <span
                      aria-hidden
                      className={'transition-transform ' + (isOpen ? 'rotate-90' : '')}
                    >
                      ›
                    </span>
                  </button>
                </div>

                {/* ---- Lista desplegable ---- */}
                {isOpen && row.key !== 'seismic' && (
                  <ul className="mb-1 ml-1 max-h-56 space-y-0.5 overflow-y-auto border-l border-slate-200 pl-1.5 dark:border-slate-700">
                    {incidentsByLayer[row.key as IncidentLayerKey].map((incident) => (
                      <IncidentListItem
                        key={incident.code}
                        incident={incident}
                        selected={incident.code === selectedCode}
                        onSelect={onFocusIncident}
                      />
                    ))}
                  </ul>
                )}

                {isOpen && row.key === 'seismic' && (
                  <ul className="mb-1 ml-1 max-h-56 space-y-0.5 overflow-y-auto border-l border-slate-200 pl-1.5 dark:border-slate-700">
                    {seismicEvents.map((event) => {
                      const style = MAGNITUDE[bandOf(event)]
                      return (
                        <li key={event.usgs_id}>
                          <button
                            type="button"
                            onClick={() => onFocusSeismic(event)}
                            aria-current={
                              event.usgs_id === selectedUsgsId ? 'true' : undefined
                            }
                            className={
                              'flex w-full items-center gap-2 rounded-lg px-1.5 py-1.5 text-left transition ' +
                              (event.usgs_id === selectedUsgsId
                                ? 'bg-slate-200 dark:bg-slate-700'
                                : 'hover:bg-slate-100 dark:hover:bg-slate-800')
                            }
                          >
                            <span
                              aria-hidden
                              className="size-2.5 shrink-0 rounded-full"
                              style={{ border: `2px solid ${style.color}` }}
                            />
                            <span className="min-w-0 flex-1">
                              <span className="block text-[11px] font-semibold text-slate-800 dark:text-slate-200">
                                {event.magnitude !== null
                                  ? `M ${event.magnitude.toFixed(1)}`
                                  : 'Sin magnitud'}
                                {event.depth_km !== null &&
                                  ` · ${Math.round(event.depth_km)} km`}
                              </span>
                              <span className="block truncate text-[10px] text-slate-500 dark:text-slate-400">
                                {event.commune ?? event.place ?? 'sin referencia'} ·{' '}
                                {formatRelative(event.timestamp)}
                              </span>
                            </span>
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                )}

                {/* ---- Sub-opciones por distribuidora ---- */}
                {row.key === 'power' && visibility.power && (
                  <ul
                    className="mb-1 ml-6 space-y-0.5 border-l border-slate-200 pl-2 dark:border-slate-700"
                    aria-label="Empresas distribuidoras"
                  >
                    {PROVIDER_ORDER.map((provider) => {
                      const style = PROVIDER[provider]
                      const count = incidentsByLayer.power.filter(
                        (incident) => providerOf(incident) === provider,
                      ).length

                      return (
                        <li key={provider}>
                          <label className="flex cursor-pointer items-center gap-2 rounded-lg px-1.5 py-1 text-[11px] text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800">
                            <input
                              type="checkbox"
                              checked={providers[provider]}
                              onChange={(event) =>
                                onProvidersChange({
                                  ...providers,
                                  [provider]: event.target.checked,
                                })
                              }
                              className="size-3 shrink-0"
                              style={{ accentColor: style.color }}
                            />
                            <span
                              aria-hidden
                              className="size-2.5 shrink-0 rounded-sm"
                              style={{ backgroundColor: style.color }}
                            />
                            <span className="flex-1 truncate font-medium">
                              {style.label}
                            </span>
                            <span className="shrink-0 tabular-nums text-slate-400 dark:text-slate-500">
                              {count}
                            </span>
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
                    className="mb-1 ml-6 mt-0.5 flex rounded-lg bg-slate-100 p-0.5 dark:bg-slate-800"
                  >
                    {SEISMIC_FILTER_OPTIONS.map((option) => (
                      <button
                        key={option.key}
                        type="button"
                        role="radio"
                        aria-checked={seismicFilter === option.key}
                        title={option.hint}
                        onClick={() => onSeismicFilterChange(option.key)}
                        className={
                          'flex-1 rounded-md px-1.5 py-1 text-[10px] font-medium transition ' +
                          (seismicFilter === option.key
                            ? 'bg-white text-slate-900 shadow-sm dark:bg-slate-600 dark:text-white'
                            : 'text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200')
                        }
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      </fieldset>
    </div>
  )
}
