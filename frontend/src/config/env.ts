/** Lectura única y tipada de las variables de entorno. */

function num(value: string | undefined, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

/**
 * Envolturas que llegan pegadas al valor cuando se copia desde otro medio.
 * El caso real que motivó esto: `VITE_API_BASE_URL` guardado en Vercel como
 * `[https://alertav-api.onrender.com/api/v1]`. Los corchetes son basura de un
 * enlace en Markdown, pero para el navegador convierten una URL absoluta en una
 * ruta relativa, así que `fetch` la resuelve contra el propio dominio de Vercel
 * y todo termina en 404 sobre `https://<app>.vercel.app/[https:/...]`.
 *
 * Cada par es [apertura, cierre]. Se pelan en capas porque un valor puede traer
 * más de una (comillas dentro de corchetes, por ejemplo).
 */
const WRAPPERS: readonly (readonly [string, string])[] = [
  ['[', ']'],
  ['<', '>'],
  ['"', '"'],
  ["'", "'"],
  ['`', '`'],
  ['(', ')'],
]

/** Quita espacios, envolturas y una coma o punto y coma final. */
function unwrap(raw: string): string {
  let value = raw.trim()

  for (let guard = 0; guard < WRAPPERS.length * 2; guard += 1) {
    const pair = WRAPPERS.find(
      ([open, close]) =>
        value.length >= 2 && value.startsWith(open) && value.endsWith(close),
    )
    if (!pair) break
    value = value.slice(1, -1).trim()
  }

  return value.replace(/[,;]+$/, '')
}

/**
 * Un valor pegado desde Markdown puede llegar como el enlace entero:
 * `[https://a/api/v1](https://a/api/v1)`. Nos quedamos con el destino, que es
 * el que el autor quiso comunicar.
 */
function fromMarkdownLink(raw: string): string | null {
  const match = /^\[[^\]]*\]\(\s*([^\s)]+)\s*\)$/.exec(raw.trim())
  return match?.[1] ?? null
}

/**
 * `https:/host` (una sola barra) es sintácticamente válido para el parser de
 * URL — lo lee como una ruta opaca — pero jamás es lo que alguien quiso
 * escribir. Solo se repara para los esquemas que sabemos que usan autoridad.
 */
function fixSingleSlash(value: string): string {
  return value.replace(/^(https?):\/(?!\/)/i, '$1://')
}

/**
 * Normaliza una URL base de la configuración.
 *
 * Acepta dos formas legítimas: una URL absoluta (`https://api.example.com/api/v1`)
 * o una ruta relativa al mismo origen (`/api/v1`, que en desarrollo atraviesa el
 * proxy de Vite). Cualquier otra cosa es un error de configuración y cae al
 * respaldo, siempre dejando rastro en consola: una variable mal pegada que la
 * app repara en silencio es una bomba de tiempo, no una funcionalidad.
 */
function url(raw: string | undefined, fallback: string, name: string): string {
  if (raw === undefined || raw.trim() === '') return fallback

  const candidate = fixSingleSlash(unwrap(fromMarkdownLink(raw) ?? raw))

  const warn = (reason: string, used: string) => {
    console.warn(
      `[AlertaV/env] ${name} venía mal formada y se ${
        used === fallback ? 'descartó' : 'corrigió'
      }.\n` +
        `  recibido: ${JSON.stringify(raw)}\n` +
        `  usando:   ${JSON.stringify(used)}\n` +
        `  motivo:   ${reason}\n` +
        `  Revisa el valor en el panel de tu proveedor (Vercel → Settings → ` +
        `Environment Variables) y vuelve a desplegar: el valor se hornea en el ` +
        `bundle durante el build, así que cambiarlo exige un redeploy.`,
    )
  }

  // Ruta relativa al mismo origen.
  if (candidate.startsWith('/') && !candidate.startsWith('//')) {
    const normalized = candidate.replace(/\/+$/, '') || '/'
    if (normalized !== raw) warn('sobraban envolturas o espacios', normalized)
    return normalized
  }

  // URL absoluta.
  try {
    const parsed = new URL(candidate)
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      warn(`protocolo no soportado (${parsed.protocol})`, fallback)
      return fallback
    }
    const normalized = parsed.href.replace(/\/+$/, '')
    if (normalized !== raw) warn('sobraban envolturas o espacios', normalized)
    return normalized
  } catch {
    warn('no es una URL absoluta ni una ruta que empiece por "/"', fallback)
    return fallback
  }
}

/**
 * Estilo del mapa base. CARTO Positron no pide API key, así que sirve de
 * respaldo real y no de placeholder: si `VITE_MAP_STYLE` falta o llega rota, el
 * mapa igual se dibuja.
 */
const DEFAULT_MAP_STYLE =
  'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json'

/**
 * Contraparte oscura. Mismo proveedor y mismo esquema de teselas que Positron,
 * así que el cambio entre ambos no vuelve a descargar la geometría: sólo cambia
 * la hoja de estilo.
 */
const DEFAULT_MAP_STYLE_DARK =
  'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'

export const env = {
  apiBaseUrl: url(
    import.meta.env.VITE_API_BASE_URL,
    '/api/v1',
    'VITE_API_BASE_URL',
  ),
  /**
   * El worker de correlación del backend corre cada 120 s. Pedir más seguido
   * que eso gasta batería del teléfono sin traer datos nuevos.
   */
  pollIntervalMs: num(import.meta.env.VITE_POLL_INTERVAL_MS, 60_000),
  /**
   * Cadencia de los sismos. Más lenta: el collector del USGS corre cada 5 min y
   * un sismo no cambia de estado una vez ocurrido.
   */
  seismicPollIntervalMs: num(import.meta.env.VITE_SEISMIC_POLL_INTERVAL_MS, 180_000),
  /**
   * Cadencia de la capa de lluvia. La más lenta de las tres, y con margen: el
   * collector de Open-Meteo corre cada 30 min porque los modelos globales se
   * recalculan cada 3 a 6 horas. Pedirlo cada 5 minutos devolvería la misma
   * foto seis veces.
   */
  rainPollIntervalMs: num(import.meta.env.VITE_RAIN_POLL_INTERVAL_MS, 600_000),
  /**
   * Cadencia del estado meteorológico táctico de la barra superior.
   *
   * La misma que la lluvia y por la misma razón —lo escribe el mismo collector,
   * cada 30 min— pero con su propia variable a propósito: son dos consumidores
   * con vidas distintas. La capa de lluvia sólo consulta cuando alguien la
   * enciende; el widget consulta desde que arranca la aplicación y no se apaga
   * nunca, porque su trabajo es estar ahí. Atarlos a un solo número obligaría a
   * elegir entre encarecer el arranque o volver perezoso el widget.
   */
  weatherPollIntervalMs: num(import.meta.env.VITE_WEATHER_POLL_INTERVAL_MS, 600_000),
  /**
   * Cadencia de la capa de cortes de ruta. La más lenta de todas, y con mucho.
   *
   * El collector del MOP corre cada hora y el propio servicio se actualiza los
   * lunes; el del MTT publica intervenciones programadas, que no cambian de un
   * momento a otro. Pedirlo cada minuto traería la misma foto sesenta veces.
   */
  roadClosurePollIntervalMs: num(
    import.meta.env.VITE_ROAD_CLOSURE_POLL_INTERVAL_MS,
    900_000,
  ),
  /** A partir de aquí la UI avisa que el dato puede no describir el presente. */
  staleAfterMs: num(import.meta.env.VITE_STALE_AFTER_MS, 180_000),
  mapStyle: url(
    import.meta.env.VITE_MAP_STYLE,
    DEFAULT_MAP_STYLE,
    'VITE_MAP_STYLE',
  ),
  mapStyleDark: url(
    import.meta.env.VITE_MAP_STYLE_DARK,
    DEFAULT_MAP_STYLE_DARK,
    'VITE_MAP_STYLE_DARK',
  ),
} as const
