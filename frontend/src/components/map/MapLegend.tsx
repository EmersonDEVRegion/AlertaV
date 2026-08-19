import { useState } from 'react'
import { ALERT, LEVEL, LEVEL_ORDER, MUTED_LEVEL } from '@/domain/symbology'
import {
  MAGNITUDE,
  MAGNITUDE_ORDER,
  legendRadius,
} from '@/domain/seismicSymbology'

/**
 * Leyenda de la política de confianza v2.0.0.
 *
 * No es decoración. La escala es deliberadamente contraintuitiva —el rojo marca
 * baja confianza, no emergencia— y el mapa además codifica estado y
 * verificación institucional como textura. Sin este cuadro, un pin rojo se lee
 * exactamente al revés de lo que significa.
 */
export function MapLegend() {
  const [open, setOpen] = useState(false)

  return (
    <div className="pointer-events-auto absolute left-3 top-3 z-10 max-w-[18.5rem]">
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
        <div className="mt-2 max-h-[70dvh] overflow-y-auto rounded-2xl bg-white/97 p-3 text-xs shadow-xl ring-1 ring-slate-900/10 backdrop-blur">
          <p className="font-semibold text-slate-900">Color: nivel de confianza</p>
          <p className="mb-2 text-[11px] leading-snug text-slate-500">
            Mide cuánta evidencia respalda el incidente, no cuán grave es.
          </p>

          <ul className="space-y-2">
            {LEVEL_ORDER.map((key) => {
              const style = LEVEL[key]
              return (
                <li key={key} className="flex gap-2">
                  <span
                    aria-hidden
                    className="mt-0.5 size-3.5 shrink-0 rounded-full ring-2 ring-white"
                    style={{ backgroundColor: style.color }}
                  />
                  <span>
                    <span className="font-medium text-slate-900">{style.label}</span>
                    <span className="ml-1 text-slate-400">({style.range})</span>
                    <span className="block text-slate-600">{style.meaning}</span>
                  </span>
                </li>
              )
            })}
          </ul>

          <p className="mt-2 rounded-lg bg-red-50 px-2.5 py-2 text-[11px] leading-snug text-red-900 ring-1 ring-red-200">
            <strong>El rojo advierte sobre el dato, no sobre el fuego.</strong> Marca
            una señal que todavía no se pudo corroborar; el naranja es el tramo
            con más evidencia.
          </p>

          <p className="mt-4 font-semibold text-slate-900">Marcas sobre el color</p>
          <ul className="mt-2 space-y-2">
            <li className="flex gap-2">
              <span
                aria-hidden
                className="relative mt-0.5 grid size-3.5 shrink-0 place-items-center rounded-full ring-2 ring-white"
                style={{ backgroundColor: LEVEL.confirmed.color }}
              >
                <span className="size-1.5 rounded-full bg-white" />
              </span>
              <span>
                <span className="font-medium text-slate-900">Centro hueco</span>
                <span className="block text-slate-600">
                  Nivel confirmado por acumulación de evidencia, pero ninguna
                  fuente lo verificó en terreno.
                </span>
              </span>
            </li>
            <li className="flex gap-2">
              <span
                aria-hidden
                className="mt-0.5 size-3.5 shrink-0 rounded-full border border-slate-600"
                style={{ backgroundColor: MUTED_LEVEL.confirmed }}
              />
              <span>
                <span className="font-medium text-slate-900">Color apagado</span>
                <span className="block text-slate-600">
                  Incidente cerrado: controlado, extinguido o sin señales nuevas.
                </span>
              </span>
            </li>
          </ul>

          <p className="mt-4 font-semibold text-slate-900">Anillo: alerta de SENAPRED</p>
          <ul className="mt-2 space-y-1.5">
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
            Son ejes distintos. Puede haber alerta roja vigente sobre un incidente
            de baja confianza, y al revés.
          </p>

          {/* --- Capa sísmica: escala propia, sin relación con la anterior --- */}
          <p className="mt-4 border-t border-slate-200 pt-3 font-semibold text-slate-900">
            Sismos: círculos huecos
          </p>
          <p className="mb-2 text-[11px] leading-snug text-slate-500">
            Escala aparte. Acá el color y el tamaño miden magnitud, no confianza:
            un sismo es un hecho medido, no una hipótesis.
          </p>
          <ul className="space-y-2">
            {MAGNITUDE_ORDER.map((key) => {
              const style = MAGNITUDE[key]
              const size = key === 'desconocido' ? 8 : legendRadius(
                key === 'menor' ? 3 : key === 'moderado' ? 4.8 : 6.5,
              )
              return (
                <li key={key} className="flex items-start gap-2">
                  <span className="grid w-6 shrink-0 place-items-center pt-0.5">
                    <span
                      aria-hidden
                      className="rounded-full"
                      style={{
                        width: size,
                        height: size,
                        border: `2px solid ${style.color}`,
                      }}
                    />
                  </span>
                  <span>
                    <span className="font-medium text-slate-900">{style.label}</span>
                    <span className="ml-1 text-slate-400">({style.range})</span>
                    <span className="block text-slate-600">{style.meaning}</span>
                  </span>
                </li>
              )
            })}
          </ul>
          <p className="mt-2 text-[11px] leading-snug text-slate-500">
            El tamaño crece con la magnitud de forma perceptual, no logarítmica:
            reproducir la energía real dejaría los sismos menores invisibles.
            El trazo tenue marca una solución preliminar del USGS.
          </p>
        </div>
      )}
    </div>
  )
}
