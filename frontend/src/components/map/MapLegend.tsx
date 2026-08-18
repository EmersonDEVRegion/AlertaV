import { useState } from 'react'
import { ALERT, PHENOMENON } from '@/domain/symbology'

/**
 * Leyenda de los dos ejes.
 *
 * No es decoracion. El mapa codifica dos cosas distintas en la misma marca
 * —relleno y anillo— y sin este cuadro nadie puede saber que un anillo rojo
 * sobre un disco amarillo significa "SENAPRED declaro alerta roja, pero nadie
 * confirmo todavia que haya fuego".
 */
export function MapLegend() {
  const [open, setOpen] = useState(false)

  return (
    <div className="pointer-events-auto absolute left-3 top-3 z-10 max-w-[17rem]">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex items-center gap-2 rounded-full bg-white/95 px-3 py-2 text-xs font-semibold text-slate-700 shadow-lg ring-1 ring-slate-900/10 backdrop-blur"
      >
        <span aria-hidden className="text-sm leading-none">
          {open ? '✕' : 'ⓘ'}
        </span>
        {open ? 'Cerrar leyenda' : 'Qué significan los colores'}
      </button>

      {open && (
        <div className="mt-2 rounded-2xl bg-white/97 p-3 text-xs shadow-xl ring-1 ring-slate-900/10 backdrop-blur">
          <p className="mb-2 font-semibold text-slate-900">
            Relleno: estado del hecho
          </p>
          <ul className="space-y-2">
            {Object.entries(PHENOMENON).map(([key, style]) => (
              <li key={key} className="flex gap-2">
                <span
                  aria-hidden
                  className="mt-0.5 size-3.5 shrink-0 rounded-full ring-2 ring-white"
                  style={{ backgroundColor: style.color }}
                />
                <span>
                  <span className="font-medium text-slate-900">{style.label}</span>
                  <span className="block text-slate-600">{style.meaning}</span>
                </span>
              </li>
            ))}
          </ul>

          <p className="mb-2 mt-4 font-semibold text-slate-900">
            Anillo: alerta de SENAPRED
          </p>
          <ul className="space-y-1.5">
            {Object.entries(ALERT).map(([key, style]) => (
              <li key={key} className="flex items-center gap-2">
                <span
                  aria-hidden
                  className="size-3.5 shrink-0 rounded-full border-2 bg-transparent"
                  style={{ borderColor: style.color }}
                />
                <span className="text-slate-700">{style.label}</span>
              </li>
            ))}
          </ul>

          <p className="mt-3 border-t border-slate-200 pt-2 text-[11px] leading-snug text-slate-500">
            Son ejes distintos. Puede haber alerta roja vigente sin que nadie
            haya confirmado el incendio en terreno, y al revés.
          </p>
        </div>
      )}
    </div>
  )
}
