import { useEffect } from 'react'
import type { Incident } from '@/api/types'
import { LINK_METHOD_LABEL, STATUS_LABEL, TYPE_LABEL, sourceLabel } from '@/domain/labels'
import {
  LEVEL,
  MUTED_LEVEL,
  isClosed,
  levelOf,
  needsVerificationCaveat,
} from '@/domain/symbology'
import { useIncidentDetail } from '@/hooks/useIncidentDetail'
import { formatDateTime, formatDistance, formatRelative } from '@/lib/format'
import { AlertBadge } from './AlertBadge'
import { ConfidenceAudit } from './ConfidenceAudit'
import { ConfidenceBar } from './ConfidenceBar'
import { SourceChips } from './SourceChips'

interface IncidentSheetProps {
  incident: Incident
  onClose: () => void
}

/**
 * Ficha del incidente. BottomSheet en teléfono, panel lateral desde `md`.
 *
 * No es modal a propósito: el mapa sigue siendo utilizable detras. En una
 * emergencia, tapar el contexto geografico para mostrar un detalle es lo
 * contrario de lo que hace falta.
 */
export function IncidentSheet({ incident, onClose }: IncidentSheetProps) {
  // El listado ya trae todo lo que se muestra arriba; el detalle solo agrega la
  // traza de señales, así que la tarjeta se pinta al instante y las señales
  // aparecen cuando llegan.
  const { data: detail, isLoading: loadingDetail } = useIncidentDetail(incident.code)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const level = levelOf(incident)
  const style = LEVEL[level]
  const closed = isClosed(incident.status)
  // El color de la ficha sigue exactamente al del pin, atenuado incluido.
  const levelColor = closed ? MUTED_LEVEL[level] : style.color
  const unverified = needsVerificationCaveat(incident)
  const events = detail?.events ?? []

  return (
    <section
      role="dialog"
      aria-label={`Incidente ${incident.code}`}
      className="
        pointer-events-auto fixed inset-x-0 bottom-0 z-20 flex max-h-[78dvh] flex-col
        rounded-t-2xl bg-white shadow-[0_-8px_40px_rgba(15,23,42,0.25)]
        md:inset-y-0 md:left-auto md:right-0 md:max-h-none md:w-[26rem] md:rounded-none
        md:rounded-l-2xl md:shadow-[-8px_0_40px_rgba(15,23,42,0.18)]
      "
    >
      {/* Asa de arrastre: señal visual de que la tarjeta es una hoja inferior. */}
      <div aria-hidden className="mx-auto mt-2 h-1 w-10 rounded-full bg-slate-300 md:hidden" />

      <header className="flex items-start gap-3 px-4 pb-3 pt-3">
        <span
          aria-hidden
          className="mt-1 size-3.5 shrink-0 rounded-full ring-2 ring-white"
          style={{ backgroundColor: levelColor }}
        />
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-base font-bold text-slate-900">
            {incident.title ?? TYPE_LABEL[incident.type]}
          </h2>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-500">
            <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-slate-700">
              {incident.code}
            </code>
            <span>{TYPE_LABEL[incident.type]}</span>
            <span aria-hidden>·</span>
            <span>{STATUS_LABEL[incident.status]}</span>
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Cerrar ficha del incidente"
          className="-mr-1 -mt-1 grid size-9 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
        >
          <span aria-hidden className="text-lg leading-none">✕</span>
        </button>
      </header>

      <div className="flex-1 overflow-y-auto overscroll-contain px-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`rounded-full px-2.5 py-1 text-xs font-semibold ${style.chip}`}
          >
            {style.label}
          </span>
          {closed && (
            <span className="rounded-full bg-slate-200 px-2.5 py-1 text-xs font-medium text-slate-700">
              {STATUS_LABEL[incident.status]}
            </span>
          )}
          <AlertBadge level={incident.alert_level} />
          {incident.is_multi_source && (
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700 ring-1 ring-slate-200">
              {incident.source_count} fuentes independientes
            </span>
          )}
        </div>

        {unverified && (
          <p className="mt-3 rounded-lg bg-amber-50 px-2.5 py-2 text-[11px] leading-snug text-amber-900 ring-1 ring-amber-200">
            <strong>Nivel confirmado por acumulación de evidencia.</strong> Ninguna
            fuente lo verificó en terreno: ni CONAF ni Bomberos han reportado
            haber llegado al lugar.
          </p>
        )}

        {(incident.commune || incident.province) && (
          <p className="mt-3 text-sm text-slate-700">
            {[incident.commune, incident.province].filter(Boolean).join(', ')}
          </p>
        )}

        {/* --- Los dos ejes de confianza, separados y rotulados --------------- */}
        <div className="mt-4 space-y-4 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-200">
          <ConfidenceBar
            label="Confianza del hecho"
            value={incident.confidence}
            color={levelColor}
            emphasis={`${style.label} · ${style.range}`}
            caption={
              incident.is_official_confirmed
                ? 'CONAF o Bomberos confirmaron el hecho en terreno.'
                : `${style.meaning} Ninguna fuente lo verificó en terreno.`
            }
          />
          <ConfidenceBar
            label="Estado de alerta"
            value={incident.alert_confidence}
            color={incident.alert_level ? '#3b82f6' : '#94a3b8'}
            caption={
              incident.alert_level
                ? 'Hay una alerta de SENAPRED vigente asociada a este incidente.'
                : 'Sin alerta oficial vigente asociada. Es un eje distinto de la confianza del hecho: su ausencia no desmiente el incidente.'
            }
          />
        </div>

        {/* --- Fuentes ------------------------------------------------------- */}
        <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Fuentes ({incident.source_count})
        </h3>
        <div className="mt-2">
          <SourceChips sources={incident.sources} />
        </div>

        {/* --- Línea de tiempo ----------------------------------------------- */}
        <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Línea de tiempo
        </h3>
        <dl className="mt-2 space-y-1.5 text-sm">
          <div className="flex justify-between gap-3">
            <dt className="text-slate-500">Primera señal</dt>
            <dd className="text-right text-slate-800">
              {formatDateTime(incident.first_seen_at)}
              <span className="ml-1.5 text-xs text-slate-500">
                ({formatRelative(incident.first_seen_at)})
              </span>
            </dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="text-slate-500">Última señal</dt>
            <dd className="text-right text-slate-800">
              {formatDateTime(incident.last_seen_at)}
              <span className="ml-1.5 text-xs text-slate-500">
                ({formatRelative(incident.last_seen_at)})
              </span>
            </dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="text-slate-500">Señales totales</dt>
            <dd className="text-slate-800">{incident.event_count}</dd>
          </div>
          {incident.resolved_at && (
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">Resuelto</dt>
              <dd className="text-slate-800">{formatDateTime(incident.resolved_at)}</dd>
            </div>
          )}
        </dl>

        {/* --- Auditoria del número ------------------------------------------ */}
        <div className="mt-5">
          <ConfidenceAudit breakdown={incident.confidence_breakdown} />
        </div>

        {/* --- Señales que lo componen --------------------------------------- */}
        <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Señales correlacionadas
        </h3>
        {loadingDetail && events.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">Cargando señales…</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {events.map((event) => (
              <li
                key={event.raw_event_id}
                className="rounded-lg bg-white p-2.5 text-xs ring-1 ring-slate-200"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-semibold text-slate-800">
                    {sourceLabel(event.source)}
                  </span>
                  <span className="shrink-0 text-slate-500">
                    {formatRelative(event.timestamp)}
                  </span>
                </div>
                {event.text && (
                  <p className="mt-1 line-clamp-3 text-slate-600">{event.text}</p>
                )}
                <p className="mt-1 text-[11px] text-slate-400">
                  {LINK_METHOD_LABEL[event.link_method]}
                  {event.distance_m !== null && ` · a ${formatDistance(event.distance_m)}`}
                  {event.matched_commune && ` · ${event.matched_commune}`}
                </p>
              </li>
            ))}
          </ul>
        )}

        <p className="mt-5 rounded-lg bg-slate-50 p-2.5 text-[11px] leading-snug text-slate-500 ring-1 ring-slate-200">
          AlertaV correlaciona fuentes públicas. Una detección satelital o un
          reporte ciudadano no equivalen a un incendio confirmado. Ante una
          emergencia, llama al <strong className="text-slate-700">132</strong>.
        </p>
      </div>
    </section>
  )
}
