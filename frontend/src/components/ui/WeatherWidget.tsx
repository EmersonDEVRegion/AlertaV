import { useEffect, useRef } from 'react'
import type { TacticalWeather, WeatherTrigger } from '@/api/tacticalWeatherTypes'
import {
  HAZARD_LABEL,
  HAZARD_SHORT,
  SEVERITY_STYLE,
  WEATHER_GLYPHS,
  WEATHER_TEXT,
  formatMetric,
  splitMetric,
} from '@/domain/tacticalWeatherSymbology'
import type { WeatherGlyphKey } from '@/domain/tacticalWeatherSymbology'
import { Switch } from '@/components/ui/primitives'
import {
  closeWeatherDetail,
  toggleRainLayer,
  toggleWeatherDetail,
  useTacticalWeather,
} from '@/lib/tacticalWeatherStore'
import type { WeatherSnapshot } from '@/lib/tacticalWeatherStore'
import { cn } from '@/lib/cn'

/**
 * Widget meteorológico táctico de la barra superior.
 *
 * ===========================================================================
 * LOS TRES ESTADOS, Y POR QUÉ SON TRES Y NO DOS
 * ===========================================================================
 *
 * 1. **Silencioso** (`severidad: "ninguna"`). El 95 % de los días del año.
 *    Glifo, temperatura y viento en el mismo gris que la telemetría de al lado,
 *    sin fondo y sin borde. No compite con nada porque no tiene nada que decir.
 *
 * 2. **Alerta** (`aviso` o `critica`). Ámbar o rojo, con fondo, y **la métrica
 *    culpable crece**: el número que cruzó el umbral pasa a 15 px y el ambiente
 *    desaparece. En la barra ya no cabe «21 °C, 12 km/h» y una alerta; cuando
 *    hay peligro, el ambiente deja de ser la noticia.
 *
 * 3. **Desconocido** (`observado_en: null`). Apagado, con un guion.
 *
 * **El tercero es el que no se puede omitir.** «No hay dato» y «no pasa nada»
 * se ven casi igual y significan lo contrario: si el collector lleva seis horas
 * caído durante un temporal, un widget que muestre la calma estará afirmando
 * algo que no sabe. Por eso el estado desconocido no reutiliza el diseño
 * silencioso: se apaga y dice explícitamente que no sabe.
 *
 * ===========================================================================
 * POR QUÉ ES UN BOTÓN Y NO SÓLO UNA ETIQUETA
 * ===========================================================================
 *
 * Porque absorbió el control de la capa de lluvia. La tarjeta aislada que vivía
 * en el riel de referencia desapareció: tenía su propio título, su propio
 * subtítulo de estado y su propia leyenda para decir lo mismo que este widget
 * dice en 180 px, y en teléfono había que abrir una ficha para llegar a ella.
 *
 * El interruptor no se puso en la cara del widget —un interruptor en la barra
 * superior invita a tocarlo por error con el pulgar— sino dentro del detalle
 * que se despliega al tocarlo. Tocar el widget nunca cambia el mapa; tocar el
 * interruptor sí.
 */

/* ------------------------------------------------------------------------- */
/* Glifo                                                                      */
/* ------------------------------------------------------------------------- */

/**
 * Dibuja un glifo del registro compartido.
 *
 * Los caminos salen de `WEATHER_GLYPHS`, el mismo formato plano que
 * `emergencyIcons.ICON_GLYPHS` usa para alimentar el generador de campos de
 * distancia con signo de MapLibre. Acá se montan en el DOM y allá se rasterizan
 * a un canvas: una sola geometría, dos destinos.
 */
function Glyph({ name, className }: { name: WeatherGlyphKey; className?: string }) {
  const glyph = WEATHER_GLYPHS[name]
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className ?? 'size-4 shrink-0'}
    >
      {glyph.paths.map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  )
}

/* ------------------------------------------------------------------------- */
/* Cápsula                                                                    */
/* ------------------------------------------------------------------------- */

function glyphFor(data: TacticalWeather): WeatherGlyphKey {
  return data.amenaza ?? 'calma'
}

/** ¿Hay una lectura utilizable? Ver la nota de los tres estados. */
function isKnown(snapshot: WeatherSnapshot): boolean {
  return snapshot.data.observado_en !== null
}

/**
 * Cuerpo de la cápsula en calma: temperatura y viento.
 *
 * Los dos números vienen de MEDIANAS regionales, no de máximos. Es la
 * diferencia entre «el ambiente de la región ahora» y «el peor punto de la
 * ventana», y confundirlas haría que un día con 38 °C en Petorca y 17 °C en la
 * costa anunciara 38 °C como si fuera el tiempo que hace.
 */
function CalmBody({ data }: { data: TacticalWeather }) {
  const temp = data.temp_c
  const viento = data.viento_kmh

  return (
    <>
      <span className="count text-[12.5px] font-semibold tabular-nums">
        {temp === null ? '—' : `${temp.toFixed(0)}°`}
      </span>
      {viento !== null && (
        <span className="hidden text-[11px] tabular-nums opacity-70 sm:inline">
          {viento.toFixed(0)} km/h
        </span>
      )}
    </>
  )
}

/**
 * Cuerpo en alerta: la métrica responsable, expandida.
 *
 * `splitMetric` separa cifra y unidad para poder darles tamaños distintos: lo
 * que se lee de un vistazo es el número, y la unidad sólo tiene que estar ahí
 * para que el número signifique algo.
 */
function AlertBody({ trigger }: { trigger: WeatherTrigger }) {
  const { value, unit } = splitMetric(trigger.valor, trigger.unidad, trigger.metrica)

  return (
    <>
      <span className="count text-[15px] font-bold leading-none tabular-nums">
        {value}
        {unit && <span className="ml-0.5 text-[10px] font-semibold opacity-80">{unit}</span>}
      </span>
      <span className="hidden truncate text-[11px] font-medium sm:inline">
        {HAZARD_SHORT[trigger.amenaza]}
      </span>
    </>
  )
}

/* ------------------------------------------------------------------------- */
/* Detalle                                                                    */
/* ------------------------------------------------------------------------- */

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="text-[11px] text-ink-muted">{label}</span>
      <span className="count text-[11.5px] font-semibold tabular-nums text-ink">{value}</span>
    </div>
  )
}

function Detail({ snapshot }: { snapshot: WeatherSnapshot }) {
  const { data, status } = snapshot
  const trigger = data.disparo_principal
  const known = isKnown(snapshot)
  const style = SEVERITY_STYLE[data.severidad]

  return (
    <div className="w-[17rem] max-w-[calc(100vw-1.5rem)] p-3">
      <div className="flex items-start gap-2">
        <span
          aria-hidden
          className="grid size-8 shrink-0 place-items-center rounded-control"
          style={{
            backgroundColor: style.background ?? 'var(--surface-sunken)',
            color: known && data.severidad !== 'ninguna' ? style.ink : 'var(--ink-faint)',
          }}
        >
          <Glyph name={glyphFor(data)} className="size-[18px]" />
        </span>

        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold leading-tight text-ink">
            {!known
              ? WEATHER_TEXT.unknown
              : trigger
                ? HAZARD_LABEL[trigger.amenaza]
                : WEATHER_TEXT.calm}
          </p>
          <p className="mt-0.5 text-[10.5px] leading-tight text-ink-muted">
            {!known
              ? WEATHER_TEXT.unknownDetail
              : trigger
                ? // El texto lo redacta el backend, que es quien conoce el
                  // umbral vigente. Reescribirlo acá sería tener la política en
                  // dos sitios.
                  `${trigger.texto}${data.comuna_origen ? ` — ${data.comuna_origen}` : ''}`
                : `${data.comunas} comunas evaluadas y ninguna cruza un umbral.`}
          </p>
        </div>
      </div>

      {known && (
        <>
          <div className="mt-2.5 border-t border-line pt-1.5">
            {data.temp_c !== null && (
              <Row label="Temperatura (mediana)" value={`${data.temp_c.toFixed(0)} °C`} />
            )}
            {data.temp_max_c !== null && (
              <Row label="Máxima regional" value={`${data.temp_max_c.toFixed(0)} °C`} />
            )}
            {data.humedad_min !== null && (
              <Row label="Humedad mínima" value={`${data.humedad_min.toFixed(0)} %`} />
            )}
            {data.rafaga_max_kmh !== null && (
              <Row label="Ráfaga máxima" value={`${data.rafaga_max_kmh.toFixed(0)} km/h`} />
            )}
            {data.uv_max !== null && (
              <Row label="Índice UV máximo" value={formatMetric(data.uv_max, 'uv_max')} />
            )}
            {data.con_lluvia > 0 && (
              <Row label="Comunas con lluvia" value={String(data.con_lluvia)} />
            )}
          </div>

          {data.comunas_en_alerta.length > 0 && (
            <p className="mt-1.5 text-[10.5px] leading-snug text-ink-muted">
              <span className="font-semibold text-ink">En alerta: </span>
              {/* Se recortan a seis: la lista completa puede ser 36 nombres y
                  este panel no es un listado, es un contexto. */}
              {data.comunas_en_alerta.slice(0, 6).join(', ')}
              {data.comunas_en_alerta.length > 6 &&
                ` y ${data.comunas_en_alerta.length - 6} más`}
            </p>
          )}
        </>
      )}

      {/*
        El interruptor de la capa de lluvia. Vive acá y no en la cara del widget
        porque un interruptor en la barra superior se toca por error con el
        pulgar, y lo que hace es encender una capa del mapa.
      */}
      <div className="mt-2.5 flex items-center gap-2.5 border-t border-line pt-2.5">
        <span className="min-w-0 flex-1">
          <span className="block text-[11px] font-semibold text-ink">
            {WEATHER_TEXT.layerToggle}
          </span>
          <span className="block truncate text-[10px] leading-tight text-ink-muted">
            {WEATHER_TEXT.layerHint}
          </span>
        </span>
        <Switch
          checked={snapshot.rainLayer}
          onCheckedChange={toggleRainLayer}
          label={WEATHER_TEXT.layerToggle}
          accentColor="#38bdf8"
        />
      </div>

      {/*
        Capa encendida y cero milímetros pronosticados en toda la región.

        Sin este aviso, encender el interruptor no produce ningún cambio visible
        y el mapa vacío admite dos lecturas opuestas —«no va a llover» y «la capa
        no cargó»—. La condición es `con_lluvia === 0` y no una comparación de
        milímetros porque el backend devuelve UNA FILA POR COMUNA CON LLUVIA:
        cero comunas es exactamente el conjunto que la capa va a dibujar.

        `known &&` es la mitad importante de la condición. Con `observado_en:
        null` el collector está caído y no hay pronóstico ninguno; afirmar ahí
        que no lloverá sería el error que la nota de los tres estados existe para
        evitar. En ese caso el widget ya dice que no sabe, arriba.
      */}
      {known && snapshot.rainLayer && data.con_lluvia === 0 && (
        <p
          role="status"
          className="mt-1.5 text-[10.5px] leading-snug text-ink-faint"
        >
          {WEATHER_TEXT.layerEmpty}
        </p>
      )}

      <p className="mt-2 text-[9.5px] leading-snug text-ink-faint">
        {WEATHER_TEXT.caveat}
        {status === 'error' && ' · No se pudo actualizar; el dato puede estar viejo.'}
      </p>
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* Widget                                                                     */
/* ------------------------------------------------------------------------- */

export function WeatherWidget() {
  const snapshot = useTacticalWeather()
  const { data } = snapshot
  const known = isKnown(snapshot)
  const trigger = data.disparo_principal
  const alerting = known && data.severidad !== 'ninguna' && trigger !== null
  const style = SEVERITY_STYLE[known ? data.severidad : 'ninguna']

  const root = useRef<HTMLDivElement>(null)

  /*
   * Cierre por clic fuera y por Escape.
   *
   * El detalle es un popover anclado a la barra, no un diálogo: no atrapa el
   * foco y no bloquea el mapa detrás, porque cerrarlo para poder arrastrar el
   * mapa sería un gesto de más en la única pantalla que importa. A cambio, tiene
   * que cerrarse solo en cuanto la atención se va a otra parte.
   *
   * `pointerdown` y no `click`: el usuario que empieza a arrastrar el mapa ya
   * decidió irse, y esperar al `click` —que en un arrastre nunca llega— dejaría
   * el panel abierto sobre el gesto.
   */
  useEffect(() => {
    if (!snapshot.expanded) return

    const onPointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) closeWeatherDetail()
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeWeatherDetail()
    }

    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [snapshot.expanded])

  const resumen = !known
    ? WEATHER_TEXT.unknown
    : trigger
      ? `${HAZARD_LABEL[trigger.amenaza]}: ${formatMetric(trigger.valor, trigger.metrica)} ${trigger.unidad}`
      : WEATHER_TEXT.calm

  return (
    <div ref={root} className="relative shrink-0">
      <button
        type="button"
        onClick={toggleWeatherDetail}
        aria-expanded={snapshot.expanded}
        // Empieza por «Estado meteorológico», el rótulo que el widget muestra
        // en su detalle, por WCAG 2.5.3: quien navega por voz dice lo que ve.
        aria-label={`${WEATHER_TEXT.title}. ${resumen}`}
        className={cn(
          'flex items-center gap-1.5 rounded-full px-2 py-1 leading-none',
          // La transición cubre las tres propiedades que cambian entre estados.
          // Sin ella, pasar de calma a rojo sería un parpadeo en una barra que
          // se mira de reojo — justo el cambio que tiene que percibirse.
          'transition-[background-color,color,box-shadow] duration-300',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1',
          'focus-visible:outline-orange-400',
          !alerting && 'hover:bg-chrome-raised',
          // El pulso SÓLO en crítico, y sólo cuando de verdad hay algo. Un
          // ámbar que late convierte una condición de propagación —que dura
          // toda una tarde de verano— en un elemento que no se puede dejar de
          // mirar. `motion-reduce` lo apaga: el color ya carga la señal.
          alerting &&
            data.severidad === 'critica' &&
            'animate-pulse-soft motion-reduce:animate-none',
          !known && 'opacity-55',
        )}
        style={{
          backgroundColor: style.background ?? undefined,
          color: style.ink,
          boxShadow: style.border ? `inset 0 0 0 1px ${style.border}` : undefined,
        }}
      >
        <Glyph name={known ? glyphFor(data) : 'calma'} />

        {!known ? (
          <span className="text-[12.5px] font-semibold">—</span>
        ) : alerting && trigger ? (
          <AlertBody trigger={trigger} />
        ) : (
          <CalmBody data={data} />
        )}
      </button>

      {snapshot.expanded && (
        <div
          role="region"
          aria-label={WEATHER_TEXT.title}
          className={cn(
            'animate-rise absolute right-0 top-[calc(100%+0.5rem)] z-30',
            // `bg-raised` opaco y no `.surface-floating`: esa receta es
            // translúcida con desenfoque y está pensada para posarse sobre el
            // mapa. Este detalle cuelga de la barra oscura, así que la mitad
            // superior compondría contra el cromo casi negro y la inferior
            // contra la cartografía — el mismo texto con dos contrastes.
            // Misma decisión que la ficha de incidente y la de sismo.
            'rounded-surface bg-raised shadow-[var(--shadow-raised)] ring-1 ring-line',
          )}
        >
          <Detail snapshot={snapshot} />
        </div>
      )}
    </div>
  )
}
