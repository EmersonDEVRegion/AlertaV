import type { HealthStatus } from '@/api/health'

/**
 * La marca que separa «no pasó nada» de «no llegó nada».
 *
 * Una fuente puede quedar ciega —un Actor de Apify detenido, un portal
 * congelado— mientras un choque ocurre. `collector_runs` tiene el diagnóstico
 * escrito, pero sin esta marca el mapa mostraría «Accidentes viales · 0»,
 * indistinguible de una tarde tranquila.
 *
 * # Sólo aparece cuando cambia la lectura
 *
 * Una capa ciega **con incidentes** no lleva marca. Suena raro y es a propósito:
 * el aviso no dice «esta fuente tiene un problema», dice «no leas este cero como
 * calma». Con incidentes en pantalla no hay cero que malinterpretar, y el
 * operador ya tiene qué mirar; poner un ícono ahí sería ruido compitiendo con
 * una emergencia real.
 *
 * Ese recorte es lo que mantiene la marca creíble. Un canal que grita siempre
 * deja de comunicar, que es exactamente cómo `partial` dejó de servir como
 * señal de salud en este proyecto.
 */

const TEXTO: Record<Exclude<HealthStatus, 'ok'>, { corto: string; largo: string }> = {
  degraded: {
    corto: 'Sin datos',
    largo:
      'Esta capa está recibiendo información antigua: la fuente corrió pero lo ' +
      'que entregó no describe el presente. El cero no significa que no haya ' +
      'ocurrido nada.',
  },
  failing: {
    corto: 'Sin datos',
    largo:
      'La última lectura de esta capa falló. El cero no significa que no haya ' +
      'ocurrido nada.',
  },
  stale: {
    corto: 'Sin datos',
    largo:
      'Hace demasiado que esta capa no recibe una lectura nueva. El cero no ' +
      'significa que no haya ocurrido nada.',
  },
  never: {
    corto: 'Sin datos',
    largo: 'Esta capa todavía no ha registrado ninguna lectura.',
  },
}

/**
 * ¿Este contador necesita la aclaración?
 *
 * Se declara aparte del componente porque es la regla entera de esta
 * funcionalidad —cuándo un cero miente— y merece nombre, prueba y una sola
 * definición. Las tres condiciones son igual de importantes:
 *
 * 1. `undefined` es «no sé». No saber si una capa ve no autoriza a afirmar que
 *    está ciega: si la consulta de salud falla, la interfaz se comporta como
 *    antes de que esta funcionalidad existiera.
 * 2. `ok` es el caso mayoritario —un día tranquilo de verdad— y marcarlo sería
 *    el canal que grita siempre.
 * 3. Con incidentes en pantalla no hay cero que malinterpretar.
 */
export function shouldWarn(status: HealthStatus | undefined, count: number): boolean {
  if (status === undefined) return false
  if (status === 'ok') return false
  if (count > 0) return false
  return true
}

interface LayerHealthProps {
  status: HealthStatus | undefined
  /** Incidentes visibles de la capa. Con alguno, no hay cero que aclarar. */
  count: number
  /** Motivo textual de la fuente, si lo hay. Se muestra en el `title`. */
  detail?: string | null
}

export function LayerHealth({ status, count, detail }: LayerHealthProps) {
  if (!shouldWarn(status, count)) return null

  const texto = TEXTO[status as Exclude<HealthStatus, 'ok'>]
  // El motivo de la fuente va al final: es lo más técnico y lo que menos
  // le sirve a quien sólo necesita saber que no puede confiar en el cero.
  const title = detail ? `${texto.largo}\n\n${detail}` : texto.largo

  return (
    <span
      // Sin píldora: va en la línea de estado del azulejo de la capa, donde el
      // ámbar del texto ya basta para separarlo del resto.
      className="inline-flex shrink-0 items-center gap-1 text-[10px] font-medium text-warn-ink"
      title={title}
    >
      <WarnGlyph />
      {texto.corto}
    </span>
  )
}

function WarnGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className="size-3" fill="currentColor">
      <path d="M12 2.6a1.5 1.5 0 0 1 1.3.75l8.4 14.55a1.5 1.5 0 0 1-1.3 2.25H3.6a1.5 1.5 0 0 1-1.3-2.25l8.4-14.55A1.5 1.5 0 0 1 12 2.6zm-1.3 5.9v5.4h2.6V8.5zm0 7.1v2.6h2.6v-2.6z" />
    </svg>
  )
}
