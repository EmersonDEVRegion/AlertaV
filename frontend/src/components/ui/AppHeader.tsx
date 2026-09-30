import { memo } from 'react'
import type { ReactNode } from 'react'
import { WeatherWidget } from '@/components/ui/WeatherWidget'
import { LiveStatus } from '@/components/ui/LiveStatus'
import { PlaceSearch } from '@/components/ui/PlaceSearch'
import type { ExploreArea } from '@/lib/exploreStore'
import { cn } from '@/lib/cn'

/**
 * Barra superior.
 *
 * # Menos es más serio
 *
 * La versión anterior llenaba la barra de cápsulas con números («5 activos ·
 * 0 · 0 · 5») y una casilla de filtro. Se leía como un tablero a medio hacer:
 * cinco cifras sin rótulo que cambian solas y compiten con el mapa. Los sitios
 * de este tipo (Watch Duty, VicEmergency, Apple Maps) dejan la barra casi
 * vacía: marca, un buscador y lo imprescindible.
 *
 *   [marca · en vivo] │ [buscador de comunas] │ [clima] [radar] [avisos] [⋯]
 *
 * - El conteo ya está en la columna («5 en curso»), que es donde se usa.
 * - «Verificados en terreno» pasó a la pestaña Capas: es un filtro del mapa.
 * - El tema pasó al menú «⋯», junto a la leyenda.
 *
 * # Sobre la barra oscura en los dos temas
 *
 * Es el cromo de la aplicación, no una superficie de contenido. Un borde
 * superior constante es lo que hace que una PWA a pantalla completa se lea
 * como aplicación y no como una página web.
 */

interface AppHeaderProps {
  /** El buscador eligió una comuna o un lugar guardado. */
  onPickArea: (area: ExploreArea) => void
  /** La campana de avisos push. Se inyecta desde `App`. */
  notifications?: ReactNode
  /**
   * El botón del radar de vehículos. Se inyecta porque su estado (abierto o
   * cerrado) vive en `App`. `undefined` con `VITE_VEHICLE_RADAR` apagado.
   */
  radar?: ReactNode
  /** El menú «⋯» (tema, leyenda). Lleva el tema, que vive en `App`. */
  menu?: ReactNode
}

/**
 * Marca.
 *
 * Debajo del nombre, «● En vivo · hace 1 min»: la única señal permanente de
 * que la aplicación está viva. Un mapa de emergencias sin incidentes se ve
 * idéntico a uno congelado, y esa ambigüedad es cara.
 */
function Brand() {
  return (
    <div className="flex shrink-0 items-center gap-2">
      <span className="relative grid size-7 place-items-center">
        {/*
          La marca: el mismo triángulo con V de `public/icons/favicon.svg` (§H).
          Antes iban tres arcos fijos que se leían como el ícono de Wi-Fi —como
          si midieran la conexión— y no cambiaban con nada. El estado vivo ya lo
          dice el punto de `LiveStatus`; la marca sólo identifica.

          La V se cala con el color de la barra (`stroke-chrome`) y no con el
          azul marino del ícono, para que siga siendo un hueco en la barra.
        */}
        <svg viewBox="10 10 44 44" aria-hidden className="size-[24px]">
          <path
            d="M32 15 50.5 47H13.5Z"
            fill="#f59e0b"
            stroke="#f59e0b"
            strokeWidth={7}
            strokeLinejoin="round"
          />
          <path
            d="M26 28.2 32 41 38 28.2"
            fill="none"
            className="stroke-chrome"
            strokeWidth={4.4}
            strokeLinecap="round"
            strokeLinejoin="miter"
            strokeMiterlimit={10}
          />
        </svg>
      </span>

      <div className="min-w-0">
        <h1 className="text-[15px] font-semibold leading-none tracking-[-0.01em]">
          Alerta
          {/*
            La V no es sólo la inicial de Valparaíso: repite la V calada del
            triángulo de al lado. Va en el naranja de la familia de incendios
            porque es la capa fundacional del proyecto.
          */}
          <span className="text-orange-400">V</span>
        </h1>
        <div className="mt-1">
          <LiveStatus />
        </div>
      </div>
    </div>
  )
}

export const AppHeader = memo(function AppHeader({
  onPickArea,
  notifications,
  radar,
  menu,
}: AppHeaderProps) {
  return (
    <header
      className={cn(
        'relative z-20 flex items-center gap-2 bg-chrome px-3 py-2 sm:gap-3',
        'pt-[max(0.5rem,env(safe-area-inset-top))] text-ink-on-chrome',
        // El hilo inferior en vez de una sombra: separa la barra del mapa sin
        // ensuciar los primeros píxeles de cartografía con un degradado gris.
        'after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-chrome-edge',
      )}
    >
      <Brand />

      <PlaceSearch onPick={onPickArea} />

      <div className="flex shrink-0 items-center gap-1.5">
        {/* Indicadores que cambian solos (clima, radar) antes que los
            controles (avisos, menú), que quedan en el extremo de la mano. */}
        <WeatherWidget />
        {radar}
        {notifications}
        {menu}
      </div>
    </header>
  )
})
