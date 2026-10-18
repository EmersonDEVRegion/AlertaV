/**
 * Radios de aviso por categoría. Espejo de `app/services/push/radios.py`.
 *
 * El servidor declara las categorías y sus radios por defecto en
 * `/push/status`; esto es el respaldo para un servidor anterior y la escala del
 * deslizador.
 */

export interface CategoriaAviso {
  clave: string
  etiqueta: string
}

export const CATEGORIAS_RESPALDO: readonly CategoriaAviso[] = [
  { clave: 'fire', etiqueta: 'Incendios' },
  { clave: 'traffic', etiqueta: 'Accidentes' },
  { clave: 'power', etiqueta: 'Cortes de luz' },
  { clave: 'hydro', etiqueta: 'Inundaciones y derrumbes' },
  { clave: 'other', etiqueta: 'Otras emergencias' },
  { clave: 'water', etiqueta: 'Cortes de agua' },
  { clave: 'transito', etiqueta: 'Tránsito' },
]

const RADIOS_RESPALDO: Readonly<Record<string, number>> = {
  fire: 5000,
  traffic: 2000,
  power: 1000,
  hydro: 3000,
  other: 2000,
  water: 1000,
  // Apagado: lo enciende quien lo quiere (`PUSH_RADIO_TRANSITO_M`).
  transito: 0,
}

/**
 * Con cuánto queda una categoría apagada al tocar «Activar». El tránsito parte
 * en 2 km: cerca de donde uno está, que es lo que cambia por dónde ir. Las
 * demás vuelven a su radio por defecto.
 */
const RADIOS_AL_ENCENDER: Readonly<Record<string, number>> = {
  transito: 2000,
}

/** El radio con que se enciende una categoría que estaba en «No avisar». */
export function radioAlEncender(
  clave: string,
  porDefecto: Readonly<Record<string, number>> | undefined,
): number {
  const propio = RADIOS_AL_ENCENDER[clave]
  if (propio) return propio
  const servidor = porDefecto?.[clave] ?? 0
  if (servidor > 0) return servidor
  return RADIOS_RESPALDO[clave] || 2000
}

const RADIO_MINIMO_M = 300
const RADIO_MAXIMO_M = 20_000

/** Posiciones del deslizador: 0 = no avisar; 1…PASOS recorren 300 m a 20 km. */
export const PASOS = 1000

/** Redondeo legible según la escala: 100 m bajo 2 km, 250 m bajo 10 km, 500 m sobre. */
function redondear(metros: number): number {
  const paso = metros < 2000 ? 100 : metros < 10_000 ? 250 : 500
  return Math.min(RADIO_MAXIMO_M, Math.max(RADIO_MINIMO_M, Math.round(metros / paso) * paso))
}

/**
 * Posición → metros, en escala logarítmica: los primeros kilómetros, que son
 * los que importan, ocupan la mayor parte del recorrido.
 */
export function metrosDePosicion(posicion: number): number {
  if (posicion <= 0) return 0
  const t = (Math.min(posicion, PASOS) - 1) / (PASOS - 1)
  const metros = RADIO_MINIMO_M * Math.pow(RADIO_MAXIMO_M / RADIO_MINIMO_M, t)
  return redondear(metros)
}

/** Metros → posición. Inversa de `metrosDePosicion`. */
export function posicionDeMetros(metros: number): number {
  if (metros <= 0) return 0
  const acotado = Math.min(RADIO_MAXIMO_M, Math.max(RADIO_MINIMO_M, metros))
  const t = Math.log(acotado / RADIO_MINIMO_M) / Math.log(RADIO_MAXIMO_M / RADIO_MINIMO_M)
  return Math.round(1 + t * (PASOS - 1))
}

/** «No avisar» · «650 m» · «1,5 km» · «12 km». */
export function formatRadio(metros: number): string {
  if (metros <= 0) return 'No avisar'
  if (metros < 1000) return `${Math.round(metros)} m`
  const km = metros / 1000
  const texto = km < 10 ? km.toFixed(km % 1 === 0 ? 0 : 1) : Math.round(km).toString()
  return `${texto.replace('.', ',')} km`
}

/** Los radios que valen: los elegidos sobre los del servidor. */
export function radiosEfectivos(
  porDefecto: Readonly<Record<string, number>> | undefined,
  elegidos: Readonly<Record<string, number>> | undefined,
): Record<string, number> {
  return { ...RADIOS_RESPALDO, ...porDefecto, ...elegidos }
}
