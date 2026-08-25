/**
 * Espejo TypeScript del contrato de la capa meteorológica.
 *
 * Fuente de verdad: `backend/docs/capa-meteorologica.md` y
 * `backend/app/schemas/weather.py`.
 *
 * # Por qué este archivo existe aparte de `api/weather.ts`
 *
 * `api/weather.ts` habla con Open-Meteo **directamente desde el navegador** y
 * sólo trae el viento actual de un incendio seleccionado, para el cono de
 * propagación. Esto otro es la capa de lluvia: la sirve NUESTRO backend, que es
 * quien calcula el flag de riesgo con umbrales de `.env`. Son dos datos, dos
 * orígenes y dos cadencias distintas; compartir archivo invitaría a reutilizar
 * una función que no aplica.
 *
 * # Tres cosas del contrato que mandan sobre todo lo demás
 *
 *   1. **Una comuna ausente es una comuna seca**, no un error. Una colección
 *      vacía es una respuesta correcta y frecuente: en verano lo es durante
 *      semanas.
 *   2. **`riesgo_inundacion` es un booleano real**, no la cadena `"true"`. Las
 *      expresiones de MapLibre no comparan entre tipos: si algún día llegara
 *      como cadena, el filtro del anillo de riesgo dejaría de encontrar nada y
 *      **no habría ningún error en consola**. Por eso `parseRainCollection` lo
 *      normaliza y avisa.
 *   3. **`motivos` viaja concatenado** con `; ` en el GeoJSON —y como lista en
 *      la ruta tipada—, porque MapLibre serializa a texto cualquier arreglo
 *      anidado en `properties`.
 */

import type { Feature, FeatureCollection, Point } from 'geojson'

/**
 * Vocabulario de presentación. Sirve para matizar el color.
 *
 * **Para decidir si hay riesgo se lee el booleano, nunca esta cadena.** Un
 * `nivel` nuevo en el backend no debe poder apagar el anillo de riesgo.
 */
export type RainLevel = 'seco' | 'lluvia' | 'riesgo' | 'riesgo_alto'

/** `properties` de cada feature de `GET /api/v1/events/weather/geojson`. */
export interface RainProperties {
  public_id: string
  comuna: string
  /** ISO-8601. El GeoJSON usa `+00:00`; la ruta tipada, `Z`. Ambas parsean. */
  inicio: string
  fin: string
  ventana_horas: number
  mm_total: number
  mm_hora_max: number
  mm_3h_max: number
  hora_pico: string | null
  /** Puede ser `null`: no todos los modelos publican la variable. */
  probabilidad_max: number | null
  horas_con_lluvia: number
  /** Booleano estricto. Ver la nota 2 de la cabecera. */
  riesgo_inundacion: boolean
  nivel: RainLevel
  /** Cadena unida por `; `. `''` cuando no hay riesgo. */
  motivos: string
  modelo: string
  /** Siempre `true`: esta capa habla del futuro, no de algo ocurrido. */
  es_pronostico: boolean
  is_confirmed_incident: boolean
}

export type RainFeature = Feature<Point, RainProperties>
export type RainCollection = FeatureCollection<Point, RainProperties>

/** Parámetros de `GET /api/v1/events/weather/geojson`. */
export interface RainQuery {
  /** 1–48. Holgura hacia atrás, **no** ventana histórica. */
  hours?: number
  solo_riesgo?: boolean
  limit?: number
}
