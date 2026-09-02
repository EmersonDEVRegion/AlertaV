import { useState } from 'react'
import type { ReactNode } from 'react'
import { Button, Panel, Switch } from '@/components/ui/primitives'
import {
  HAZARD_LEGEND,
  HAZARD_RAMP,
  HAZARD_RETICULE,
} from '@/domain/hazardSymbology'
// Sólo la zona de relevo, para `REFERENCE_SWAP_ZOOMS`: la capa de lluvia sigue
// existiendo en el mapa aunque su tarjeta se haya ido al widget, y el test de
// coherencia entre estilo e interfaz sigue leyendo ese número desde acá.
import { RAIN_SWAP } from '@/domain/rainSymbology'
import {
  ROAD_CLOSURE_LEGEND,
  ROAD_CLOSURE_LEGEND_TEXT,
  ROAD_CLOSURE_PALETTE,
} from '@/domain/roadClosureSymbology'
import type { HazardStatus } from '@/hooks/useSeismicHazard'
import type { RoadClosureStatus } from '@/hooks/useRoadClosures'
import type { Theme } from '@/hooks/useTheme'
import { cn } from '@/lib/cn'

/**
 * Controlador de capas de referencia.
 *
 * # Por qué salió del panel derecho
 *
 * Vivía al final del `SidePanel`, bajo una línea divisoria, después de cinco
 * filas de emergencias y de sus sublistas desplegables. Esa posición decía algo
 * que no es cierto: que estas capas son un apéndice de las otras.
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
  // Sin nada de lluvia: ese control se fue al widget de la barra superior. Ver
  // la nota dentro de `ReferenceLayers`.
  closureEnabled: boolean
  closureStatus: RoadClosureStatus
  closureCount: number
  closureCutCount: number
  onClosureToggle: () => void
  onClosureRetry: () => void
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

function RoadIcon() {
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
      {/* Una calzada en perspectiva con la línea central discontinua: el
          símbolo universal de «vía», y la discontinuidad ya insinúa la
          interrupción sin necesidad de un aspa. */}
      <path d="M4 21 7 3" />
      <path d="M20 21 17 3" />
      <path d="M12 5v2" />
      <path d="M12 11v2" />
      <path d="M12 17v2" />
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
          Es lo que hace que las tarjetas se distingan de un vistazo sin repetir
          el nombre: violeta es amenaza sísmica, ámbar son cortes de ruta, y el
          mismo color es el que aparece en el mapa.
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

/**
 * Subtítulo de la tarjeta de cortes.
 *
 * El caso `ready` antepone los cortes EFECTIVOS al total, y ese orden es la
 * decisión de la función: de todo lo que esta capa dibuja, lo único accionable
 * es cuántas rutas no se pueden pasar. Un «14 vigentes» a secas mezcla una
 * repavimentación programada con un puente caído.
 */
function closureDescription(
  status: RoadClosureStatus,
  count: number,
  cutCount: number,
): string {
  switch (status) {
    case 'loading':
      return 'Consultando Vialidad y el MTT…'
    case 'error':
      return 'No se pudo cargar'
    case 'empty':
      return ROAD_CLOSURE_LEGEND_TEXT.empty
    case 'ready':
      return cutCount > 0
        ? `${cutCount} ruta${cutCount === 1 ? '' : 's'} cortada${cutCount === 1 ? '' : 's'} · ${count} vigente${count === 1 ? '' : 's'}`
        : `${count} vigente${count === 1 ? '' : 's'} · ninguna cortada`
    default:
      return ROAD_CLOSURE_LEGEND_TEXT.subtitle
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
  closureEnabled,
  closureStatus,
  closureCount,
  closureCutCount,
  onClosureToggle,
  onClosureRetry,
  theme,
}: ReferenceDockProps) {
  const hazardAccent = HAZARD_RAMP[theme].stops[2]![1]
  /*
   * El acento de la tarjeta es el ÁMBAR, no el rojo del extremo de la rampa.
   *
   * En las otras dos tarjetas el acento es el color más alarmante de su paleta
   * porque ése es el estado que la capa existe para mostrar. Acá no: el estado
   * normal de esta capa son faenas y desvíos, y teñir el azulejo de rojo diría
   * «hay rutas cortadas» incluso cuando no hay ninguna. El rojo aparece en la
   * leyenda y en el mapa, que es donde significa algo.
   */
  const closureAccent = ROAD_CLOSURE_PALETTE[theme].low

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

      {/*
        --- La tarjeta de lluvia ya NO vive acá -------------------------

        Se fusionó en el widget meteorológico de la barra superior
        (`components/ui/WeatherWidget.tsx`), y conviene dejar escrito el porqué
        para que nadie la reponga por simetría.

        Tenía título, subtítulo de estado, muestra de color, nota de escala y
        advertencia legal para decir lo que el widget dice en 180 px — y encima
        lo decía sólo cuando alguien la encendía. Una capa apagada por defecto
        no puede ser el sitio donde se anuncia un temporal: en teléfono había
        que abrir la ficha «Referencia» para enterarse de que llovía.

        Al widget se fue todo: el estado, la explicación y el interruptor de la
        capa del mapa, que ahora está dentro de su detalle desplegable. La capa
        de MapLibre no cambió ni una línea; lo que cambió es quién la enciende.

        Las otras dos tarjetas se quedan porque no son lo mismo: la amenaza
        sísmica describe el terreno —no tiene estado que vigilar— y los cortes
        de ruta son un listado que se consulta, no una condición ambiental que
        se mira de reojo.
      */}

      {/* --- Cortes de ruta ------------------------------------------- */}
      <LayerCard
        label={ROAD_CLOSURE_LEGEND_TEXT.title}
        description={closureDescription(closureStatus, closureCount, closureCutCount)}
        icon={<RoadIcon />}
        checked={closureEnabled}
        accentHex={closureAccent}
        failed={closureStatus === 'error'}
        busy={closureStatus === 'loading'}
        onToggle={onClosureToggle}
        {...(closureStatus === 'error' ? { onRetry: onClosureRetry } : {})}
      >
        <div className="px-0.5 pb-0.5 pt-2">
          {closureStatus === 'empty' ? (
            <p className="text-[9.5px] leading-tight text-ink-faint">
              Ni Vialidad ni el MTT informan intervenciones vigentes en la
              región. La capa está encendida y al día.
            </p>
          ) : (
            <>
              {/*
                Las cuatro filas en columna y no en línea como las de lluvia:
                acá son cuatro y no dos, y sobre todo cada una necesita su
                explicación. «Transitable» y «Tránsito restringido» son
                indistinguibles sin la frase que las acompaña, y esa frase es
                justo lo que el usuario necesita para decidir si sale.

                El color lo pide cada fila a la MISMA paleta que pinta el mapa
                (ver `ROAD_CLOSURE_LEGEND`): un hex escrito acá se
                desincronizaría del estilo el día que alguien ajuste la rampa.
              */}
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
              {/* No es negociable: un corte de ruta no es un siniestro, y una
                  emergencia del MOP arrastra semanas de vigencia. */}
              <p className="mt-1.5 text-[9.5px] leading-tight text-ink-faint">
                {ROAD_CLOSURE_LEGEND_TEXT.caveat}
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
  // Dos y no tres: la lluvia se cuenta sola en el widget de la barra superior.
  const activeCount = Number(props.hazardEnabled) + Number(props.closureEnabled)

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
