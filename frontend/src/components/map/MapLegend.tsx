import { useState } from 'react'
import { ALERT, LEVEL, LEVEL_ORDER, MUTED_LEVEL } from '@/domain/symbology'
import { LAYER_LABEL } from '@/domain/families'
import type { IncidentLayerKey } from '@/domain/families'
import { OTHER_LEVEL } from '@/domain/otherSymbology'
import { TRAFFIC_LEVEL } from '@/domain/trafficSymbology'
import {
  MAGNITUDE,
  MAGNITUDE_ORDER,
  legendRadius,
} from '@/domain/seismicSymbology'
import { cn } from '@/lib/cn'

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
    /*
     * Sin posicionamiento propio: vive dentro del riel izquierdo que arma
     * `App`, debajo del controlador de capas de referencia. Antes se anclaba
     * sola a `left-3 top-3`, que es exactamente donde ahora va el dock, y dos
     * elementos absolutos peleando por la misma esquina es la clase de colisión
     * que sólo se ve en la pantalla de alguien más.
     */
    <div className="pointer-events-auto">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className={cn(
          'surface-floating flex w-full items-center gap-2 px-3 py-2',
          'text-[11px] font-semibold text-ink-muted',
          'transition-[color,scale] duration-150 hover:text-ink active:scale-[0.99]',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent',
        )}
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
          className={cn('size-3.5 shrink-0 transition-transform duration-300', open && 'rotate-45')}
        >
          {/* La misma «+» girada 45° es la «×» de cerrar: una sola forma que
              rota, en vez de dos nodos intercambiados que saltarían. */}
          <path d="M12 5v14M5 12h14" />
        </svg>
        <span className="min-w-0 flex-1 truncate text-left">
          {open ? 'Cerrar leyenda' : 'Qué significan los colores'}
        </span>
      </button>

      {open && (
        <div className="animate-rise surface-floating mt-2 max-h-[60dvh] overflow-y-auto overscroll-contain p-3 text-xs">
          <LegendBody />
        </div>
      )}
    </div>
  )
}

/**
 * El cuerpo de la leyenda, sin superficie ni control de apertura.
 *
 * Separado porque en teléfono la leyenda no es una tarjeta que se despliega bajo
 * un botón del riel izquierdo —ese riel no existe a 430 px— sino una ficha más
 * de `MobileMapControls`, que ya aporta la superficie y el gesto de abrir. Ver
 * la nota de `hooks/useMediaQuery.ts`.
 */
export function LegendBody() {
  return (
    <>
      <p className="font-semibold text-ink">Color: tipo y confianza</p>
      <p className="mb-2 text-[11px] leading-snug text-ink-muted">
        La familia decide la paleta; el tono dentro de ella, cuánta evidencia
        respalda el incidente. La confianza no mide gravedad.
      </p>

      {/* Una fila por familia: tres muestras de color y su rango. La tabla
          comparada se lee mucho más rápido que tres listas seguidas. */}
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-ink-faint">
            <th className="pb-1 text-left font-medium">Capa</th>
            <th className="pb-1 font-medium">&lt;30 %</th>
            <th className="pb-1 font-medium">30-60 %</th>
            <th className="pb-1 font-medium">&gt;60 %</th>
          </tr>
        </thead>
        <tbody>
          {(
            [
              ['fire', LEVEL],
              ['traffic', TRAFFIC_LEVEL],
              ['otros', OTHER_LEVEL],
            ] as const
          ).map(([key, palette]) => (
            <tr key={key}>
              <td className="py-1 pr-2 font-medium text-ink">
                {LAYER_LABEL[key as IncidentLayerKey]}
              </td>
              {LEVEL_ORDER.map((level) => (
                <td key={level} className="py-1 text-center">
                  <span
                    aria-hidden
                    title={palette[level].label}
                    className="inline-block size-3.5 rounded-full ring-2 ring-white"
                    style={{ backgroundColor: palette[level].color }}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      <ul className="mt-2 space-y-1 text-[11px] text-ink-muted">
        {LEVEL_ORDER.map((key) => (
          <li key={key}>
            <span className="font-medium text-ink">{LEVEL[key].range}</span>
            {' — '}
            {LEVEL[key].meaning}
          </li>
        ))}
      </ul>

      <p className="mt-2 callout callout-danger">
        <strong>En incendios, el rojo advierte sobre el dato, no sobre el
        fuego.</strong> Marca una señal que todavía no se pudo corroborar; el
        naranja es el tramo con más evidencia. En las capas frías la
        intensidad sí crece con la evidencia.
      </p>

      <p className="mt-4 font-semibold text-ink">Marcas sobre el color</p>
      <ul className="mt-2 space-y-2">
        <li className="flex gap-2">
          <span
            aria-hidden
            className="relative mt-0.5 grid size-3.5 shrink-0 place-items-center rounded-full ring-2 ring-white"
            style={{ backgroundColor: LEVEL.confirmed.color }}
          >
            <span className="size-1.5 rounded-full bg-raised" />
          </span>
          <span>
            <span className="font-medium text-ink">Centro hueco</span>
            <span className="block text-ink-muted">
              Nivel confirmado por acumulación de evidencia, pero ninguna
              fuente lo verificó en terreno.
            </span>
          </span>
        </li>
        <li className="flex gap-2">
          <span
            aria-hidden
            className="mt-0.5 size-3.5 shrink-0 rounded-full border border-line-strong"
            style={{ backgroundColor: MUTED_LEVEL.confirmed }}
          />
          <span>
            <span className="font-medium text-ink">Color apagado</span>
            <span className="block text-ink-muted">
              Incidente cerrado: controlado, extinguido o sin señales nuevas.
            </span>
          </span>
        </li>
      </ul>

      <p className="mt-4 font-semibold text-ink">Anillo: alerta de SENAPRED</p>
      <ul className="mt-2 space-y-1.5">
        {Object.entries(ALERT).map(([key, style]) => (
          <li key={key} className="flex items-center gap-2">
            <span
              aria-hidden
              className="size-3.5 shrink-0 rounded-full border-2 bg-transparent"
              style={{ borderColor: style.color }}
            />
            <span className="text-ink-muted">{style.label}</span>
          </li>
        ))}
      </ul>

      <p className="mt-3 border-t border-line pt-2 text-[11px] leading-snug text-ink-muted">
        Son ejes distintos. Puede haber alerta roja vigente sobre un incidente
        de baja confianza, y al revés.
      </p>

      {/* --- Capa sísmica: escala propia, sin relación con la anterior --- */}
      <p className="mt-4 border-t border-line pt-3 font-semibold text-ink">
        Sismos: círculos huecos
      </p>
      <p className="mb-2 text-[11px] leading-snug text-ink-muted">
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
                <span className="font-medium text-ink">{style.label}</span>
                <span className="ml-1 text-ink-faint">({style.range})</span>
                <span className="block text-ink-muted">{style.meaning}</span>
              </span>
            </li>
          )
        })}
      </ul>
      <p className="mt-2 text-[11px] leading-snug text-ink-muted">
        El tamaño crece con la magnitud de forma perceptual, no logarítmica:
        reproducir la energía real dejaría los sismos menores invisibles.
        El trazo tenue marca una solución preliminar del USGS.
      </p>
    </>
  )
}
