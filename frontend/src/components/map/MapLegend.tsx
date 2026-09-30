import { memo, useState } from 'react'
import type { ReactNode } from 'react'
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
import { GlyphIcon } from '@/components/ui/GlyphIcon'
import { ICON_GLYPHS, ICON_IDS, OUTAGE_ICON, WATER_ICON } from '@/domain/emergencyIcons'
import { OUTAGE_CLUSTER, PROVIDER, PROVIDER_ORDER } from '@/domain/powerSymbology'
import { WATER } from '@/domain/waterSymbology'
import { cn } from '@/lib/cn'

/**
 * El rayo y la gota van aparte, cada uno en su sección («Cortes de luz» y
 * «Cortes de agua»): no son incidentes y su color no mide confianza.
 */
const LEGEND_ICONS = ICON_IDS.filter((id) => id !== OUTAGE_ICON && id !== WATER_ICON)

/**
 * Leyenda de la política de confianza v2.0.0.
 *
 * No es decoración. La escala es deliberadamente contraintuitiva —el rojo marca
 * baja confianza, no emergencia— y el mapa además codifica estado y
 * verificación institucional como textura. Sin este cuadro, un pin rojo se lee
 * exactamente al revés de lo que significa.
 */
export const MapLegend = memo(function MapLegend() {
  const [open, setOpen] = useState(false)

  return (
    /*
     * Sin posicionamiento propio: vive dentro del riel izquierdo que arma
     * `App`, debajo del controlador de capas de referencia. Antes se anclaba
     * sola a `left-3 top-3`, que es exactamente donde ahora va el dock, y dos
     * elementos absolutos peleando por la misma esquina es la clase de colisión
     * que sólo se ve en la pantalla de alguien más.
     */
    <div className="pointer-events-auto flex min-h-0 flex-col">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className={cn(
          'surface-floating flex w-full shrink-0 items-center gap-2 px-3 py-2',
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
        <div className="animate-rise surface-floating mt-2 max-h-[26rem] min-h-0 overflow-y-auto overscroll-contain p-3 text-xs">
          <LegendBody />
        </div>
      )}
    </div>
  )
})

/**
 * El cuerpo de la leyenda, sin superficie ni control de apertura.
 *
 * # Dos niveles
 *
 * Abrir la leyenda desplegaba nueve bloques seguidos —tabla, rangos, aviso,
 * marcas, anillos, íconos, luz, agua y sismos— en una tarjeta de 15 rem. Lo
 * que se busca de un vistazo son tres cosas: **qué es cada ícono**, **qué dice
 * el color** y **que el rojo no es gravedad**. Eso queda arriba, en unos 200 px.
 * Lo demás es consulta y va plegado en `<details>`, que es accesible sin una
 * línea de JavaScript y recuerda su estado mientras la leyenda esté abierta.
 *
 * Separado de `MapLegend` porque en teléfono no hay riel: lo monta una ficha de
 * `MobileMapControls`. Ver `hooks/useMediaQuery.ts`.
 */
export function LegendBody() {
  return (
    <div className="space-y-3 text-[11px]">
      {/* --- 1. Qué es cada ícono ----------------------------------------- */}
      <div role="group" aria-label="Qué es cada ícono">
        <p className="mb-1.5 font-semibold text-ink">Qué es cada ícono</p>
        <ul className="grid grid-cols-2 gap-x-3 gap-y-1">
          {LEGEND_ICONS.map((id) => (
            <li key={id} className="flex min-w-0 items-center gap-1.5 text-ink-muted">
              <GlyphIcon id={id} className="size-3.5 shrink-0 text-ink" />
              <span className="truncate">{ICON_GLYPHS[id].label}</span>
            </li>
          ))}
          <li className="flex min-w-0 items-center gap-1.5 text-ink-muted">
            <span
              aria-hidden
              className="grid size-3.5 shrink-0 place-items-center rounded-full"
              style={{ backgroundColor: PROVIDER.chilquinta.color }}
            >
              <GlyphIcon id={OUTAGE_ICON} className="size-2.5" color="#ffffff" />
            </span>
            <span className="truncate">Corte de luz</span>
          </li>
          <li className="flex min-w-0 items-center gap-1.5 text-ink-muted">
            <span
              aria-hidden
              className="grid size-3.5 shrink-0 place-items-center rounded-full"
              style={{ backgroundColor: WATER.color }}
            >
              <GlyphIcon id={WATER_ICON} className="size-2.5" color={WATER.onColor} />
            </span>
            <span className="truncate">Corte de agua</span>
          </li>
        </ul>
      </div>

      {/* --- 2. Qué dice el color ----------------------------------------- */}
      <div role="group" aria-label="Qué dice el color">
        <p className="flex items-baseline justify-between font-semibold text-ink">
          <span>El color mide evidencia</span>
          <span className="text-[9.5px] font-normal text-ink-faint">menos → más</span>
        </p>
        <ul className="mt-1 space-y-0.5">
          {(
            [
              ['fire', LEVEL],
              ['traffic', TRAFFIC_LEVEL],
              ['otros', OTHER_LEVEL],
            ] as const
          ).map(([key, palette]) => (
            <li key={key} className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-ink-muted">
                {LAYER_LABEL[key as IncidentLayerKey]}
              </span>
              <span className="flex shrink-0 gap-1">
                {LEVEL_ORDER.map((level) => (
                  <span
                    key={level}
                    aria-hidden
                    title={`${palette[level].label} (${LEVEL[level].range})`}
                    className="size-3 rounded-full ring-2 ring-white"
                    style={{ backgroundColor: palette[level].color }}
                  />
                ))}
              </span>
            </li>
          ))}
        </ul>
        <p className="callout callout-danger mt-2 text-[10.5px] leading-snug">
          <strong>No mide gravedad.</strong> En incendios, el rojo es una señal
          sin corroborar; el naranja, la que tiene más evidencia.
        </p>
      </div>

      {/* --- 3. Consulta, plegada ----------------------------------------- */}
      <div className="divide-y divide-line border-t border-line">
        <LegendDetails title="Rangos de confianza">
          <ul className="space-y-1 text-ink-muted">
            {LEVEL_ORDER.map((key) => (
              <li key={key}>
                <span className="font-medium text-ink">{LEVEL[key].range}</span>
                {' — '}
                {LEVEL[key].meaning}
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-[10.5px] leading-snug text-ink-muted">
            La familia decide la paleta; el tono, cuánta evidencia respalda el
            incidente. En las capas frías la intensidad crece con la evidencia.
          </p>
        </LegendDetails>

        <LegendDetails title="Marcas y alertas de SENAPRED">
          <ul className="space-y-1.5">
            <li className="flex gap-2">
              <span
                aria-hidden
                className="relative mt-0.5 grid size-3.5 shrink-0 place-items-center rounded-full ring-2 ring-white"
                style={{ backgroundColor: LEVEL.confirmed.color }}
              >
                <span className="size-1.5 rounded-full bg-raised" />
              </span>
              <span className="text-ink-muted">
                <span className="font-medium text-ink">Centro hueco:</span> evidencia
                suficiente, pero nadie lo verificó en terreno.
              </span>
            </li>
            <li className="flex gap-2">
              <span
                aria-hidden
                className="mt-0.5 size-3.5 shrink-0 rounded-full border border-line-strong"
                style={{ backgroundColor: MUTED_LEVEL.confirmed }}
              />
              <span className="text-ink-muted">
                <span className="font-medium text-ink">Color apagado:</span> cerrado
                (controlado, extinguido o sin señales nuevas).
              </span>
            </li>
            {Object.entries(ALERT).map(([key, style]) => (
              <li key={key} className="flex items-center gap-2">
                <span
                  aria-hidden
                  className="size-3.5 shrink-0 rounded-full border-2 bg-transparent"
                  style={{ borderColor: style.color }}
                />
                <span className="text-ink-muted">Anillo: {style.label}</span>
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-[10.5px] leading-snug text-ink-muted">
            La alerta y la evidencia son ejes distintos: puede haber alerta roja
            sobre un incidente de baja confianza, y al revés.
          </p>
        </LegendDetails>

        <LegendDetails title="Cortes de luz y agua">
          <ul className="space-y-1.5">
            {PROVIDER_ORDER.map((provider) => (
              <li key={provider} className="flex items-center gap-2">
                <span
                  aria-hidden
                  className="grid size-4 shrink-0 place-items-center rounded-full ring-2 ring-white"
                  style={{ backgroundColor: PROVIDER[provider].color }}
                >
                  <GlyphIcon id={OUTAGE_ICON} className="size-2.5" color="#ffffff" />
                </span>
                <span className="text-ink-muted">{PROVIDER[provider].label}</span>
              </li>
            ))}
            <li className="flex items-center gap-2">
              <span
                aria-hidden
                className="grid size-4 shrink-0 place-items-center rounded-full text-[9px] font-semibold ring-2 ring-white"
                style={{ backgroundColor: OUTAGE_CLUSTER.color, color: OUTAGE_CLUSTER.onColor }}
              >
                12
              </span>
              <span className="text-ink-muted">Varios cortes juntos: tócalo para acercarte.</span>
            </li>
            <li className="flex items-center gap-2">
              <span
                aria-hidden
                className="grid size-4 shrink-0 place-items-center rounded-full ring-2 ring-white"
                style={{ backgroundColor: WATER.color }}
              >
                <GlyphIcon id={WATER_ICON} className="size-2.5" color={WATER.onColor} />
              </span>
              <span className="text-ink-muted">
                Agua (Esval): información de servicio, va debajo de todo.
              </span>
            </li>
          </ul>
          <p className="mt-1.5 text-[10.5px] leading-snug text-ink-muted">
            Un corte de luz sale del mapa cuando la empresa deja de listarlo.
          </p>
        </LegendDetails>

        <LegendDetails title="Sismos: escala propia">
          <ul className="space-y-1.5">
            {MAGNITUDE_ORDER.map((key) => {
              const style = MAGNITUDE[key]
              const size =
                key === 'desconocido'
                  ? 8
                  : legendRadius(key === 'menor' ? 3 : key === 'moderado' ? 4.8 : 6.5)
              return (
                <li key={key} className="flex items-start gap-2">
                  <span className="grid w-6 shrink-0 place-items-center pt-0.5">
                    <span
                      aria-hidden
                      className="rounded-full"
                      style={{ width: size, height: size, border: `2px solid ${style.color}` }}
                    />
                  </span>
                  <span className="text-ink-muted">
                    <span className="font-medium text-ink">{style.label}</span>
                    <span className="ml-1 text-ink-faint">({style.range})</span>
                  </span>
                </li>
              )
            })}
          </ul>
          <p className="mt-1.5 text-[10.5px] leading-snug text-ink-muted">
            Color y tamaño miden magnitud, no confianza: un sismo es un hecho
            medido. El trazo tenue marca una solución preliminar del USGS.
          </p>
        </LegendDetails>
      </div>
    </div>
  )
}

function LegendDetails({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="group">
      <summary
        className={cn(
          'flex cursor-pointer list-none items-center gap-1.5 py-1.5 font-medium text-ink-muted',
          'transition-colors hover:text-ink [&::-webkit-details-marker]:hidden',
        )}
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
          className="size-3 shrink-0 -rotate-90 transition-transform duration-200 group-open:rotate-0"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
        {title}
      </summary>
      <div className="pb-2 pl-4">{children}</div>
    </details>
  )
}
