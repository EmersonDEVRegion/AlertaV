import type { ReactNode } from 'react'
import { Button } from '@/components/ui/primitives'
import { CUARTEL_PALETTE } from '@/domain/cuarteles'
import { HAZARD_LEGEND, HAZARD_RAMP } from '@/domain/hazardSymbology'
import {
  ROAD_CLOSURE_LEGEND,
  ROAD_CLOSURE_LEGEND_TEXT,
  ROAD_CLOSURE_PALETTE,
} from '@/domain/roadClosureSymbology'
import type { CuartelesStatus } from '@/hooks/useCuarteles'
import type { RoadClosureStatus } from '@/hooks/useRoadClosures'
import type { HazardStatus } from '@/hooks/useSeismicHazard'
import type { Theme } from '@/hooks/useTheme'
import { cn } from '@/lib/cn'
import { LayerGroupHeading, LayerTile } from './LayerTile'

/**
 * Las capas de referencia: amenaza sísmica, cortes de ruta y cuarteles.
 *
 * # Por qué van en su propio grupo
 *
 * Las de emergencia **filtran** algo ya descargado: encenderlas es instantáneo.
 * Éstas **cargan** un modelo o un listado que nadie pidió todavía, con su estado
 * (cargando, error, vacío) y una leyenda propia. Arrancan apagadas: nada se
 * descarga sin un gesto del usuario. Y una amenaza sísmica al lado de los
 * incendios se podía leer como un evento en curso; en otro grupo, bajo
 * «Referencia», no.
 *
 * El azulejo lleva el estado corto; con la capa encendida, su ficha aparece
 * bajo el mosaico con la leyenda, la explicación y, si falló, el reintento. El
 * subtítulo de la ficha distingue «no hay» de «no se pudo cargar»: un invierno
 * sin cortes de ruta no es un servidor caído.
 */
export interface ReferenceLayersProps {
  hazardEnabled: boolean
  hazardStatus: HazardStatus
  hazardError: string | null
  onHazardToggle: () => void
  onHazardRetry: () => void
  closureEnabled: boolean
  closureStatus: RoadClosureStatus
  closureCount: number
  closureCutCount: number
  onClosureToggle: () => void
  onClosureRetry: () => void
  cuartelesEnabled: boolean
  cuartelesStatus: CuartelesStatus
  cuartelesCount: number
  cuartelesError: string | null
  onCuartelesToggle: () => void
  onCuartelesRetry: () => void
  theme: Theme
}

/* ------------------------------------------------------------------------- */
/* Iconografía: trazo y no emoji, hereda `currentColor`                       */
/* ------------------------------------------------------------------------- */

function StrokeIcon({ children }: { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.25}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className="size-3.5"
    >
      {children}
    </svg>
  )
}

/** Un registro sismográfico: reposo, sacudida, reposo. */
const WaveIcon = () => (
  <StrokeIcon>
    <path d="M2 12h3.5l2-6 3.5 12 3-9 2 3H22" />
  </StrokeIcon>
)

/** Calzada en perspectiva con la línea central discontinua. */
const RoadIcon = () => (
  <StrokeIcon>
    <path d="M4 21 7 3" />
    <path d="M20 21 17 3" />
    <path d="M12 5v2" />
    <path d="M12 11v2" />
    <path d="M12 17v2" />
  </StrokeIcon>
)

/** Un cuartel: techo a dos aguas y el portón del carro. */
const StationIcon = () => (
  <StrokeIcon>
    <path d="M3 10.5 12 4l9 6.5" />
    <path d="M5 9.5V20h14V9.5" />
    <path d="M9 20v-6h6v6" />
  </StrokeIcon>
)

/* ------------------------------------------------------------------------- */
/* Textos de estado                                                           */
/* ------------------------------------------------------------------------- */

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/**
 * El caso `ready` antepone los cortes EFECTIVOS al total: de todo lo que la
 * capa dibuja, lo accionable es cuántas rutas no se pueden pasar. Un «14
 * vigentes» a secas mezcla una repavimentación con un puente caído.
 */
function closureDescription(status: RoadClosureStatus, count: number, cutCount: number): string {
  switch (status) {
    case 'loading':
      return 'Consultando Vialidad y el MTT…'
    case 'error':
      return 'No se pudo cargar'
    case 'empty':
      return ROAD_CLOSURE_LEGEND_TEXT.empty
    case 'ready':
      return cutCount > 0
        ? `${plural(cutCount, 'ruta cortada', 'rutas cortadas')} · ${plural(count, 'vigente', 'vigentes')}`
        : `${plural(count, 'vigente', 'vigentes')} · ninguna cortada`
    default:
      return ROAD_CLOSURE_LEGEND_TEXT.subtitle
  }
}

function closureShort(status: RoadClosureStatus, count: number, cutCount: number): string | null {
  switch (status) {
    case 'loading':
      return 'Cargando…'
    case 'error':
      return 'Sin conexión'
    case 'empty':
      return 'Ninguno'
    case 'ready':
      return cutCount > 0 ? plural(cutCount, 'cortada', 'cortadas') : plural(count, 'vigente', 'vigentes')
    default:
      return null
  }
}

function cuartelesDescription(status: CuartelesStatus, count: number, error: string | null): string {
  switch (status) {
    case 'loading':
      return 'Cargando cuarteles…'
    case 'error':
      return error ?? 'No se pudo cargar'
    case 'ready':
      return `${count} cuarteles en la región`
    default:
      return 'Cuerpos y compañías de la región'
  }
}

function hazardDescription(status: HazardStatus, error: string | null): string {
  switch (status) {
    case 'loading':
      return 'Descargando modelo del CSN…'
    case 'error':
      return error ?? 'No se pudo cargar'
    default:
      return HAZARD_LEGEND.subtitle
  }
}

/** Lo que dice el azulejo: corto, y sólo si hay algo que decir. */
function shortStatus(status: string): string | null {
  if (status === 'loading') return 'Cargando…'
  if (status === 'error') return 'Sin conexión'
  return null
}

/* ------------------------------------------------------------------------- */
/* Ficha de una capa encendida                                                */
/* ------------------------------------------------------------------------- */

function LayerDetail({
  label,
  description,
  failed,
  onRetry,
  children,
}: {
  label: string
  description: string
  failed: boolean
  onRetry?: () => void
  children?: ReactNode
}) {
  return (
    <section aria-label={label} className="rounded-control bg-sunken px-2.5 py-2">
      <p className="text-[11px] font-semibold text-ink">{label}</p>
      <p className={cn('text-[10.5px] leading-tight', failed ? 'text-danger-ink' : 'text-ink-muted')}>
        {description}
      </p>
      {onRetry && (
        <Button variant="subtle" size="sm" onClick={onRetry} className="mt-1.5 w-full text-[10.5px]">
          Reintentar descarga
        </Button>
      )}
      {!failed && children && <div className="pt-2">{children}</div>}
    </section>
  )
}

const NOTE = 'mt-1.5 text-[9.5px] leading-tight text-ink-faint'

/* ------------------------------------------------------------------------- */

export function ReferenceLayers({
  hazardEnabled,
  hazardStatus,
  hazardError,
  onHazardToggle,
  onHazardRetry,
  closureEnabled,
  closureStatus,
  closureCount,
  closureCutCount,
  onClosureToggle,
  onClosureRetry,
  cuartelesEnabled,
  cuartelesStatus,
  cuartelesCount,
  cuartelesError,
  onCuartelesToggle,
  onCuartelesRetry,
  theme,
}: ReferenceLayersProps) {
  const hazardAccent = HAZARD_RAMP[theme].stops[2]![1]
  /*
   * Ámbar y no el rojo del extremo de la rampa: el estado normal de esta capa
   * son faenas y desvíos, y teñir el azulejo de rojo diría «hay rutas
   * cortadas» aunque no haya ninguna. El rojo aparece en la leyenda y el mapa.
   */
  const closureAccent = ROAD_CLOSURE_PALETTE[theme].low
  // Pizarra: un cuartel es referencia, no una emergencia (ver `CUARTEL_PALETTE`).
  const cuartelAccent = CUARTEL_PALETTE[theme].stroke

  const anyOn = hazardEnabled || closureEnabled || cuartelesEnabled

  return (
    <section aria-labelledby="capas-referencia">
      <LayerGroupHeading id="capas-referencia">Referencia</LayerGroupHeading>

      <div className="grid grid-cols-3 gap-1.5">
        <LayerTile
          label="Amenaza sísmica"
          icon={<WaveIcon />}
          color={hazardAccent}
          on={hazardEnabled}
          onToggle={onHazardToggle}
          busy={hazardStatus === 'loading'}
          failed={hazardStatus === 'error'}
          detail={hazardEnabled ? shortStatus(hazardStatus) : null}
        />
        <LayerTile
          label={ROAD_CLOSURE_LEGEND_TEXT.title}
          icon={<RoadIcon />}
          color={closureAccent}
          on={closureEnabled}
          onToggle={onClosureToggle}
          busy={closureStatus === 'loading'}
          failed={closureStatus === 'error'}
          detail={closureEnabled ? closureShort(closureStatus, closureCount, closureCutCount) : null}
        />
        <LayerTile
          label="Cuarteles de Bomberos"
          icon={<StationIcon />}
          color={cuartelAccent}
          on={cuartelesEnabled}
          onToggle={onCuartelesToggle}
          busy={cuartelesStatus === 'loading'}
          failed={cuartelesStatus === 'error'}
          detail={
            cuartelesEnabled
              ? (shortStatus(cuartelesStatus) ??
                (cuartelesStatus === 'ready' ? `${cuartelesCount} cuarteles` : null))
              : null
          }
        />
      </div>

      {anyOn && (
        <div className="mt-1.5 space-y-1.5">
          {hazardEnabled && (
            <LayerDetail
              label={HAZARD_LEGEND.title}
              description={hazardDescription(hazardStatus, hazardError)}
              failed={hazardStatus === 'error'}
              {...(hazardStatus === 'error' ? { onRetry: onHazardRetry } : {})}
            >
              <div
                aria-hidden
                className="h-1.5 w-full rounded-full"
                style={{
                  background: `linear-gradient(to right, ${HAZARD_RAMP[theme].stops
                    .map(([, color]) => color)
                    .join(', ')})`,
                }}
              />
              <div className="mt-1 flex justify-between text-[9.5px] text-ink-faint">
                <span>{HAZARD_LEGEND.low}</span>
                <span className="font-medium text-ink-muted">PGA</span>
                <span>{HAZARD_LEGEND.high}</span>
              </div>
              {/* La resolución evita leer el degradado como una medición
                  continua del terreno: es una grilla de 5 km interpolada. */}
              <p className={NOTE}>{HAZARD_LEGEND.scale}</p>
              {/* No es negociable: describe el terreno, no un evento en curso. */}
              <p className={NOTE}>{HAZARD_LEGEND.caveat}</p>
            </LayerDetail>
          )}

          {closureEnabled && (
            <LayerDetail
              label={ROAD_CLOSURE_LEGEND_TEXT.title}
              description={closureDescription(closureStatus, closureCount, closureCutCount)}
              failed={closureStatus === 'error'}
              {...(closureStatus === 'error' ? { onRetry: onClosureRetry } : {})}
            >
              {closureStatus === 'empty' ? (
                <p className="text-[9.5px] leading-tight text-ink-faint">
                  Ni Vialidad ni el MTT informan intervenciones vigentes en la región. La capa
                  está encendida y al día.
                </p>
              ) : (
                <>
                  {/* Cada estado con su frase: «Transitable» y «Tránsito
                      restringido» no se distinguen sin ella. El color sale de
                      la misma paleta que pinta el mapa. */}
                  <ul className="space-y-1">
                    {ROAD_CLOSURE_LEGEND.map((row) => (
                      <li key={row.label} className="flex items-start gap-1.5">
                        <span
                          aria-hidden
                          className="mt-[3px] size-2.5 shrink-0 rotate-45 rounded-[2px]"
                          style={{ backgroundColor: row.color(theme) }}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block text-[9.5px] font-medium leading-tight text-ink-muted">
                            {row.label}
                          </span>
                          <span className="block text-[9px] leading-tight text-ink-faint">
                            {row.meaning}
                          </span>
                        </span>
                      </li>
                    ))}
                  </ul>
                  {/* Un corte de ruta no es un siniestro. */}
                  <p className={NOTE}>{ROAD_CLOSURE_LEGEND_TEXT.caveat}</p>
                </>
              )}
            </LayerDetail>
          )}

          {cuartelesEnabled && (
            <LayerDetail
              label="Cuarteles de Bomberos"
              description={cuartelesDescription(cuartelesStatus, cuartelesCount, cuartelesError)}
              failed={cuartelesStatus === 'error'}
              {...(cuartelesStatus === 'error' ? { onRetry: onCuartelesRetry } : {})}
            >
              <ul className="space-y-1">
                <li className="flex items-center gap-1.5">
                  <span
                    aria-hidden
                    className="size-3 shrink-0 rounded-full border-[2.5px]"
                    style={{
                      borderColor: CUARTEL_PALETTE[theme].stroke,
                      backgroundColor: CUARTEL_PALETTE[theme].fill,
                    }}
                  />
                  <span className="text-[9.5px] leading-tight text-ink-muted">
                    Cuartel del Cuerpo (comandancia)
                  </span>
                </li>
                <li className="flex items-center gap-1.5">
                  <span
                    aria-hidden
                    className="size-2.5 shrink-0 rounded-full border-2"
                    style={{
                      borderColor: CUARTEL_PALETTE[theme].stroke,
                      backgroundColor: CUARTEL_PALETTE[theme].fill,
                    }}
                  />
                  <span className="text-[9.5px] leading-tight text-ink-muted">Compañía</span>
                </li>
              </ul>
              <p className={NOTE}>
                Se ven al acercar el mapa. Fuente: SIG Bomberos de Chile. Puede faltar alguna
                compañía.
              </p>
              {/* El cuartel más cercano no es necesariamente el que responde. */}
              <p className={NOTE}>
                En una emergencia llama al 132: la central despacha la compañía que corresponde.
              </p>
            </LayerDetail>
          )}
        </div>
      )}
    </section>
  )
}
