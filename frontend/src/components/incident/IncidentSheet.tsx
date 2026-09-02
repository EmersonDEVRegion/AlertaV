import { useEffect } from 'react'
import type { CurrentWind } from '@/api/weather'
import type { Incident } from '@/api/types'
import type { WindCone } from '@/domain/windCone'
import { PROJECTION_HOURS, compassLabel } from '@/domain/windCone'
import { LINK_METHOD_LABEL, STATUS_LABEL, TYPE_LABEL, sourceLabel } from '@/domain/labels'
import {
  EMERGENCY_CONTACT,
  UNCONFIRMED_NOUN,
  VERIFYING_SOURCES,
  layerOf,
} from '@/domain/families'
import { styleFor } from '@/domain/palette'
import { isClosed, needsVerificationCaveat } from '@/domain/symbology'
import { useIncidentDetail } from '@/hooks/useIncidentDetail'
import { formatDateTime, formatDistance, formatRelative } from '@/lib/format'
import { AlertBadge } from './AlertBadge'
import { ConfidenceAudit } from './ConfidenceAudit'
import { ConfidenceBar } from './ConfidenceBar'
import { CongestionNotice } from './CongestionNotice'
import { OutageDetails } from './OutageDetails'
import { SourceChips } from './SourceChips'

interface IncidentSheetProps {
  incident: Incident
  onClose: () => void
  /** Viento actual en el punto. `null` si no aplica o no llegó. */
  wind?: CurrentWind | null
  windCone?: WindCone | null
  windLoading?: boolean
  windError?: boolean
}

/**
 * Ficha del incidente. BottomSheet en teléfono, panel lateral desde `md`.
 *
 * No es modal a propósito: el mapa sigue siendo utilizable detras. En una
 * emergencia, tapar el contexto geografico para mostrar un detalle es lo
 * contrario de lo que hace falta.
 */
export function IncidentSheet({
  incident,
  onClose,
  wind = null,
  windCone = null,
  windLoading = false,
  windError = false,
}: IncidentSheetProps) {
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

  const layer = layerOf(incident.type)
  // La paleta la decide la familia: un accidente vial no se pinta ni se rotula
  // como un incendio. `styleFor` ya resuelve el atenuado de los cerrados.
  const style = styleFor(incident)
  const closed = isClosed(incident.status)
  const levelColor = style.color
  const unverified = needsVerificationCaveat(incident)
  const contact = EMERGENCY_CONTACT[layer]
  const events = detail?.events ?? []

  return (
    <section
      role="dialog"
      aria-label={`Incidente ${incident.code}`}
      className="pointer-events-auto fixed inset-x-0 bottom-0 z-20 flex max-h-[78dvh] flex-col
        rounded-t-2xl bg-raised shadow-[var(--shadow-raised)]
        md:inset-y-0 md:left-auto md:right-0 md:max-h-none md:w-[26rem] md:rounded-none
        md:rounded-l-2xl md:shadow-[var(--shadow-raised)]
 "
    >
      {/* Asa de arrastre: señal visual de que la tarjeta es una hoja inferior. */}
      <div aria-hidden className="mx-auto mt-2 h-1 w-10 rounded-full bg-line-strong md:hidden" />

      <header className="flex items-start gap-3 px-4 pb-3 pt-3">
        <span
          aria-hidden
          className="mt-1 size-3.5 shrink-0 rounded-full ring-2 ring-white"
          style={{ backgroundColor: levelColor }}
        />
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-base font-bold text-ink">
            {incident.title ?? TYPE_LABEL[incident.type]}
          </h2>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-ink-muted">
            <code className="rounded bg-sunken px-1.5 py-0.5 font-mono text-[11px] font-semibold text-ink-muted">
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
          className="-mr-1 -mt-1 grid size-9 shrink-0 place-items-center rounded-full text-ink-faint transition hover:bg-sunken hover:text-ink-muted"
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
            <span className="rounded-full bg-hover px-2.5 py-1 text-xs font-medium text-ink-muted">
              {STATUS_LABEL[incident.status]}
            </span>
          )}
          <AlertBadge level={incident.alert_level} />
          {incident.is_multi_source && (
            <span className="rounded-full bg-sunken px-2.5 py-1 text-xs font-medium text-ink-muted ring-1 ring-line">
              {incident.source_count} fuentes independientes
            </span>
          )}
        </div>

        {unverified && (
          <p className="mt-3 callout callout-warn">
            <strong>Nivel confirmado por acumulación de evidencia.</strong> Ninguna
            fuente lo verificó en terreno: {VERIFYING_SOURCES[layer].negative}.
          </p>
        )}

        {(incident.commune || incident.province) && (
          <p className="mt-3 text-sm text-ink-muted">
            {[incident.commune, incident.province].filter(Boolean).join(', ')}
          </p>
        )}

        {/* --- Los dos ejes de confianza, separados y rotulados --------------- */}
        <div className="mt-4 space-y-4 rounded-surface bg-sunken p-3 ring-1 ring-line">
          <ConfidenceBar
            label="Confianza del hecho"
            value={incident.confidence}
            color={levelColor}
            emphasis={`${style.label} · ${style.range}`}
            caption={
              incident.is_official_confirmed
                ? VERIFYING_SOURCES[layer].affirmative
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

        {/* --- Corte de suministro ------------------------------------------- */}
        {incident.outage && <OutageDetails outage={incident.outage} />}

        {/* --- Congestión estimada -------------------------------------------
            Va ARRIBA de las fuentes y la línea de tiempo: para alguien que está
            por salir a la calle es lo más accionable de la ficha, y enterrarlo
            bajo la trazabilidad sería ordenar el panel por lo que le interesa
            al que audita en vez de al que va manejando. */}
        {detail?.congestion && <CongestionNotice congestion={detail.congestion} />}

        {/* --- Fuentes ------------------------------------------------------- */}
        <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Fuentes ({incident.source_count})
        </h3>
        <div className="mt-2">
          <SourceChips sources={incident.sources} />
        </div>

        {/* --- Línea de tiempo ----------------------------------------------- */}
        <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Línea de tiempo
        </h3>
        <dl className="mt-2 space-y-1.5 text-sm">
          <div className="flex justify-between gap-3">
            <dt className="text-ink-muted">Primera señal</dt>
            <dd className="text-right text-ink">
              {formatDateTime(incident.first_seen_at)}
              <span className="ml-1.5 text-xs text-ink-muted">
                ({formatRelative(incident.first_seen_at)})
              </span>
            </dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="text-ink-muted">Última señal</dt>
            <dd className="text-right text-ink">
              {formatDateTime(incident.last_seen_at)}
              <span className="ml-1.5 text-xs text-ink-muted">
                ({formatRelative(incident.last_seen_at)})
              </span>
            </dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="text-ink-muted">Señales totales</dt>
            <dd className="text-ink">{incident.event_count}</dd>
          </div>
          {incident.resolved_at && (
            <div className="flex justify-between gap-3">
              <dt className="text-ink-muted">Resuelto</dt>
              <dd className="text-ink">{formatDateTime(incident.resolved_at)}</dd>
            </div>
          )}
        </dl>

        {/* --- Auditoria del número ------------------------------------------ */}
        <div className="mt-5">
          <ConfidenceAudit breakdown={incident.confidence_breakdown} />
        </div>

        {/* --- Señales que lo componen --------------------------------------- */}
        <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Señales correlacionadas
        </h3>
        {loadingDetail && events.length === 0 ? (
          <p className="mt-2 text-sm text-ink-muted">Cargando señales…</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {events.map((event) => (
              <li
                key={event.raw_event_id}
                className="rounded-control bg-raised p-2.5 text-xs ring-1 ring-line"
              >
                <div className="flex items-baseline justify-between gap-2">
                  {/* Quien publico, no la banda: el chip de «Fuentes» ya dice
                      «Prensa», y repetirlo aca gasta la linea sin agregar nada.
                      Cuando la fuente no tiene nombre propio —una distribuidora
                      electrica no es «alguien»— se cae a la banda. */}
                  <span className="font-semibold text-ink">
                    {event.source_label ?? sourceLabel(event.source)}
                  </span>
                  <span className="shrink-0 text-ink-muted">
                    {formatRelative(event.timestamp)}
                  </span>
                </div>
                {event.text &&
                  (event.source_url ? (
                    /* El titular ES el enlace: es lo que la persona quiere
                       tocar, y un «Ver mas» aparte obliga a leer dos veces.
                       `rel="noopener noreferrer"` aunque el backend ya validó
                       el esquema — son cosas distintas: ahi se decide que el
                       destino sea http(s), aca que no pueda tocar
                       `window.opener` ni recibir nuestro Referer. */
                    <a
                      href={event.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-1 block line-clamp-3 text-ink-muted underline
                        decoration-line underline-offset-2 transition-colors
                        hover:text-ink hover:decoration-ink-muted
                        focus-visible:outline-none focus-visible:ring-2
                        focus-visible:ring-accent"
                    >
                      {event.text}
                      <span aria-hidden className="ml-1 text-ink-faint">
                        ↗
                      </span>
                      <span className="sr-only"> (abre en una pestaña nueva)</span>
                    </a>
                  ) : (
                    <p className="mt-1 line-clamp-3 text-ink-muted">{event.text}</p>
                  ))}
                {!event.text && event.source_url && (
                  /* Sin texto, el enlace necesita su propia etiqueta o queda un
                     `<a>` vacio: invisible para el mouse e ilegible para un
                     lector de pantalla. */
                  <a
                    href={event.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-1 inline-block text-ink-muted underline
                      decoration-line underline-offset-2 hover:text-ink"
                  >
                    Ver publicación ↗
                  </a>
                )}
                <p className="mt-1 text-[11px] text-ink-faint">
                  {LINK_METHOD_LABEL[event.link_method]}
                  {event.distance_m !== null && ` · a ${formatDistance(event.distance_m)}`}
                  {event.matched_commune && ` · ${event.matched_commune}`}
                </p>
              </li>
            ))}
          </ul>
        )}

        {/* --- Propagación por viento (sólo incendios) --------------------- */}
        {layer === 'fire' && (windLoading || windError || wind) && (
          <>
            <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-ink-muted">
              Viento y propagación
            </h3>

            {windLoading && (
              <p className="mt-2 text-sm text-ink-muted">Consultando el viento…</p>
            )}

            {windError && (
              <p className="mt-2 text-[11px] leading-snug text-ink-muted">
                No se pudo obtener el viento desde Open-Meteo. El cono de
                propagación no se dibuja.
              </p>
            )}

            {wind && windCone && (
              <>
                <dl className="mt-2 space-y-1.5 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="text-ink-muted">Viento</dt>
                    <dd className="text-ink">
                      {Math.round(wind.windSpeedKmh)} km/h del{' '}
                      {compassLabel(wind.windDirectionDeg)}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-ink-muted">Avance estimado</dt>
                    <dd className="text-ink">
                      {windCone.lengthKm.toFixed(1)} km hacia el{' '}
                      {compassLabel(windCone.bearingDeg)}
                    </dd>
                  </div>
                </dl>
                <p className="mt-2 callout callout-warn">
                  Proyección indicativa a {PROJECTION_HOURS} h suponiendo avance
                  al 10 % de la velocidad del viento en pastizal abierto. No
                  considera combustible ni pendiente, y{' '}
                  <strong>subestima en subida</strong>.
                </p>
              </>
            )}

            {wind && !windCone && (
              <p className="mt-2 text-[11px] leading-snug text-ink-muted">
                Viento en calma: no hay una dirección de propagación que
                proyectar.
              </p>
            )}
          </>
        )}

        <p className="mt-5 rounded-control bg-sunken p-2.5 text-[11px] leading-snug text-ink-muted ring-1 ring-line">
          AlertaV correlaciona fuentes públicas. Una detección satelital o un
          reporte ciudadano no equivalen a {UNCONFIRMED_NOUN[layer]}.{' '}
          {contact ? (
            <>
              Ante una emergencia, llama a {contact.service} al{' '}
              <strong className="text-ink-muted">
                {contact.number}
              </strong>
              {layer === 'traffic' && ', o al 131 si hay personas lesionadas'}.
            </>
          ) : (
            <>Los cortes se reportan directamente a la empresa distribuidora.</>
          )}
        </p>
      </div>
    </section>
  )
}
