import { LAYER_LABEL } from '@/domain/families'
import { OTHER_LEVEL } from '@/domain/otherSymbology'
import { LEVEL } from '@/domain/symbology'
import { TRAFFIC_LEVEL } from '@/domain/trafficSymbology'

/**
 * Control de capas superpuesto al mapa.
 *
 * Las tres capas de incidentes filtran por familia sobre la misma consulta a
 * `/incidents/active`. La de sismos es aparte: viene de `/events/seismic`, y
 * apagarla además detiene su polling.
 */

export interface LayerVisibility {
  fire: boolean
  traffic: boolean
  otros: boolean
  seismic: boolean
}

export const DEFAULT_LAYER_VISIBILITY: LayerVisibility = {
  fire: true,
  traffic: true,
  otros: true,
  seismic: true,
}

interface LayerTogglesProps {
  visibility: LayerVisibility
  onChange: (next: LayerVisibility) => void
  counts: Record<keyof LayerVisibility, number>
}

interface Row {
  key: keyof LayerVisibility
  label: string
  swatch: string
  /** Los sismos se dibujan como círculo hueco; el swatch lo refleja. */
  hollow?: boolean
}

const ROWS: readonly Row[] = [
  { key: 'fire', label: LAYER_LABEL.fire, swatch: LEVEL.confirmed.color },
  { key: 'traffic', label: LAYER_LABEL.traffic, swatch: TRAFFIC_LEVEL.confirmed.color },
  { key: 'otros', label: LAYER_LABEL.otros, swatch: OTHER_LEVEL.confirmed.color },
  { key: 'seismic', label: 'Sismos', swatch: '#f97316', hollow: true },
]

export function LayerToggles({ visibility, onChange, counts }: LayerTogglesProps) {
  return (
    <fieldset className="pointer-events-auto absolute right-3 top-[8.5rem] z-10 rounded-2xl bg-white/95 p-2.5 shadow-lg ring-1 ring-slate-900/10 backdrop-blur md:top-[9.5rem]">
      <legend className="sr-only">Capas del mapa</legend>

      <ul className="space-y-1">
        {ROWS.map((row) => (
          <li key={row.key}>
            <label className="flex cursor-pointer items-center gap-2 rounded-lg px-1.5 py-1 text-xs text-slate-800 hover:bg-slate-100">
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

              <span className="flex-1 whitespace-nowrap font-medium">{row.label}</span>

              <span className="min-w-4 text-right text-[11px] tabular-nums text-slate-500">
                {counts[row.key]}
              </span>
            </label>
          </li>
        ))}
      </ul>
    </fieldset>
  )
}
