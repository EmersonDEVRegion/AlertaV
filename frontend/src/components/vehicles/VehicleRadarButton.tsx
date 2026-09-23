import type { Ref } from 'react'
import type { VehicleFeedState } from '@/hooks/useVehicleFeed'
import { cn } from '@/lib/cn'
import { RadarGlyph } from './RadarGlyph'
import { RADAR_PANEL_ID } from './VehicleRadarPanel'

/**
 * Botón del radar en la barra superior, con su contador.
 *
 * # Por qué en la barra y no flotando sobre el mapa
 *
 * Por lo mismo que la campana: estos avisos **no son una capa del mapa**. No
 * tienen coordenadas, y un botón sobre el terreno sugeriría que las tienen. El
 * borde inferior, además, ya lo ocupan el botón de reporte (el único urgente de
 * la pantalla), la ficha del incidente en teléfono y la atribución.
 *
 * # El contador
 *
 *   - **Hay avisos de menos de 6 h:** rojo, con latido. Es lo que hay que mirar.
 *   - **Hay avisos, ninguno reciente:** neutro, blanco sobre la barra.
 *   - **Cero y la fuente está sana:** no hay contador. Una cápsula con un cero
 *     permanente es ruido que el ojo aprende a ignorar (regla de `AppHeader`).
 *   - **Cero y la fuente NO está sana:** «–». Un «0» ahí afirmaría calma.
 *   - **Cargando o con error:** nada. No saber no autoriza a mostrar un número.
 */

interface VehicleRadarButtonProps {
  feed: VehicleFeedState
  open: boolean
  onToggle: () => void
  ref?: Ref<HTMLButtonElement>
}

/** Lo que dice el contador, o `null` si no va. Aparte para poder probarlo. */
export function badgeFor(feed: Pick<VehicleFeedState, 'status' | 'count'>): string | null {
  if (feed.status === 'ready') return feed.count > 99 ? '99+' : String(feed.count)
  if (feed.status === 'blind') return '–'
  return null
}

export function radarLabel(feed: Pick<VehicleFeedState, 'status' | 'count' | 'recentCount'>): string {
  switch (feed.status) {
    case 'ready': {
      const avisos = feed.count === 1 ? '1 aviso' : `${feed.count} avisos`
      const nuevos = feed.recentCount > 0 ? `, ${feed.recentCount} de las últimas 6 h` : ''
      return `Radar de vehículos: ${avisos} en las últimas 48 h${nuevos}`
    }
    case 'empty':
      return 'Radar de vehículos: sin avisos en las últimas 48 h'
    case 'blind':
      return 'Radar de vehículos: sin datos de la fuente'
    default:
      return 'Radar de vehículos'
  }
}

export function VehicleRadarButton({ feed, open, onToggle, ref }: VehicleRadarButtonProps) {
  const badge = badgeFor(feed)
  const hot = feed.status === 'ready' && feed.recentCount > 0
  const label = radarLabel(feed)

  return (
    <button
      ref={ref}
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      aria-controls={open ? RADAR_PANEL_ID : undefined}
      aria-label={label}
      title={label}
      className={cn(
        'relative grid size-8 shrink-0 place-items-center rounded-full',
        'bg-chrome-raised text-ink-on-chrome',
        'transition-[background-color,scale,box-shadow] duration-150 active:scale-[0.94]',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
        'focus-visible:outline-accent',
        open && 'shadow-[inset_0_0_0_1px_rgb(255_255_255/0.35)]',
        !open && feed.status !== 'ready' && 'text-white/60',
      )}
    >
      <RadarGlyph className="size-4" />

      {badge !== null && (
        <span
          aria-hidden
          className={cn(
            'absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full px-1',
            'text-[9.5px] font-bold leading-none tabular-nums',
            // El anillo del color de la barra separa la cápsula del botón.
            'shadow-[0_0_0_2px_var(--surface-chrome)]',
            // El latido de la barra (`animate-pulse-soft`) y no el halo de la
            // tarjeta: un anillo que crece 2,6 veces desde una cápsula de 16 px
            // taparía la campana de al lado.
            hot ? 'animate-pulse-soft bg-urgent text-urgent-ink' : 'bg-ink-on-chrome text-chrome',
          )}
        >
          {badge}
        </span>
      )}
    </button>
  )
}
