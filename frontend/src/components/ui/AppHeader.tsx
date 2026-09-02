import type { ReactNode } from 'react'
import { LEVEL, LEVEL_ORDER } from '@/domain/symbology'
import { WeatherWidget } from '@/components/ui/WeatherWidget'
import { cn } from '@/lib/cn'

/**
 * Barra superior.
 *
 * # La decisión de composición: una barra, tres zonas
 *
 * La versión anterior apilaba el título y el desglose de confianza en una
 * columna a la izquierda, y empujaba el filtro y el tema a la derecha. Con
 * cinco números en la segunda línea, el bloque izquierdo crecía hasta ocupar
 * media barra y la jerarquía se perdía: el título —lo único fijo— quedaba
 * compitiendo por atención con cifras que cambian solas cada minuto.
 *
 * Ahora son tres zonas con roles distintos:
 *
 *   [marca] │ [telemetría, centrada y en cápsulas] │ [controles]
 *
 * La marca no cambia nunca, así que se queda quieta a la izquierda y se
 * comprime a lo mínimo. La telemetría vive en cápsulas separadas —cada tramo
 * de confianza en la suya, con su punto de color— porque son cinco datos
 * independientes y no una frase; leer «12 activos · 3 · 5 · 4 · 2 con alerta»
 * exigía contar posiciones. Y los controles se agrupan al otro extremo, que es
 * donde la mano espera encontrarlos.
 *
 * # Sobre la barra oscura en los dos temas
 *
 * Se mantiene, y no por inercia: es el cromo de la aplicación, no una
 * superficie de contenido. Un borde superior constante es lo que hace que una
 * PWA a pantalla completa se lea como aplicación y no como una página web. Lo
 * que cambió es el color —de azul pizarra a casi negro— para que no compita con
 * el agua del mapa base.
 */

interface AppHeaderProps {
  total: number
  /** Conteo por tramo de `confidence_level`. */
  byLevel: { unsafe: number; possible: number; confirmed: number }
  withAlert: number
  confirmedOnly: boolean
  onToggleConfirmedOnly: (value: boolean) => void
  /** Se inyecta desde `App`, que es quien posee el estado del tema. */
  themeToggle?: ReactNode
}

/**
 * Marca.
 *
 * El punto que late no es adorno: es la única señal permanente de que la
 * aplicación está viva. Un mapa de emergencias sin incidentes se ve idéntico a
 * un mapa de emergencias congelado, y esa ambigüedad es cara.
 */
function Brand() {
  return (
    <div className="flex shrink-0 items-center gap-2">
      <span className="relative grid size-7 place-items-center">
        {/* Diafragma sísmico: tres arcos concéntricos saliendo de un punto. */}
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          aria-hidden
          className="size-[22px] text-orange-400"
        >
          <circle cx="12" cy="17" r="1.6" fill="currentColor" stroke="none" />
          <path d="M8.6 13.6a4.8 4.8 0 0 1 6.8 0" />
          <path d="M5.8 10.2a8.8 8.8 0 0 1 12.4 0" />
          <path d="M3 6.8a12.8 12.8 0 0 1 18 0" opacity="0.45" />
        </svg>
      </span>

      <h1 className="text-[15px] font-semibold leading-none tracking-[-0.01em]">
        Alerta
        {/*
          La V no es sólo la inicial de Valparaíso: es lo único cromático de la
          marca, así que carga con toda la identidad. Va en el naranja de la
          familia de incendios porque es la capa fundacional del proyecto.
        */}
        <span className="text-orange-400">V</span>
      </h1>
    </div>
  )
}

/** Cápsula de un dato de telemetría. */
function Stat({
  value,
  label,
  dot,
  title,
}: {
  value: number
  label: string
  /** Color del punto. Valor y no clase: viene de la paleta de datos. */
  dot?: string
  title?: string
}) {
  return (
    <span
      title={title}
      className="inline-flex items-center gap-1.5 rounded-full bg-chrome-raised px-2 py-1 text-[11px] leading-none"
    >
      {dot && (
        <span aria-hidden className="size-1.5 rounded-full" style={{ backgroundColor: dot }} />
      )}
      <span className="count font-semibold text-ink-on-chrome">{value}</span>
      <span className="text-white/45">{label}</span>
    </span>
  )
}

export function AppHeader({
  total,
  byLevel,
  withAlert,
  confirmedOnly,
  onToggleConfirmedOnly,
  themeToggle,
}: AppHeaderProps) {
  return (
    <header
      className={cn(
        'relative z-20 flex items-center gap-3 bg-chrome px-3 py-2',
        'pt-[max(0.5rem,env(safe-area-inset-top))] text-ink-on-chrome',
        // El hilo inferior en vez de una sombra: separa la barra del mapa sin
        // ensuciar los primeros píxeles de cartografía con un degradado gris.
        'after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-chrome-edge',
      )}
    >
      <Brand />

      {/*
        Telemetría. `overflow-x-auto` con `scrollbar` oculto: en un teléfono
        estrecho las cápsulas se deslizan en vez de partirse en dos líneas, que
        es lo que hacía crecer la barra y empujar el mapa hacia abajo.
      */}
      <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <Stat value={total} label="activos" title="Incidentes vigentes en las capas encendidas" />

        <span aria-hidden className="h-4 w-px shrink-0 bg-chrome-edge" />

        {LEVEL_ORDER.map((key) => (
          <Stat
            key={key}
            value={byLevel[key]}
            label=""
            dot={LEVEL[key].color}
            title={`${LEVEL[key].label} — ${LEVEL[key].range}`}
          />
        ))}

        {/* Sólo si hay alguna. Una cápsula con un cero permanente es ruido que
            el ojo aprende a ignorar, y el día que valga 1 no lo verá. */}
        {withAlert > 0 && (
          <>
            <span aria-hidden className="h-4 w-px shrink-0 bg-chrome-edge" />
            <Stat value={withAlert} label="con alerta" title="Con alerta vigente de SENAPRED" />
          </>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-1.5">
        {/*
          El widget meteorológico va ANTES del filtro y del tema, y ese orden no
          es casual: es lo único de esta zona que puede cambiar solo. Los otros
          dos son controles —hacen lo que el usuario les pidió la última vez— y
          éste es un indicador. Ponerlo al principio del grupo lo deja pegado a
          la telemetría, que es la otra cosa de la barra que se mueve sola,
          mientras los controles quedan agrupados en el extremo donde la mano
          los busca.

          Fuera de la franja con `overflow-x-auto` a propósito: la telemetría se
          desliza cuando no cabe, y una alerta meteorológica que haya que
          desplazar para ver no es una alerta.
        */}
        <WeatherWidget />

        <label
          className={cn(
            'flex cursor-pointer select-none items-center gap-2 rounded-full px-2.5 py-1.5',
            'text-[11px] font-medium leading-none transition-colors duration-150',
            confirmedOnly
              ? 'bg-orange-500/20 text-orange-200'
              : 'bg-chrome-raised text-white/70 hover:text-ink-on-chrome',
          )}
        >
          <input
            type="checkbox"
            checked={confirmedOnly}
            onChange={(event) => onToggleConfirmedOnly(event.target.checked)}
            className="size-3 accent-orange-500"
          />
          {/* `confirmed_only` del backend filtra por verificación institucional
              (CONAF/Bomberos), no por el tramo `confirmed`. El texto lo dice. */}
          <span className="hidden sm:inline">Verificados en terreno</span>
          <span className="sm:hidden">Verificados</span>
        </label>

        {themeToggle}
      </div>
    </header>
  )
}
