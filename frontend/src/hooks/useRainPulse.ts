import { useEffect, useState } from 'react'
import type { Map as MapLibreMap } from 'maplibre-gl'
import { RAIN_RISK_RING_LAYER_ID } from '@/components/map/rainLayers'

/**
 * El pulso del anillo de riesgo de inundación.
 *
 * # Por qué NO es un `fill-pattern` animado
 *
 * Fue la primera idea y no funciona, por dos motivos distintos:
 *
 *   1. **Un SVG animado no se anima.** MapLibre rasteriza la imagen UNA vez al
 *      registrarla con `addImage` y sube el bitmap al atlas de texturas. Las
 *      animaciones SMIL o CSS del SVG no se ejecutan nunca: lo que queda en
 *      pantalla es el primer fotograma, congelado.
 *   2. **Un patrón por frames sí anima, y cuesta carísimo.** La vía real sería
 *      una `StyleImageInterface` cuyo `render()` devuelve `true` en cada frame.
 *      Eso obliga a **reconstruir y volver a subir el atlas de texturas a la GPU
 *      60 veces por segundo** y fuerza un repintado completo del mapa en cada
 *      uno. En un teléfono de gama media con teselas cargando debajo, ahí se van
 *      los 60 fps.
 *
 * # Lo que sí se hace
 *
 * Se anima **una propiedad de pintura constante y escalar**
 * (`circle-stroke-opacity`) sobre una capa filtrada a las comunas en riesgo — 0
 * a 3 features en un invierno normal. Tres propiedades hacen que sea barato:
 *
 *   * **No es data-driven.** Una expresión por feature obligaría a reevaluarla y
 *     a volver a subir el búfer de vértices de pintura; un escalar constante
 *     viaja como un uniform del shader. Es de lo más barato que MapLibre puede
 *     cambiar. Por eso no se anima el radio, que sí es data-driven.
 *   * **Se escribe a ~12 Hz, no a 60.** MapLibre repinta cuando algo cambia, así
 *     que la frecuencia de escritura ES la frecuencia de repintado. Un
 *     desvanecimiento de opacidad a 12 fps es indistinguible de uno a 60, y
 *     cuesta la quinta parte. El rAF sigue corriendo a 60 Hz pero la mayoría de
 *     los ticks sólo comparan dos números y salen.
 *   * **No corre casi nunca.** Sin comunas en riesgo, sin capa encendida o con
 *     `prefers-reduced-motion`, el efecto ni siquiera registra el rAF. El costo
 *     en un día seco —el caso normal— es exactamente cero.
 *
 * La capa declara `circle-stroke-opacity-transition: { duration: 0 }`. Sin eso,
 * la transición por defecto de 300 ms interpolaría entre nuestras escrituras y
 * el mapa volvería a repintar a 60 fps: la optimización se perdería entera.
 */

/** Un ciclo completo. Lento a propósito: respirar, no parpadear. */
const PULSE_PERIOD_MS = 3200

/** Mínimo entre escrituras. ~12 repintados por segundo. */
const WRITE_INTERVAL_MS = 80

/** Por debajo de esto el ojo no distingue el cambio; no vale un repintado. */
const EPSILON = 0.01

/**
 * `prefers-reduced-motion`.
 *
 * Reactivo y no leído una sola vez: en iOS y en Windows la preferencia se puede
 * cambiar con la aplicación abierta, y una animación que ignora eso en una app
 * que alguien consulta durante una emergencia es justo el peor momento.
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const sync = () => setReduced(query.matches)
    sync()
    query.addEventListener('change', sync)
    return () => query.removeEventListener('change', sync)
  }, [])

  return reduced
}

/**
 * @param map     Instancia nativa de MapLibre. `null` mientras no exista.
 * @param active  Sólo `true` si la capa está encendida Y hay comunas en riesgo.
 * @param range   `[mínimo, máximo]` de opacidad. El máximo es el valor en reposo.
 */
export function useRainPulse(
  map: MapLibreMap | null,
  active: boolean,
  range: readonly [number, number],
): void {
  const reduced = usePrefersReducedMotion()
  const [min, max] = range

  useEffect(() => {
    if (!map) return

    /*
     * La capa puede no existir todavía —o ya no existir, si un cambio de tema
     * disparó `setStyle` y MapLibre aún no la recreó—. `setPaintProperty` sobre
     * una capa ausente lanza; consultarla antes cuesta una búsqueda en un mapa.
     */
    const write = (value: number): boolean => {
      if (!map.getLayer(RAIN_RISK_RING_LAYER_ID)) return false
      map.setPaintProperty(RAIN_RISK_RING_LAYER_ID, 'circle-stroke-opacity', value)
      return true
    }

    if (!active || reduced) {
      // Reposo en el MÁXIMO, no en el mínimo: si la animación no corre, el
      // anillo tiene que seguir viéndose igual de legible. Una capa que sólo se
      // entiende cuando se mueve está mal diseñada.
      write(max)
      return
    }

    let frame = 0
    let lastWrite = 0
    let lastValue = Number.NaN

    const tick = (now: number) => {
      frame = requestAnimationFrame(tick)

      // Estrangulador. La mayoría de los ticks terminan acá: una resta y un
      // retorno. El rAF además se detiene solo cuando la pestaña pasa a segundo
      // plano, así que no hay nada que apagar a mano.
      if (now - lastWrite < WRITE_INTERVAL_MS) return

      // Coseno alzado: entra y sale suave, sin el tirón de una rampa lineal.
      const phase = (now % PULSE_PERIOD_MS) / PULSE_PERIOD_MS
      const eased = (1 - Math.cos(phase * 2 * Math.PI)) / 2
      const value = Math.round((min + (max - min) * eased) * 100) / 100

      if (Math.abs(value - lastValue) < EPSILON) return
      if (!write(value)) return

      lastWrite = now
      lastValue = value
    }

    frame = requestAnimationFrame(tick)

    return () => {
      cancelAnimationFrame(frame)
      // Restituir el reposo. Sin esto, apagar la capa la dejaría congelada en
      // el punto del ciclo en que iba, y al volver a encenderla el anillo
      // aparecería medio transparente sin motivo aparente.
      write(max)
    }
  }, [map, active, reduced, min, max])
}
