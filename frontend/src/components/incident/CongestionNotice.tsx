import type { Congestion } from '@/api/types'
import { formatDateTime } from '@/lib/format'

/**
 * «Espere congestión entre las X y las Y.»
 *
 * Es lo más cerca que AlertaV puede estar de Waze sin la API de Waze, y la
 * distancia importa: Waze **mide** el tráfico, esto lo **estima** desde una
 * tabla de arterias. Todo el componente está construido alrededor de esa
 * diferencia.
 *
 * # Por qué la advertencia no es letra chica
 *
 * Alguien va a decidir si sale de su casa mirando estos dos relojes. Si la
 * estimación falla y la persona se enteró de que era una estimación, el sistema
 * se equivocó; si falla y la persona creía que era una medición, el sistema
 * mintió. Por eso «estimación» va en el cuerpo y no en un asterisco, y por eso
 * la base —qué vía, por qué esa vía, de dónde salió la hora— está a la vista y
 * no escondida en un tooltip.
 *
 * # La hora de publicación degrada el aviso, no lo oculta
 *
 * Cuando la nota no dijo a qué hora ocurrió, la ventana se calcula sobre la
 * hora en que se publicó, que suele ser bastante posterior. Sigue sirviendo
 * —una arteria cortada genera taco igual— pero el margen es peor, y el
 * componente lo dice con esas palabras en vez de callarlo.
 */

const ORIGEN_HORA: Record<Congestion['source_time'], string | null> = {
  exacta: null,
  aproximada: 'La hora del hecho es aproximada, según la fuente.',
  franja: 'La fuente sólo indicó un tramo del día, así que el margen es amplio.',
  publicacion:
    'La fuente no indicó la hora del hecho: se usó la de publicación, que suele ' +
    'ser posterior. La congestión pudo empezar antes.',
}

function hhmm(iso: string): string {
  // `formatDateTime` da fecha y hora; acá sólo interesa el reloj, porque las dos
  // puntas de la ventana caen casi siempre el mismo día y repetir la fecha
  // empuja el dato útil fuera de la línea en pantallas angostas.
  return new Date(iso).toLocaleTimeString('es-CL', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

interface CongestionNoticeProps {
  congestion: Congestion
}

export function CongestionNotice({ congestion }: CongestionNoticeProps) {
  const nota = ORIGEN_HORA[congestion.source_time]
  const cruzaDia =
    new Date(congestion.starts_at).getDate() !== new Date(congestion.ends_at).getDate()

  return (
    <section className="mt-5 rounded-control bg-warn-bg p-3 ring-1 ring-warn-line">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-warn-ink">
        Congestión estimada
      </h3>

      <p className="mt-1.5 text-sm text-ink">
        Espere congestión en <strong>{congestion.road}</strong> entre las{' '}
        <strong>{hhmm(congestion.starts_at)}</strong> y las{' '}
        <strong>{hhmm(congestion.ends_at)}</strong>
        {congestion.peak_hour && ' (hora punta)'}.
      </p>

      {cruzaDia && (
        <p className="mt-1 text-[11px] text-ink-muted">
          {formatDateTime(congestion.starts_at)} → {formatDateTime(congestion.ends_at)}
        </p>
      )}

      <p className="mt-2 text-[11px] leading-snug text-ink-muted">
        <strong>Es una estimación, no una medición.</strong> AlertaV no mide
        tráfico: son {congestion.duration_minutes} minutos de despeje habitual
        para esta vía{congestion.peak_hour && ' en hora punta'}, porque {congestion.basis}.
      </p>

      {nota && <p className="mt-1 text-[11px] leading-snug text-ink-muted">{nota}</p>}
    </section>
  )
}
