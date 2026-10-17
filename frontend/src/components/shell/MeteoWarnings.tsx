import { memo, useId, useState } from 'react'
import type { MeteoWarning } from '@/api/meteoWarningTypes'
import { GlyphIcon } from '@/components/ui/GlyphIcon'
import { SectionToggle } from '@/components/ui/SectionToggle'
import {
  WARNING_STYLE,
  phenomenonLabel,
  vigenciaLabel,
  zonasLabel,
} from '@/domain/meteoWarnings'
import { WEATHER_GLYPHS, weatherGlyphFor } from '@/domain/weatherGlyphs'
import { useMeteoWarnings, type MeteoWarningsState } from '@/hooks/useMeteoWarnings'
import { useNow } from '@/hooks/useNow'
import { cn } from '@/lib/cn'
import { showWarningOnMap } from '@/lib/meteoZonesStore'
import { setSheetSnap } from '@/lib/sheetStore'
import { useSectionOpen } from '@/lib/sectionStore'

/**
 * Avisos, alertas y alarmas de la DMC para la región, arriba de «Ahora».
 *
 * Es la respuesta a «¿qué puede venir?»: viento o lluvia anuncian cortes de
 * luz, árboles caídos y choques antes de que ocurran. Por eso va en «Ahora» y
 * no pegado a cada incidente: un pronóstico regional no es un juicio sobre un
 * corte en particular.
 *
 * Sin avisos no se dibuja nada: la calma no necesita una tarjeta. Con la
 * fuente caída y sin avisos, sí se dice, porque esa lista vacía no es calma.
 *
 * «Ver en el mapa» enciende la capa «Avisos DMC», resalta las zonas del aviso
 * y las encuadra; en el teléfono baja la hoja para que se vean.
 */

function verEnElMapa(id: string): void {
  showWarningOnMap(id)
  setSheetSnap('peek')
}

/** Una vez por minuto basta: la vigencia de la DMC se mide en tramos del día. */
const TICK_MS = 60_000

function WarningGlyph({ fenomeno }: { fenomeno: string | null }) {
  const id = weatherGlyphFor(fenomeno)
  if (!id) return <GlyphIcon id="av-alert" className="size-5 shrink-0" />
  return (
    <svg viewBox="0 0 24 24" aria-hidden focusable="false" className="size-5 shrink-0" fill="currentColor">
      <path d={WEATHER_GLYPHS[id].path} fillRule="evenodd" />
    </svg>
  )
}

const WarningCard = memo(function WarningCard({ warning }: { warning: MeteoWarning }) {
  const [open, setOpen] = useState(false)
  const bodyId = useId()
  const style = WARNING_STYLE[warning.severidad]
  const vigencia = vigenciaLabel(warning)

  return (
    <li className={cn('overflow-hidden rounded-control border-l-4', style.card, style.edge)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={bodyId}
        className="flex w-full items-start gap-2 px-2.5 py-2 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      >
        <WarningGlyph fenomeno={warning.fenomeno} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span
              className={cn(
                'shrink-0 rounded-full px-1.5 py-px text-[9.5px] font-bold uppercase tracking-wide',
                style.chip,
              )}
            >
              DMC · {warning.tipo}
            </span>
            <span className="min-w-0 truncate text-[12px] font-semibold">
              {phenomenonLabel(warning)}
            </span>
          </span>
          {vigencia && <span className="mt-0.5 block text-[11px] font-medium">{vigencia}</span>}
          <span className="block text-[10.5px] opacity-80">{zonasLabel(warning)}</span>
        </span>
      </button>

      {open && (
        <div id={bodyId} className="space-y-1.5 px-2.5 pb-2.5 text-[11px] leading-snug">
          {warning.observacion && <p className="font-medium">{warning.observacion}</p>}
          {warning.titulo && <p className="opacity-90">{warning.titulo}</p>}
          {warning.tabla && (
            <div className="overflow-x-auto">
              {warning.tabla.titulo && (
                <p className="mb-0.5 text-[10px] font-semibold opacity-80">{warning.tabla.titulo}</p>
              )}
              <table className="w-full border-collapse text-[10.5px]">
                {warning.tabla.columnas.length > 0 && (
                  <thead>
                    <tr>
                      <th className="pr-2 text-left font-semibold">Zona</th>
                      {warning.tabla.columnas.map((c) => (
                        <th key={c} className="pr-2 text-left font-semibold">
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                )}
                <tbody>
                  {warning.tabla.filas.map((f) => (
                    <tr key={f.zona}>
                      <td className="pr-2 align-top">{f.zona}</td>
                      {f.valores.map((v, i) => (
                        <td key={i} className="pr-2 align-top font-medium tabular-nums">
                          {v}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <button
            type="button"
            onClick={() => verEnElMapa(warning.id)}
            className="rounded-full px-2.5 py-1 text-[10.5px] font-semibold ring-1 ring-current/30 hover:bg-current/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          >
            Ver en el mapa
          </button>
          <p className="text-[10px] opacity-80">
            Pronóstico de la Dirección Meteorológica de Chile
            {warning.codigo ? ` (${warning.codigo})` : ''}. No es una alerta de SENAPRED.{' '}
            <a
              href={warning.url}
              target="_blank"
              rel="noopener noreferrer"
              className="font-semibold underline underline-offset-2"
            >
              Ver en meteochile.gob.cl
            </a>
          </p>
        </div>
      )}
    </li>
  )
})

export function MeteoWarningsView({ status, items }: MeteoWarningsState) {
  const bodyId = useId()
  const [open, toggle] = useSectionOpen('avisos-meteo', true)

  if (status === 'blind') {
    return (
      <p className="callout callout-warn text-[10.5px]">
        Sin avisos meteorológicos, pero la lectura de la DMC no está respondiendo.
      </p>
    )
  }
  if (status !== 'ready') return null

  return (
    <section aria-label="Avisos meteorológicos" className="rounded-control bg-sunken p-1.5">
      <SectionToggle
        label="Avisos meteorológicos · DMC"
        count={items.length}
        open={open}
        onToggle={toggle}
        controls={bodyId}
        preview={items[0] ? `${items[0].tipo}: ${phenomenonLabel(items[0])}` : undefined}
      />
      {open && (
        <ul id={bodyId} className="mt-1 space-y-1">
          {items.map((w) => (
            <WarningCard key={w.id} warning={w} />
          ))}
        </ul>
      )}
    </section>
  )
}

/** La franja con su consulta. La monta la pestaña «Ahora». */
export const MeteoWarnings = memo(function MeteoWarnings() {
  const now = useNow(TICK_MS)
  const state = useMeteoWarnings(now)
  return <MeteoWarningsView {...state} />
})
