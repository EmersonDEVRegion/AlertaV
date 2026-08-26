import { useState } from 'react'
import type { SeismicEvent } from '@/api/seismicTypes'
import type { Incident, OutageProvider } from '@/api/types'
import {
  AccordionRow,
  AccordionTrigger,
  Badge,
  Button,
  Checkbox,
  Sheet,
  Switch,
} from '@/components/ui/primitives'
import { LAYER_LABEL } from '@/domain/families'
import type { IncidentLayerKey } from '@/domain/families'
import { HAZARD_LEGEND, HAZARD_RAMP } from '@/domain/hazardSymbology'
import { OTHER_LEVEL } from '@/domain/otherSymbology'
import { PROVIDER, PROVIDER_ORDER, providerOf } from '@/domain/powerSymbology'
import { RAIN_LEGEND, RAIN_PALETTE } from '@/domain/rainSymbology'
import { SEISMIC_FILTER_OPTIONS, type SeismicFilterKey } from '@/domain/seismicFilter'
import { MAGNITUDE, bandOf } from '@/domain/seismicSymbology'
import { LEVEL } from '@/domain/symbology'
import { TRAFFIC_LEVEL } from '@/domain/trafficSymbology'
import type { HazardStatus } from '@/hooks/useSeismicHazard'
import type { RainStatus } from '@/hooks/useRainLayer'
import { cn } from '@/lib/cn'
import { formatRelative } from '@/lib/format'
import { IncidentListItem } from './IncidentListItem'

/**
 * Panel lateral del mapa.
 *
 * # Dos secciones que no se pueden mezclar
 *
 * Arriba, las **capas de emergencia**: casillas que filtran un conjunto ya
 * descargado. Abajo, las **capas de referencia**: interruptores que encienden
 * un modelo que ni siquiera se ha pedido todavía.
 *
 * La diferencia de forma —casilla contra interruptor— es deliberada. Un vecino
 * que ve la amenaza sísmica encendida junto a los incendios activos podría leer
 * el modelo probabilístico como un evento en curso, y eso es exactamente lo que
 * el proyecto entero trata de evitar.
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
  hazardEnabled: boolean
  hazardStatus: HazardStatus
  onHazardToggle: () => void
  onHazardRetry: () => void
  rainEnabled: boolean
  rainStatus: RainStatus
  rainCount: number
  rainRiskCount: number
  onRainToggle: () => void
  onRainRetry: () => void
  theme: 'light' | 'dark'
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

/**
 * Fila de una capa de referencia.
 *
 * El subtítulo carga con el estado —cargando, error, vacío— porque ahí se juega
 * la diferencia entre «no hay lluvia» y «no se pudo cargar». Son cosas
 * distintas y la interfaz no puede confundirlas.
 */
function ReferenceSwitch({
  label,
  description,
  checked,
  accentHex,
  onToggle,
  onRetry,
}: {
  label: string
  description: string
  checked: boolean
  /** Color del riel encendido. Valor, no clase: viene de la paleta de datos. */
  accentHex: string
  onToggle: () => void
  /** Sólo se dibuja si se pasa: es el estado de error. */
  onRetry?: () => void
}) {
  return (
    <div className="row-control">
      <Switch
        checked={checked}
        onCheckedChange={onToggle}
        label={label}
        accentColor={accentHex}
      />

      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium text-ink">{label}</span>
        <span className="block truncate text-[10px] text-ink-muted">{description}</span>
      </span>

      {onRetry && (
        <Button variant="subtle" size="sm" onClick={onRetry} className="shrink-0 text-[10px]">
          Reintentar
        </Button>
      )}
    </div>
  )
}

function rainDescription(status: RainStatus, count: number, riskCount: number): string {
  switch (status) {
    case 'loading':
      return 'Consultando el pronóstico…'
    case 'error':
      return 'No se pudo cargar'
    case 'empty':
      return RAIN_LEGEND.empty
    case 'ready':
      return riskCount > 0
        ? `${count} comuna${count === 1 ? '' : 's'} · ${riskCount} con riesgo`
        : `${count} comuna${count === 1 ? '' : 's'} · sin riesgo`
    default:
      return RAIN_LEGEND.subtitle
  }
}

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
  hazardEnabled,
  hazardStatus,
  onHazardToggle,
  onHazardRetry,
  rainEnabled,
  rainStatus,
  rainCount,
  rainRiskCount,
  onRainToggle,
  onRainRetry,
  theme,
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

      {/* --- Capas de referencia --------------------------------------------
          Separadas por una línea y un encabezado propio: no son emergencias ni
          se cuentan como tales. */}
      <div className="mt-2 border-t border-line pt-2">
        <p className="px-1.5 pb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">
          Capas de referencia
        </p>

        <ReferenceSwitch
          label="Amenaza sísmica"
          description={
            hazardStatus === 'loading'
              ? 'Descargando modelo…'
              : hazardStatus === 'error'
                ? 'No se pudo cargar'
                : HAZARD_LEGEND.subtitle
          }
          checked={hazardEnabled}
          accentHex={HAZARD_RAMP[theme].stops[2]![1]}
          onToggle={onHazardToggle}
          {...(hazardStatus === 'error' ? { onRetry: onHazardRetry } : {})}
        />

        {hazardEnabled && hazardStatus === 'ready' && (
          <div className="px-1.5 pb-1">
            <div
              aria-hidden
              className="h-1.5 w-full rounded-full"
              style={{
                background: `linear-gradient(to right, ${HAZARD_RAMP[theme].stops
                  .map(([, color]) => color)
                  .join(', ')})`,
              }}
            />
            <div className="mt-0.5 flex justify-between text-[9px] text-ink-faint">
              <span>{HAZARD_LEGEND.low}</span>
              <span>PGA</span>
              <span>{HAZARD_LEGEND.high}</span>
            </div>
          </div>
        )}

        <ReferenceSwitch
          label={RAIN_LEGEND.title}
          description={rainDescription(rainStatus, rainCount, rainRiskCount)}
          checked={rainEnabled}
          accentHex={RAIN_PALETTE[theme].risk}
          onToggle={onRainToggle}
          {...(rainStatus === 'error' ? { onRetry: onRainRetry } : {})}
        />

        {rainEnabled && (rainStatus === 'ready' || rainStatus === 'empty') && (
          <div className="px-1.5 pb-1">
            {rainStatus === 'ready' ? (
              <>
                <div className="flex items-center gap-3 text-[9px] text-ink-muted">
                  <span className="flex items-center gap-1">
                    <span
                      aria-hidden
                      className="size-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: RAIN_PALETTE[theme].rain, opacity: 0.7 }}
                    />
                    {RAIN_LEGEND.rain}
                  </span>
                  {rainRiskCount > 0 && (
                    <span className="flex items-center gap-1" title={RAIN_LEGEND.risk}>
                      <span
                        aria-hidden
                        className="size-2.5 shrink-0 rounded-full"
                        style={{
                          backgroundColor: RAIN_PALETTE[theme].risk,
                          boxShadow: `0 0 0 1.5px ${RAIN_PALETTE[theme].ring}`,
                        }}
                      />
                      Riesgo
                    </span>
                  )}
                </div>
                {/* No es negociable: la interfaz dice «riesgo pronosticado» y
                    nunca «inundación» a secas. */}
                <p className="mt-1 text-[9px] leading-tight text-ink-faint">
                  {RAIN_LEGEND.caveat}
                </p>
              </>
            ) : (
              <p className="text-[9px] leading-tight text-ink-faint">
                Ninguna comuna supera el umbral de emisión del pronóstico. La capa está
                encendida y al día.
              </p>
            )}
          </div>
        )}
      </div>
    </Sheet>
  )
}
