/**
 * Borde difuminado y ondulado para las capas que son una imagen de una grilla.
 *
 * Lo comparten la lluvia (§L) y la amenaza sísmica (§I). Las dos grillas
 * terminan en una caja y el fenómeno no: sin esto el mapa muestra un corte a
 * cuchillo en una línea recta que no existe.
 *
 * Dos piezas:
 *
 *   1. **Difuminado**: el alfa cae a cero en las últimas `feather` celdas.
 *   2. **Ondulación**: el comienzo de esa caída se corre hacia adentro entre 0
 *      y `wobble` celdas. Un degradado recto se sigue leyendo como una recta
 *      (§L, la cordillera del 30-09): el ojo ve la línea donde el color empieza
 *      a caer.
 */

/**
 * Ondulación determinista en [0, 1]: dos senos de frecuencias que no se
 * sincronizan. Ni `Math.random` ni ruido con semilla: la misma grilla tiene que
 * dar siempre la misma imagen, o el borde "respiraría" en cada refresco.
 * Longitudes de onda de 5 a 8 celdas: ondas amplias, no un borde dentado.
 */
export function edgeWobble(gx: number, gy: number): number {
  const w = 0.6 * Math.sin(gx * 0.9 + gy * 0.35) + 0.4 * Math.sin(gy * 1.25 - gx * 0.55 + 1.7)
  return (w + 1) / 2
}

/** Escalón suave de 0 a 1 (`smoothstep`), saturado fuera de [0, 1]. */
function smoothstep(t: number): number {
  const x = Math.max(0, Math.min(1, t))
  return x * x * (3 - 2 * x)
}

/**
 * Factor de alfa en [0, 1] para un punto a `edge` celdas del borde.
 *
 * `gx` y `gy` son la posición en la grilla, sólo para la ondulación. En el borde
 * mismo (`edge <= 0`) es siempre 0, ondule como ondule.
 */
export function edgeFade(
  edge: number,
  gx: number,
  gy: number,
  feather: number,
  wobble: number,
): number {
  if (feather <= 0) return edge >= 0 ? 1 : 0
  const inset = wobble > 0 ? wobble * edgeWobble(gx, gy) : 0
  return smoothstep((edge - inset) / feather)
}
