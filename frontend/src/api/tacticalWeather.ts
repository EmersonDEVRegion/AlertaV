/**
 * Cliente del estado meteorológico táctico.
 *
 * `GET /api/v1/events/weather/tactical` → un objeto con la peor amenaza vigente
 * de la región, la cifra que la disparó y el ambiente para el estado silencioso.
 *
 * # Por qué se valida campo por campo si el backend es nuestro
 *
 * Por lo mismo que `api/rain.ts`, y acá pesa más: este objeto alimenta la barra
 * superior, que está siempre en pantalla. Un `undefined` que llegue a
 * `valor.toFixed(0)` no degrada una capa opcional — revienta el cromo de la
 * aplicación entera y deja al usuario mirando una pantalla en blanco durante una
 * emergencia. El coste de comprobar quince campos una vez cada diez minutos es
 * ninguno; el de no hacerlo es la app.
 *
 * # El estado desconocido es un valor, no una excepción
 *
 * `UNKNOWN_WEATHER` es la respuesta a "no se pudo leer": mismo objeto, con
 * `observado_en: null`. El widget ya sabe dibujar eso —apagado, con un guion— y
 * es exactamente lo mismo que dibuja cuando el backend responde bien pero no
 * tiene ninguna corrida reciente. Los dos casos significan lo mismo para quien
 * mira: no sabemos qué tiempo hace.
 *
 * Lo que **no** puede ocurrir es que ninguno de esos dos casos se confunda con
 * la calma. Ver la nota 3 de `tacticalWeatherTypes.ts`.
 */

import { apiGet } from './client'
import type {
  TacticalWeather,
  WeatherHazard,
  WeatherSeverity,
  WeatherTrigger,
} from './tacticalWeatherTypes'

const SEVERITIES: readonly WeatherSeverity[] = ['ninguna', 'aviso', 'critica']
const HAZARDS: readonly WeatherHazard[] = [
  'lluvia',
  'remocion',
  'incendio',
  'viento',
  'calor',
  'uv',
]

/**
 * El estado cuando no se sabe nada.
 *
 * Referencia estable y congelada, por el mismo motivo que `EMPTY_RAIN`: el store
 * la devuelve como instantáneo mientras no haya datos, y `useSyncExternalStore`
 * vuelve a renderizar cada vez que `getSnapshot()` devuelve una identidad nueva.
 * Un literal fresco en cada lectura dejaría el widget repintándose en bucle.
 */
export const UNKNOWN_WEATHER: TacticalWeather = Object.freeze({
  observado_en: null,
  inicio: null,
  fin: null,
  severidad: 'ninguna' as const,
  amenaza: null,
  disparo_principal: null,
  comuna_origen: null,
  temp_c: null,
  viento_kmh: null,
  temp_max_c: null,
  temp_min_c: null,
  humedad_min: null,
  rafaga_max_kmh: null,
  uv_max: null,
  comunas: 0,
  con_lluvia: 0,
  en_aviso: 0,
  en_critico: 0,
  comunas_en_alerta: Object.freeze([]) as unknown as string[],
  modelo: 'desconocido',
  es_pronostico: true,
})

function toNumberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function toCount(value: unknown): number {
  const parsed = toNumberOrNull(value)
  return parsed === null || parsed < 0 ? 0 : Math.trunc(parsed)
}

function toText(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function toTextOrNull(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null
}

function toSeverity(value: unknown): WeatherSeverity {
  return SEVERITIES.includes(value as WeatherSeverity)
    ? (value as WeatherSeverity)
    : // Una severidad desconocida se lee como calma **a propósito**. La
      // alternativa —tratarla como crítica «por si acaso»— pondría la barra en
      // rojo permanente el día que el backend añada un nivel, y una alerta que
      // no se apaga nunca deja de ser una alerta. Si de verdad hay peligro, el
      // disparo principal seguirá ahí y su propia severidad lo dirá.
      'ninguna'
}

function toHazard(value: unknown): WeatherHazard | null {
  return HAZARDS.includes(value as WeatherHazard) ? (value as WeatherHazard) : null
}

/**
 * Un disparo utilizable, o `null`.
 *
 * Es el campo más delicado del contrato porque es el único que el widget
 * renderiza **campo por campo**: el valor en 28 px, la unidad al lado, el
 * umbral debajo. Si `valor` no es un número, no hay nada que expandir y es
 * preferible caer al texto genérico de la severidad que pintar `NaN °C` en la
 * barra superior.
 */
export function parseTrigger(raw: unknown): WeatherTrigger | null {
  if (typeof raw !== 'object' || raw === null) return null

  const source = raw as Record<string, unknown>
  const amenaza = toHazard(source['amenaza'])
  const valor = toNumberOrNull(source['valor'])
  const umbral = toNumberOrNull(source['umbral'])
  const severidad = toSeverity(source['severidad'])

  if (amenaza === null || valor === null || umbral === null) return null
  if (severidad === 'ninguna') return null

  return {
    amenaza,
    severidad,
    metrica: toText(source['metrica'], 'desconocida'),
    valor,
    unidad: toText(source['unidad']),
    umbral,
    texto: toText(source['texto']),
    momento: toTextOrNull(source['momento']),
  }
}

function toNames(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string')
}

/**
 * Valida y normaliza la respuesta.
 *
 * Exportada para poder probarla sin red: es donde vive el riesgo de que el
 * contrato se mueva.
 *
 * # La coherencia que se repara acá
 *
 * Una `severidad` distinta de `ninguna` sin `disparo_principal` es un estado
 * imposible por construcción del backend —la severidad SALE del disparo— pero
 * si llegara, el widget se pintaría de rojo sin poder decir por qué. Se degrada
 * a calma y se avisa por consola: una barra roja que no explica nada es peor
 * que una barra gris.
 */
export function parseTacticalWeather(payload: unknown): TacticalWeather {
  if (typeof payload !== 'object' || payload === null) return UNKNOWN_WEATHER

  const source = payload as Record<string, unknown>
  const disparo = parseTrigger(source['disparo_principal'])
  let severidad = toSeverity(source['severidad'])

  if (severidad !== 'ninguna' && disparo === null) {
    console.warn(
      `[AlertaV/meteo] llegó severidad "${severidad}" sin un disparo principal legible. ` +
        'El widget no tendría qué expandir, así que se degrada a calma. ' +
        'Revisa `TacticalWeatherRead.disparo_principal` en el backend.',
    )
    severidad = 'ninguna'
  }

  return {
    observado_en: toTextOrNull(source['observado_en']),
    inicio: toTextOrNull(source['inicio']),
    fin: toTextOrNull(source['fin']),
    severidad,
    amenaza: disparo?.amenaza ?? toHazard(source['amenaza']),
    disparo_principal: disparo,
    comuna_origen: toTextOrNull(source['comuna_origen']),
    temp_c: toNumberOrNull(source['temp_c']),
    viento_kmh: toNumberOrNull(source['viento_kmh']),
    temp_max_c: toNumberOrNull(source['temp_max_c']),
    temp_min_c: toNumberOrNull(source['temp_min_c']),
    humedad_min: toNumberOrNull(source['humedad_min']),
    rafaga_max_kmh: toNumberOrNull(source['rafaga_max_kmh']),
    uv_max: toNumberOrNull(source['uv_max']),
    comunas: toCount(source['comunas']),
    con_lluvia: toCount(source['con_lluvia']),
    en_aviso: toCount(source['en_aviso']),
    en_critico: toCount(source['en_critico']),
    comunas_en_alerta: toNames(source['comunas_en_alerta']),
    modelo: toText(source['modelo'], 'desconocido'),
    es_pronostico: source['es_pronostico'] !== false,
  }
}

/** `GET /api/v1/events/weather/tactical` */
export async function fetchTacticalWeather(
  signal?: AbortSignal,
): Promise<TacticalWeather> {
  // Sin parámetros: `hours=3` (el defecto) es la holgura correcta y estirarla
  // haría que el widget pueda mostrar como «ahora» una temperatura de hace medio
  // día. Una URL fija además mantiene estable la clave de caché del navegador.
  const payload = await apiGet<unknown>('/events/weather/tactical', signal)
  return parseTacticalWeather(payload)
}
