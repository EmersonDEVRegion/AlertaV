/**
 * Control de capas superpuesto al mapa.
 *
 * `Accidentes viales` va deshabilitado con la etiqueta «Próximamente»: el
 * backend no tiene fuente de despachos viales todavía. Se muestra igual porque
 * comunica hacia dónde va el producto, pero deshabilitado de verdad —`disabled`
 * en el input, no sólo apagado visualmente— para que ni el teclado ni un lector
 * de pantalla lo ofrezcan como algo accionable.
 */

export interface LayerVisibility {
  incidents: boolean
  seismic: boolean
}

interface LayerTogglesProps {
  visibility: LayerVisibility
  onChange: (next: LayerVisibility) => void
  counts: { incidents: number; seismic: number }
}

interface Row {
  key: keyof LayerVisibility | 'traffic'
  label: string
  /** Punto de color que representa la capa en el mapa. */
  swatch: string
  hollow?: boolean
  disabled?: boolean
}

const ROWS: readonly Row[] = [
  { key: 'incidents', label: 'Incendios', swatch: '#ea580c' },
  { key: 'seismic', label: 'Sismos', swatch: '#f97316', hollow: true },
  { key: 'traffic', label: 'Accidentes viales', swatch: '#94a3b8', disabled: true },
]

export function LayerToggles({ visibility, onChange, counts }: LayerTogglesProps) {
  return (
    <fieldset
      className="pointer-events-auto absolute right-3 top-[8.5rem] z-10 rounded-2xl bg-white/95 p-2.5 shadow-lg ring-1 ring-slate-900/10 backdrop-blur md:top-[9.5rem]"
    >
      <legend className="sr-only">Capas del mapa</legend>

      <ul className="space-y-1">
        {ROWS.map((row) => {
          const isLayer = row.key !== 'traffic'
          const checked = isLayer ? visibility[row.key as keyof LayerVisibility] : false
          const count = isLayer ? counts[row.key as keyof LayerVisibility] : 0

          return (
            <li key={row.key}>
              <label
                className={
                  'flex items-center gap-2 rounded-lg px-1.5 py-1 text-xs ' +
                  (row.disabled
                    ? 'cursor-not-allowed text-slate-400'
                    : 'cursor-pointer text-slate-800 hover:bg-slate-100')
                }
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={row.disabled}
                  onChange={(event) =>
                    isLayer &&
                    onChange({ ...visibility, [row.key]: event.target.checked })
                  }
                  className="size-3.5 shrink-0 accent-orange-500 disabled:opacity-40"
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

                {row.disabled ? (
                  <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500 ring-1 ring-slate-200">
                    Próximamente
                  </span>
                ) : (
                  <span className="min-w-4 text-right text-[11px] tabular-nums text-slate-500">
                    {count}
                  </span>
                )}
              </label>
            </li>
          )
        })}
      </ul>
    </fieldset>
  )
}
