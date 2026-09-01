import { useState } from 'react'
import type { ReactNode } from 'react'
import { Button, Panel, Switch } from '@/components/ui/primitives'
import {
  HAZARD_LEGEND,
  HAZARD_RAMP,
  HAZARD_RETICULE,
} from '@/domain/hazardSymbology'
import { RAIN_LEGEND, RAIN_PALETTE, RAIN_SWAP } from '@/domain/rainSymbology'
import type { HazardStatus } from '@/hooks/useSeismicHazard'
import type { RainStatus } from '@/hooks/useRainLayer'
import type { Theme } from '@/hooks/useTheme'
import { cn } from '@/lib/cn'

/**
 * Controlador de capas de referencia.
 *
 * # Por qué salió del panel derecho
 *
 * Vivía al final del `SidePanel`, bajo una línea divisoria, después de cinco
 * filas de emergencias y de sus sublistas desplegables. Esa posición decía algo
 * que no es cierto: que estas dos capas son un apéndice de las otras.
 *
 * No lo son. Son de otra naturaleza:
 *
 *   - Las de emergencia **filtran** un conjunto ya descargado. Encenderlas y
 *     apagarlas es instantáneo y no tiene estado propio.
 *   - Éstas **cargan un modelo** que ni siquiera se ha pedido. Tienen carga
 *     diferida, estado de error, reintento y una leyenda propia.
 *
 * Mezclarlas en un mismo panel obligaba a las segundas a comportarse como las
 * primeras: una fila estrecha, sin sitio para explicar qué se está encendiendo
 * ni qué significa el resultado. Acá tienen su propia superficie, al otro lado
 * de la pantalla, y el gesto de encenderlas es un gesto distinto también en el
 * espacio.
 *
 * # La regla que ordena la tarjeta
 *
 * El subtítulo carga con el estado —cargando, error, vacío, listo— porque ahí
 * se juega la diferencia entre «no hay lluvia» y «no se pudo cargar». Son cosas
 * distintas y la interfaz no puede confundirlas: un invierno seco no es un
 * fallo del servidor.
 */

export interface ReferenceDockProps {
  hazardEnabled: boolean
  hazardStatus: HazardStatus
  hazardError: string | null
  onHazardToggle: () => void
  onHazardRetry: () => void
  rainEnabled: boolean
  rainStatus: RainStatus
  rainCount: number
  rainRiskCount: number
  onRainToggle: () => void
  onRainRetry: () => void
  theme: Theme
}

/* ------------------------------------------------------------------------- */
/* Iconografía                                                                */
/* ------------------------------------------------------------------------- */

/**
 * Trazo vectorial y no emoji.
 *
 * Un emoji lo dibuja la fuente del sistema: distinto en Android, en iOS y en
 * Windows, con su propio color fijo que no responde al tema y sin alineación
 * fiable con el texto. Estos heredan `currentColor` y miden siempre lo mismo.
 */
function WaveIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className="size-4"
    >
      {/* Un registro sismográfico: reposo, sacudida, reposo. */}
      <path d="M2 12h3.5l2-6 3.5 12 3-9 2 3H22" />
    </svg>
  )
}

function RainIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className="size-4"
    >
      <path d="M17.5 17a4.5 4.5 0 0 0 .5-8.97A6 6 0 0 0 6.3 8.5 3.75 3.75 0 0 0 6.5 16" />
      <path d="M9 18.5 8 21" />
      <path d="M13 18.5 12 21" />
      <path d="M17 18.5 16 21" />
    </svg>
  )
}

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
      className={cn('size-3.5 transition-transform duration-300', !open && '-rotate-90')}
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  )
}

/* ------------------------------------------------------------------------- */
/* Tarjeta de una capa                                                        */
/* ------------------------------------------------------------------------- */

interface LayerCardProps {
  label: string
  description: string
  icon: ReactNode
  checked: boolean
  /** Color de la capa. Valor y no clase: viene de la paleta de datos. */
  accentHex: string
  /** ¿El subtítulo describe un fallo? Cambia el tono, no sólo el texto. */
  failed?: boolean
  busy?: boolean
  onToggle: () => void
  onRetry?: () => void
  /** Leyenda. Sólo se revela con la capa encendida y cargada. */
  children?: ReactNode
}

function LayerCard({
  label,
  description,
  icon,
  checked,
  accentHex,
  failed = false,
  busy = false,
  onToggle,
  onRetry,
  children,
}: LayerCardProps) {
  return (
    <div
      className={cn(
        'rounded-control p-2 transition-colors duration-200',
        checked ? 'bg-sunken' : 'hover:bg-hover',
      )}
    >
      <div className="flex items-center gap-2.5">
        {/*
          El azulejo del ícono se tiñe con el color de la capa a baja opacidad.
          Es lo que hace que las dos tarjetas se distingan de un vistazo sin
          repetir el nombre: violeta es amenaza, cian es lluvia, y el mismo
          color es el que aparece en el mapa.
        */}
        <span
          aria-hidden
          className={cn(
            'relative grid size-8 shrink-0 place-items-center rounded-control transition-all duration-300',
            !checked && 'text-ink-faint',
          )}
          style={
            checked
              ? { backgroundColor: `${accentHex}22`, color: accentHex }
              : { backgroundColor: 'var(--surface-sunken)' }
          }
        >
          {icon}
          {/* Pulso mientras carga. Va sobre el azulejo y no sobre el
              interruptor: el interruptor tiene que verse firme —es la
              intención del usuario— y el que espera es el dato. */}
          {busy && (
            <span
              className="absolute size-8 animate-ping rounded-control opacity-40"
              style={{ backgroundColor: accentHex }}
            />
          )}
        </span>

        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-semibold text-ink">{label}</span>
          <span
            className={cn(
              'block truncate text-[10.5px] leading-tight',
              failed ? 'text-danger-ink' : 'text-ink-muted',
            )}
          >
            {description}
          </span>
        </span>

        <Switch
          checked={checked}
          onCheckedChange={onToggle}
          label={label}
          accentColor={accentHex}
        />
      </div>

      {onRetry && (
        <Button
          variant="subtle"
          size="sm"
          onClick={onRetry}
          className="mt-1.5 w-full text-[10.5px]"
        >
          Reintentar descarga
        </Button>
      )}

      {/*
        Revelado de la leyenda.

        `grid-template-rows: 0fr → 1fr` en vez de `max-height`. La receta del
        `max-height` obliga a inventar un techo: si se queda corto recorta el
        contenido, y si se pasa —lo habitual— la transición gasta la mayor parte
        de su duración animando espacio vacío y el resultado se ve arrancado.
        Con `fr` el navegador interpola hasta la altura REAL del contenido.
      */}
      <div
        className={cn(
          'grid transition-[grid-template-rows,opacity] duration-300 ease-out',
          checked && children ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
        )}
      >
        <div className="overflow-hidden">{children}</div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* Textos de estado                                                           */
/* ------------------------------------------------------------------------- */

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

function hazardDescription(status: HazardStatus, error: string | null): string {
  switch (status) {
    case 'loading':
      return 'Descargando modelo del CSN…'
    case 'error':
      return error ?? 'No se pudo cargar'
    case 'ready':
      return HAZARD_LEGEND.subtitle
    default:
      return HAZARD_LEGEND.subtitle
  }
}

/** Marca de escala: qué representación manda al zoom actual. */
function ScaleNote({ regional, local }: { regional: string; local: string }) {
  return (
    <p className="mt-1.5 flex items-center gap-1.5 text-[9.5px] text-ink-faint">
      <span>{regional}</span>
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
        className="size-2.5 shrink-0"
      >
        <path d="M5 12h14" />
        <path d="m13 6 6 6-6 6" />
      </svg>
      <span>{local}</span>
    </p>
  )
}

/* ------------------------------------------------------------------------- */

/**
 * Las dos tarjetas, sin el contenedor plegable.
 *
 * Existe separado porque en teléfono este contenido no vive en el riel
 * izquierdo —dos superficies flotantes de 15 rem no caben a 430 px— sino dentro
 * de la barra de fichas de `MobileMapControls`, donde la ficha ya hace de
 * cabecera y un segundo plegado sería un clic de más para llegar a lo mismo.
 * Ver la nota de `hooks/useMediaQuery.ts`.
 */
export function ReferenceLayers({
  hazardEnabled,
  hazardStatus,
  hazardError,
  onHazardToggle,
  onHazardRetry,
  rainEnabled,
  rainStatus,
  rainCount,
  rainRiskCount,
  onRainToggle,
  onRainRetry,
  theme,
}: ReferenceDockProps) {
  const hazardAccent = HAZARD_RAMP[theme].stops[2]![1]
  const rainAccent = RAIN_PALETTE[theme].risk

  return (
    <div className="space-y-0.5">
      {/* --- Amenaza sísmica ------------------------------------------ */}
      <LayerCard
        label="Amenaza sísmica"
        description={hazardDescription(hazardStatus, hazardError)}
        icon={<WaveIcon />}
        checked={hazardEnabled}
        accentHex={hazardAccent}
        failed={hazardStatus === 'error'}
        busy={hazardStatus === 'loading'}
        onToggle={onHazardToggle}
        {...(hazardStatus === 'error' ? { onRetry: onHazardRetry } : {})}
      >
        <div className="px-0.5 pb-0.5 pt-2">
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
          {/* Ya no hay relevo de representación que anunciar —la capa es
              una sola superficie en todo el rango de zoom—, así que en
              lugar de una flecha «de esto a esto» se declara la
              RESOLUCIÓN. Es el dato que evita el malentendido que queda:
              que el degradado suave sea una medición continua del terreno
              y no la interpolación de una grilla de 5 km. */}
          <p className="mt-1.5 text-[9.5px] leading-tight text-ink-faint">
            {HAZARD_LEGEND.scale} · {HAZARD_LEGEND.reticule}
          </p>
          {/* No es negociable: esta capa describe el terreno, no un
              evento en curso. */}
          <p className="mt-1.5 text-[9.5px] leading-tight text-ink-faint">
            {HAZARD_LEGEND.caveat}
          </p>
        </div>
      </LayerCard>

      {/* --- Lluvia pronosticada -------------------------------------- */}
      <LayerCard
        label={RAIN_LEGEND.title}
        description={rainDescription(rainStatus, rainCount, rainRiskCount)}
        icon={<RainIcon />}
        checked={rainEnabled}
        accentHex={rainAccent}
        failed={rainStatus === 'error'}
        busy={rainStatus === 'loading'}
        onToggle={onRainToggle}
        {...(rainStatus === 'error' ? { onRetry: onRainRetry } : {})}
      >
        <div className="px-0.5 pb-0.5 pt-2">
          {rainStatus === 'empty' ? (
            <p className="text-[9.5px] leading-tight text-ink-faint">
              Ninguna comuna supera el umbral de emisión del pronóstico. La capa
              está encendida y al día.
            </p>
          ) : (
            <>
              <div className="flex items-center gap-3 text-[9.5px] text-ink-muted">
                <span className="flex items-center gap-1">
                  <span
                    aria-hidden
                    className="size-2.5 shrink-0 rounded-full"
                    style={{
                      backgroundColor: RAIN_PALETTE[theme].rain,
                      opacity: 0.7,
                    }}
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
              <ScaleNote regional={RAIN_LEGEND.regional} local={RAIN_LEGEND.local} />
              {/* No es negociable: la interfaz dice «riesgo pronosticado»
                  y nunca «inundación» a secas. */}
              <p className="mt-1.5 text-[9.5px] leading-tight text-ink-faint">
                {RAIN_LEGEND.caveat}
              </p>
            </>
          )}
        </div>
      </LayerCard>
    </div>
  )
}

/**
 * El riel izquierdo de escritorio: las mismas tarjetas, dentro de una superficie
 * flotante plegable.
 */
export function ReferenceDock(props: ReferenceDockProps) {
  const [open, setOpen] = useState(true)
  const activeCount = Number(props.hazardEnabled) + Number(props.rainEnabled)

  return (
    <Panel className="pointer-events-auto w-full overflow-hidden p-1.5">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls="reference-dock-body"
        className={cn(
          'flex w-full items-center gap-2 rounded-control px-1.5 py-1 text-left transition-colors',
          'hover:bg-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent',
        )}
      >
        <span className="text-ink-faint">
          <Chevron open={open} />
        </span>
        <span className="min-w-0 flex-1 truncate text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
          Capas de referencia
        </span>
        {/*
          Contador de encendidas. Sólo aparece con el dock cerrado: abierto, los
          interruptores ya lo dicen, y un número repitiendo lo que se ve al lado
          es ruido.
        */}
        {!open && activeCount > 0 && (
          <span
            aria-hidden
            className="rounded-full bg-accent-soft px-1.5 text-[10px] font-semibold text-accent"
          >
            {activeCount}
          </span>
        )}
      </button>

      <div
        id="reference-dock-body"
        className={cn(
          'grid transition-[grid-template-rows] duration-300 ease-out',
          open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]',
        )}
      >
        <div className="overflow-hidden">
          <div className="mt-0.5">
            <ReferenceLayers {...props} />
          </div>
        </div>
      </div>
    </Panel>
  )
}

/**
 * Zonas de relevo, expuestas para los tests de coherencia con el mapa.
 *
 * En la lluvia sigue siendo un relevo de representación. En la amenaza ya no lo
 * es —hay una sola superficie— y el número que queda es la ventana en la que
 * aparece la retícula de celda, que es lo único que cambia con el zoom.
 */
export const REFERENCE_SWAP_ZOOMS = {
  hazard: HAZARD_RETICULE,
  rain: RAIN_SWAP,
} as const
