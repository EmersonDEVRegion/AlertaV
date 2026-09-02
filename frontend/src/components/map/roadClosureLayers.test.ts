// @vitest-environment node
/**
 * Tests de la capa de cortes de ruta.
 *
 * Los cuatro bloques cubren fallos que NO producen error en consola:
 *
 *   1. **El anclaje.** Si `ROAD_CLOSURE_BEFORE_ID` deja de coincidir con una
 *      capa real, MapLibre emite un `error` y descarta la capa entera. Peor si
 *      el ancla existe pero está en el sitio equivocado: los cortes taparían
 *      los pines de emergencia y el mapa invertiría su jerarquía sin decir nada.
 *   2. **El zoom como raíz del radio.** `["zoom"]` sólo puede ser la entrada de
 *      un `interpolate` de nivel superior. Anidarlo tira el estilo completo — es
 *      el error que ya se pagó dos veces en este repositorio.
 *   3. **`has` y no `!= null`.** Es lo único que separa un aviso del MTT —sin
 *      escala publicada— de una emergencia del MOP de gravedad mínima. Si esa
 *      discriminación se rompe, el mapa afirma que alguien midió algo que nadie
 *      midió, y en la dirección peligrosa: hacia «no pasa nada».
 *   4. **El salto al rojo cae en 4.** `severity_rank` del backend hace
 *      `transito * 2 + (1 si grave)`, así que 4 es el primer valor que
 *      significa «no se puede pasar». Si el salto se moviera, el color dejaría
 *      de responder la única pregunta que esta capa existe para responder.
 */

import { describe, expect, it } from 'vitest'
import {
  CLOSURE_BODY_LAYER_ID,
  CLOSURE_CUT_RING_LAYER_ID,
  CLOSURE_HIT_LAYER_ID,
  CLOSURE_MTT_DOT_LAYER_ID,
  HAS_SEVERITY,
  IS_CUT,
  ROAD_CLOSURE_BEFORE_ID,
  ROAD_CLOSURE_LAYER_IDS,
  closureBodyLayer,
  closureColor,
  closureCutRingLayer,
  closureHitLayer,
  closureMttDotLayer,
} from './roadClosureLayers'
import { coneFillLayer } from './overlayLayers'
import { alertHaloLayer, coreLayer } from './incidentLayers'
import { RAIN_BEFORE_ID } from './rainLayers'
import {
  ROAD_CLOSURE_PALETTE,
  SEVERITY_CUT,
  SEVERITY_MAX,
  severityLabel,
} from '@/domain/roadClosureSymbology'

const THEMES = ['light', 'dark'] as const

/** Sólo las que tienen `circle-radius` construido por la fábrica. */
const ALL = [closureCutRingLayer, closureBodyLayer, closureMttDotLayer] as const

/** Recorre una expresión buscando `["zoom"]` a cualquier profundidad. */
function findZoomDepth(node: unknown, depth = 0): number[] {
  if (!Array.isArray(node)) return []
  if (node[0] === 'zoom') return [depth]
  return node.flatMap((child) => findZoomDepth(child, depth + 1))
}

describe('jerarquía de dibujo', () => {
  it('se ancla a una capa que existe de verdad', () => {
    expect(ROAD_CLOSURE_BEFORE_ID).toBe(coneFillLayer.id)
  })

  it('comparte ancla con la lluvia: es la única capa propia siempre montada', () => {
    // No es coincidencia y no debe "arreglarse" dándole un ancla propia. Las
    // capas de incidentes y sismos viven bajo un interruptor; anclarse a una de
    // ellas haría que MapLibre descartara ésta cuando el usuario la apague.
    expect(ROAD_CLOSURE_BEFORE_ID).toBe(RAIN_BEFORE_ID)
  })

  it('no se ancla a una capa de incidentes: los cortes van estrictamente por debajo', () => {
    expect(ROAD_CLOSURE_BEFORE_ID).not.toBe(alertHaloLayer.id)
    expect(ROAD_CLOSURE_BEFORE_ID).not.toBe(coreLayer.id)
  })

  it('el anillo de corte va el más abajo y el objetivo de toque el más arriba', () => {
    // El orden del arreglo ES el orden de inserción, y por tanto el de dibujo.
    // Con el anillo encima del cuerpo se leería como borde y no como halo.
    expect(ROAD_CLOSURE_LAYER_IDS[0]).toBe(CLOSURE_CUT_RING_LAYER_ID)
    expect(ROAD_CLOSURE_LAYER_IDS.at(-1)).toBe(CLOSURE_HIT_LAYER_ID)
  })

  it('el re-anclaje conoce todas las capas que se montan', () => {
    const montadas = [
      CLOSURE_CUT_RING_LAYER_ID,
      CLOSURE_BODY_LAYER_ID,
      CLOSURE_MTT_DOT_LAYER_ID,
      CLOSURE_HIT_LAYER_ID,
    ]
    // Una capa fuera de esta lista no se re-ancla tras un cambio de tema y se
    // queda flotando por encima de los incidentes. No hay error: sólo queda mal.
    expect([...ROAD_CLOSURE_LAYER_IDS].sort()).toEqual(montadas.sort())
  })
})

describe('el zoom es la raíz de la interpolación del radio', () => {
  for (const theme of THEMES) {
    for (const factory of ALL) {
      it(`${factory.name} (${theme}) no anida ["zoom"]`, () => {
        const radius = factory(theme, true).paint?.['circle-radius']
        const depths = findZoomDepth(radius)

        expect(depths.length).toBeGreaterThan(0)
        // Profundidad 1 = hijo directo del `interpolate` raíz. Cualquier valor
        // mayor significa que quedó dentro de otra expresión, y MapLibre
        // rechaza el estilo COMPLETO con «zoom expression may only be used as
        // input to a top-level step or interpolate expression».
        expect(depths.every((depth) => depth === 1)).toBe(true)
      })
    }
  }

  it('el objetivo de toque tampoco lo anida', () => {
    const depths = findZoomDepth(closureHitLayer(true).paint?.['circle-radius'])
    expect(depths.every((depth) => depth === 1)).toBe(true)
  })
})

describe('el MTT y el MOP no se confunden', () => {
  it('la discriminación es `has`, no una comparación con null', () => {
    /*
     * `["!=", ["get","severidad"], null]` parece equivalente y no lo es: `get`
     * sobre una clave ausente devuelve `null`, igual que una clave presente con
     * valor nulo. `has` responde exactamente la pregunta que se quiere hacer, y
     * el cliente BORRA la clave cuando no hay valor precisamente para que esto
     * funcione (ver `parseRoadClosures`).
     */
    expect(HAS_SEVERITY).toEqual(['has', 'severidad'])
  })

  it('el punto central hueco es exclusivo de los avisos sin escala', () => {
    expect(closureMttDotLayer('dark', true).filter).toEqual(['!', HAS_SEVERITY])
  })

  it('el cuerpo elige entre la rampa del MOP y el tono del MTT, no interpola los dos', () => {
    for (const theme of THEMES) {
      const color = closureColor(theme) as unknown[]
      expect(color[0]).toBe('case')
      expect(color[1]).toEqual(HAS_SEVERITY)
      // La rama de respaldo —sin severidad— es el tono del MTT tal cual, no un
      // extremo de la rampa. Si esto cambiara a `palette.low`, cada aviso del
      // portal se pintaría como «emergencia de gravedad mínima».
      expect(color.at(-1)).toBe(ROAD_CLOSURE_PALETTE[theme].mtt)
    }
  })

  it('el tono del MTT no es ninguno de los de la rampa del MOP', () => {
    for (const theme of THEMES) {
      const { mtt, low, mid, high } = ROAD_CLOSURE_PALETTE[theme]
      expect([low, mid, high]).not.toContain(mtt)
    }
  })
})

describe('la jerarquía cromática responde «¿puedo pasar?»', () => {
  it('el salto al rojo cae exactamente en el escalón de ruta cortada', () => {
    // Atado a `severity_rank` del backend: `transito` aporta 0, 2 o 4, así que
    // 4 es el primer valor que significa «no se puede pasar». Mover esto
    // desalinearía el color del dominio sin que nada falle.
    expect(SEVERITY_CUT).toBe(4)
    expect(IS_CUT).toEqual(['>=', ['to-number', ['get', 'severidad'], 0], SEVERITY_CUT])
  })

  it('el rojo empieza en el escalón de corte y llega hasta el máximo', () => {
    for (const theme of THEMES) {
      const { high } = ROAD_CLOSURE_PALETTE[theme]
      const ramp = (closureColor(theme) as unknown[])[2] as unknown[]

      // Pares [tope, color] a partir del índice 3 del `interpolate`.
      const stops = new Map<number, string>()
      for (let i = 3; i < ramp.length; i += 2) {
        stops.set(ramp[i] as number, ramp[i + 1] as string)
      }

      expect(stops.get(SEVERITY_CUT)).toBe(high)
      expect(stops.get(SEVERITY_MAX)).toBe(high)
      // Y por debajo del corte, nunca el rojo: una ruta transitable no puede
      // pintarse como cortada, por muy dañada que esté.
      expect(stops.get(0)).not.toBe(high)
      expect(stops.get(3)).not.toBe(high)
    }
  })

  it('el anillo sólo rodea a los cortes efectivos', () => {
    expect(closureCutRingLayer('dark', true).filter).toEqual(IS_CUT)
  })

  it('el anillo es estático: reposa en el extremo ALTO de su rango', () => {
    /*
     * La capa de lluvia tuvo un pulso por `requestAnimationFrame` y se quitó
     * para no dejar el mapa repintando por un adorno. Este anillo nace estático
     * por la misma decisión. Que repose en el máximo y no en el mínimo importa:
     * un anillo que se quedara abajo al no animarse desaparecería justo para
     * quien tenga `prefers-reduced-motion`.
     */
    for (const theme of THEMES) {
      const palette = ROAD_CLOSURE_PALETTE[theme]
      const opacity = closureCutRingLayer(theme, true).paint?.['circle-stroke-opacity']
      expect(opacity).toBe(palette.ringOpacity[1])
    }
  })
})

describe('encendido y apagado', () => {
  it('se apaga por visibility, nunca desmontando la fuente', () => {
    for (const factory of ALL) {
      expect(factory('dark', false).layout?.visibility).toBe('none')
      expect(factory('dark', true).layout?.visibility).toBe('visible')
    }
    expect(closureHitLayer(false).layout?.visibility).toBe('none')
  })
})

describe('etiquetas', () => {
  it('sin severidad NO cae en el tramo bajo', () => {
    // Es la misma mentira que `?? 0` en el cliente, contada con palabras.
    expect(severityLabel(null)).toBe('Sin gravedad informada')
    expect(severityLabel(undefined)).toBe('Sin gravedad informada')
    expect(severityLabel(0)).toBe('Transitable')
  })

  it('el corte de la etiqueta coincide con el del color', () => {
    expect(severityLabel(SEVERITY_CUT - 1)).not.toBe('Ruta cortada')
    expect(severityLabel(SEVERITY_CUT)).toBe('Ruta cortada')
    expect(severityLabel(SEVERITY_MAX)).toBe('Ruta cortada')
  })
})
