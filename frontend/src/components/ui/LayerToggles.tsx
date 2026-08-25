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
import { HAZARD_LEGEND, HAZARD_RAMP } from '@/domain/hazardSymbology'
import { RAIN_LEGEND, RAIN_PALETTE } from '@/domain/rainSymbology'
import type { HazardStatus } from '@/hooks/useSeismicHazard'
import type { RainStatus } from '@/hooks/useRainLayer'
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
  /** Capa de referencia de amenaza sísmica. */
  hazardEnabled: boolean
  hazardStatus: HazardStatus
  onHazardToggle: () => void
  onHazardRetry: () => void
  /** Capa de lluvia pronosticada. Diferida: el primer encendido dispara la llamada. */
  rainEnabled: boolean
  rainStatus: RainStatus
  /** Comunas con lluvia pronosticada, y cuántas de ellas con riesgo. */
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
  hollow?: boolean
}

const ROWS: readonly Row[] = [
  { key: 'fire', label: LAYER_LABEL.fire, swatch: LEVEL.confirmed.color },
  { key: 'traffic', label: LAYER_LABEL.traffic, swatch: TRAFFIC_LEVEL.confirmed.color },
  { key: 'power', label: LAYER_LABEL.power, swatch: PROVIDER.chilquinta.color },
  { key: 'otros', label: LAYER_LABEL.otros, swatch: OTHER_LEVEL.confirmed.color },
  { key: 'seismic', label: 'Sismos', swatch: '#f97316', hollow: true },
]

/** Ancho del panel. Se declara una vez porque lo usan el contenedor y el
 *  desplazamiento de cierre, y si se separaran el panel quedaría asomando. */
const PANEL_WIDTH = 'w-60'

/** Enlaza la pestaña con el panel para `aria-controls`. */
const PANEL_ID = 'map-layer-panel'

/**
 * Chevron. Apunta a la derecha por defecto —el gesto de empujar el panel para
 * cerrarlo— y se gira 180° cuando está cerrado, para invitar a traerlo de vuelta.
 */
function Chevron({ collapsed }: { collapsed: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={
        'size-4 transition-transform duration-300 ' + (collapsed ? 'rotate-180' : '')
      }
    >
      <path d="m9 18 6-6-6-6" />
    </svg>
  )
}

/**
 * Fila de una capa de referencia.
 *
 * Interruptor y no casilla, y la diferencia de forma es deliberada: las casillas
 * de arriba FILTRAN un conjunto que ya está descargado, mientras que esto
 * enciende una capa que ni siquiera se ha pedido todavía. Mezclar las dos formas
 * invitaría a leer una referencia como un evento en curso.
 *
 * El subtítulo carga con el estado —cargando, error, vacío— porque es donde se
 * juega la diferencia entre "no hay lluvia" y "no se pudo cargar". Son cosas
 * distintas y la UI no puede confundirlas.
 */
function ReferenceSwitch({
  label,
  description,
  checked,
  accent,
  onToggle,
  onRetry,
}: {
  label: string
  description: string
  checked: boolean
  /** Clases del interruptor encendido. Un color por capa. */
  accent: string
  onToggle: () => void
  /** Sólo se dibuja si se pasa: es el estado de error. */
  onRetry?: () => void
}) {
  return (
    <div className="flex items-center gap-2 rounded-lg px-1.5 py-1">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={onToggle}
        className={
          'relative h-4 w-7 shrink-0 rounded-full transition-colors ' +
          (checked ? accent : 'bg-slate-300 dark:bg-slate-600')
        }
      >
        <span
          aria-hidden
          className={
            'absolute top-0.5 size-3 rounded-full bg-white transition-transform ' +
            (checked ? 'translate-x-3.5' : 'translate-x-0.5')
          }
        />
      </button>

      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium text-slate-800 dark:text-slate-100">
          {label}
        </span>
        <span className="block truncate text-[10px] text-slate-500 dark:text-slate-400">
          {description}
        </span>
      </span>

      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium text-slate-600 ring-1 ring-slate-300 hover:bg-slate-100 dark:text-slate-300 dark:ring-slate-600 dark:hover:bg-slate-800"
        >
          Reintentar
        </button>
      )}
    </div>
  )
}

/**
 * Subtítulo del interruptor de lluvia.
 *
 * La regla del hand-off: **una comuna ausente es una comuna seca**, así que una
 * colección vacía es una respuesta correcta y frecuente —en verano lo es durante
 * semanas—. Tiene que decir "sin lluvia pronosticada" y NUNCA "sin datos", que
 * es lo que diría si se tratara como un error.
 */
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
}: LayerTogglesProps) {
  const [expanded, setExpanded] = useState<keyof LayerVisibility | null>(null)
  // Abierto por defecto: el panel es el índice del mapa, y esconderlo de
  // entrada dejaría la pantalla sin pistas de qué se está viendo.
  const [collapsed, setCollapsed] = useState(false)

  const toggleExpanded = (key: keyof LayerVisibility) =>
    setExpanded((current) => (current === key ? null : key))

  return (
    /*
     * Tres capas, y cada una hace una sola cosa:
     *
     *   1. contenedor  — ancla la posición. NO se mueve.
     *   2. deslizador  — el único que se transforma. Lleva dentro la pestaña.
     *   3. panel       — el recuadro con los filtros.
     *
     * La pestaña va DENTRO del deslizador, anclada a su borde izquierdo con
     * `right-full`. Así, al desplazar el deslizador por (ancho + inset), el
     * panel sale completo de la pantalla y la pestaña queda justo en el borde,
     * que es lo único que debe seguir asomando.
     *
     * `pointer-events-none` en el contenedor y `auto` en el deslizador: el mapa
     * sigue recibiendo el arrastre en el hueco que deja el panel al cerrarse.
     */
    <div className="pointer-events-none absolute right-3 top-[8.5rem] z-10 md:top-[9.5rem]">
      <div
        className={
          /*
           * Sólo `transform` cambia. Ni `width` ni `display`: ambos disparan
           * layout en cada frame y el navegador no puede componer la animación
           * en el hilo del compositor. Con `translateX` la transición se
           * resuelve en GPU y se mantiene fluida aunque el mapa esté
           * repintando teselas debajo.
           */
          'pointer-events-auto relative transition-transform duration-300 ease-out will-change-transform ' +
          (collapsed ? 'translate-x-[calc(100%+0.75rem)]' : 'translate-x-0')
        }
      >
        {/* --- Pestaña --------------------------------------------------- */}
        <button
          type="button"
          onClick={() => setCollapsed((value) => !value)}
          aria-expanded={!collapsed}
          aria-controls={PANEL_ID}
          aria-label={collapsed ? 'Mostrar filtros del mapa' : 'Ocultar filtros del mapa'}
          title={collapsed ? 'Mostrar filtros' : 'Ocultar filtros'}
          className="
            absolute right-full top-3 grid h-12 w-7 place-items-center
            rounded-l-lg rounded-r-none bg-white/95 text-slate-500 shadow-lg
            ring-1 ring-slate-900/10 backdrop-blur transition-colors
            hover:text-slate-900
            dark:bg-slate-900/95 dark:text-slate-400 dark:ring-white/10
            dark:hover:text-slate-100
          "
          style={{
            // El anillo del panel dibujaría una línea entre ambos; recortarla
            // en el costado que se toca es lo que los hace ver como una pieza.
            clipPath: 'inset(-8px 0 -8px -8px)',
          }}
        >
          <Chevron collapsed={collapsed} />
        </button>

        {/* --- Panel ------------------------------------------------------ */}
        <div
          id={PANEL_ID}
          /*
           * `inert` cuando está cerrado. El panel sigue en el DOM —es lo que
           * permite animarlo— pero fuera de la pantalla: sin esto, el tabulador
           * seguiría entrando en casillas invisibles y un lector de pantalla
           * las anunciaría.
           */
          inert={collapsed}
          aria-hidden={collapsed}
          className={`${PANEL_WIDTH} rounded-2xl bg-white/95 p-2.5 shadow-lg ring-1 ring-slate-900/10 backdrop-blur dark:bg-slate-900/95 dark:ring-white/10`}
        >
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

        {/* --- Capas de referencia ------------------------------------------
            Separadas por una línea y un encabezado propio: no son emergencias
            ni se cuentan como tales. Mezclarlas con las casillas de arriba
            invitaría a leer la amenaza como un evento en curso. */}
        <div className="mt-2 border-t border-slate-200 pt-2 dark:border-slate-700">
          <p className="px-1.5 pb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
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
            accent="bg-violet-600 dark:bg-violet-500"
            onToggle={onHazardToggle}
            {...(hazardStatus === 'error' ? { onRetry: onHazardRetry } : {})}
          />

          {/* Leyenda: sólo cuando la capa está encendida y cargada. */}
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
              <div className="mt-0.5 flex justify-between text-[9px] text-slate-400 dark:text-slate-500">
                <span>{HAZARD_LEGEND.low}</span>
                <span>PGA</span>
                <span>{HAZARD_LEGEND.high}</span>
              </div>
            </div>
          )}

          {/* ---- Lluvia pronosticada ----------------------------------------
              Va junto a la amenaza sísmica y no entre las casillas de arriba:
              tampoco es una emergencia. Pero se diferencia de ella en algo que
              el usuario tiene que poder notar — la amenaza es un modelo
              estático del terreno y esto es un pronóstico que cambia cada
              media hora. De ahí que el subtítulo lleve la cuenta de comunas y
              el pie recuerde que no es una alerta declarada. */}
          <ReferenceSwitch
            label={RAIN_LEGEND.title}
            description={rainDescription(rainStatus, rainCount, rainRiskCount)}
            checked={rainEnabled}
            accent="bg-blue-600 dark:bg-blue-500"
            onToggle={onRainToggle}
            {...(rainStatus === 'error' ? { onRetry: onRainRetry } : {})}
          />

          {rainEnabled && (rainStatus === 'ready' || rainStatus === 'empty') && (
            <div className="px-1.5 pb-1">
              {rainStatus === 'ready' ? (
                <>
                  <div className="flex items-center gap-3 text-[9px] text-slate-500 dark:text-slate-400">
                    <span className="flex items-center gap-1">
                      <span
                        aria-hidden
                        className="size-2.5 shrink-0 rounded-full"
                        style={{ backgroundColor: RAIN_PALETTE[theme].rain, opacity: 0.7 }}
                      />
                      {RAIN_LEGEND.rain}
                    </span>
                    {/* La etiqueta corta cabe en el panel; el texto completo
                        —"riesgo PRONOSTICADO", nunca "inundación" a secas—
                        viaja en el `title`. */}
                    {rainRiskCount > 0 && (
                      <span className="flex items-center gap-1" title={RAIN_LEGEND.risk}>
                        <span
                          aria-hidden
                          className="size-2.5 shrink-0 rounded-full"
                          style={{
                            backgroundColor: RAIN_PALETTE[theme].risk,
                            // El anillo del mapa, replicado: es la marca que
                            // distingue el riesgo, y la leyenda tiene que
                            // enseñar la misma forma que se ve en pantalla.
                            boxShadow: `0 0 0 1.5px ${RAIN_PALETTE[theme].ring}`,
                          }}
                        />
                        Riesgo
                      </span>
                    )}
                  </div>
                  {/* No es negociable: el hand-off pide que la UI diga "riesgo
                      pronosticado" y nunca "inundación". */}
                  <p className="mt-1 text-[9px] leading-tight text-slate-400 dark:text-slate-500">
                    {RAIN_LEGEND.caveat}
                  </p>
                </>
              ) : (
                <p className="text-[9px] leading-tight text-slate-400 dark:text-slate-500">
                  Ninguna comuna supera el umbral de emisión del pronóstico. La capa
                  está encendida y al día.
                </p>
              )}
            </div>
          )}
        </div>
        </div>
      </div>
    </div>
  )
}
