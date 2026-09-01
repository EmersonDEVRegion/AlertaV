/**
 * Simbología de la capa de amenaza sísmica.
 *
 * # Qué representa, y por qué no puede parecerse a una emergencia
 *
 * Es un modelo **probabilístico y estático** del CSN: dice cuánto puede llegar
 * a acelerarse el suelo en cada celda, no que algo esté ocurriendo. Comparte
 * pantalla con incendios activos y cortes en curso, así que la regla que ordena
 * todo este archivo es que **no debe leerse como un evento**.
 *
 * De ahí salen las tres decisiones:
 *
 *   1. **Rampa continua, no bandas.** Las otras capas usan `match` sobre
 *      categorías discretas porque codifican estados. Ésta interpola sobre un
 *      número real. Un degradado suave se lee como "campo de fondo"; escalones
 *      duros se leerían como zonas declaradas por alguien.
 *   2. **Fuera de la familia cálida.** Rojo, naranja y amarillo ya están
 *      tomados por incendios y sismos reales. Esta capa usa violeta, que no
 *      colisiona con ninguna paleta de emergencia.
 *   3. **Opacidad baja y sin trazo fuerte.** Es contexto bajo los datos, no
 *      encima.
 *
 * # La variable
 *
 * Se pinta `pga_475`: aceleración máxima del suelo en g, con 10 % de
 * probabilidad de excedencia en 50 años (período de retorno ≈ 475 años). Es el
 * nivel del diseño sísmico habitual y el que tiene sentido para público
 * general; `pga_2475` es para estructuras críticas y `sa*` para cálculo
 * estructural. El archivo trae las cinco: cambiar de variable es cambiar el
 * nombre en `HAZARD_VARIABLE`.
 */

/** Propiedad del GeoJSON que decide el color. */
export const HAZARD_VARIABLE = 'pga_475'

/**
 * Extremos de la rampa, en g.
 *
 * Son una rampa continua y no cortes de clasificación **a propósito**: el
 * archivo se genera con un script y su distribución real puede cambiar entre
 * versiones del modelo. Una interpolación degrada con elegancia si los valores
 * caen fuera del rango —satura en un extremo— mientras que unos cortes fijos
 * mal calibrados producirían un mapa de un solo color sin avisar.
 */
export const HAZARD_MIN_G = 0.15
export const HAZARD_MAX_G = 0.6

export interface HazardRamp {
  /** Paradas [valor en g, color]. De menor a mayor amenaza. */
  stops: readonly (readonly [number, string])[]
  /** Color del borde de celda. Muy sutil: la grilla no es el dato. */
  line: string
  fillOpacity: number
  lineOpacity: number
}

/**
 * Dos rampas, una por tema.
 *
 * No es la misma paleta con otra opacidad. Sobre un mapa **oscuro** el ojo lee
 * intensidad como "más luz", así que la rampa va de violeta apagado a violeta
 * brillante. Sobre un mapa **claro** la lectura se invierte: más amenaza es más
 * oscuro. Usar una sola rampa dejaría la zona de mayor amenaza casi invisible
 * en uno de los dos temas.
 */
export const HAZARD_RAMP: Record<'light' | 'dark', HazardRamp> = {
  light: {
    stops: [
      [HAZARD_MIN_G, '#ede9fe'],
      [0.3, '#c4b5fd'],
      [0.45, '#8b5cf6'],
      [HAZARD_MAX_G, '#5b21b6'],
    ],
    line: '#6d28d9',
    fillOpacity: 0.35,
    lineOpacity: 0.18,
  },
  dark: {
    stops: [
      [HAZARD_MIN_G, '#3b1d6e'],
      [0.3, '#6d28d9'],
      [0.45, '#a78bfa'],
      [HAZARD_MAX_G, '#ddd6fe'],
    ],
    line: '#a78bfa',
    fillOpacity: 0.28,
    lineOpacity: 0.14,
  },
}

/* ===========================================================================
 * Mapa de calor
 * ===========================================================================
 *
 * # Por qué la capa cambia de forma según la escala
 *
 * Las celdas y el mapa de calor dicen lo mismo con gramáticas distintas, y cada
 * una sólo funciona en su rango:
 *
 *   - A escala regional (z7–z11) el usuario no está leyendo el valor de una
 *     celda: está buscando **dónde se concentra la amenaza**. Cuatro mil
 *     rectángulos de 5 km pintados uno al lado del otro producen un mosaico que
 *     el ojo tiene que integrar solo. Un `heatmap` hace esa integración en el
 *     shader: es literalmente un estimador de densidad por kernel, y lo que
 *     devuelve es la tendencia.
 *   - Al acercarse a una comuna la pregunta cambia a **cuánto**, y ahí el
 *     difuminado es una pérdida de información: la celda tiene un borde real,
 *     un valor real, y el relleno lo respeta.
 *
 * El cruce es una interpolación de opacidad sobre el zoom, no un `minzoom`
 * duro: dos capas apareciendo y desapareciendo de golpe en el mismo umbral se
 * ve como un parpadeo. Con el solape, durante ~1,5 niveles de zoom conviven y
 * el ojo lee una transformación, no un cambio de capa.
 */

/**
 * Ventana del cruce, en niveles de zoom: `[dominio del calor, dominio de las celdas]`.
 *
 * Por debajo del primer valor manda el mapa de calor; por encima del segundo,
 * las celdas. En medio se cruzan. Los dos usan la MISMA ventana en direcciones
 * opuestas para que la suma de opacidades no se hunda a la mitad del camino.
 */
export const HAZARD_CROSSFADE: readonly [number, number] = [10.5, 12.2]

/**
 * Radio del kernel, en píxeles: `[zoom, radio]`.
 *
 * **Duplica en cada nivel de zoom, y eso no es estética.** El radio de un
 * `heatmap` se declara en píxeles de pantalla, pero lo que representa es una
 * distancia sobre el terreno: el paso de la grilla del CSN (~0,045°, unos 5 km).
 * Como cada nivel de zoom duplica los píxeles por metro, un radio fijo
 * significaría un kernel que cubre 30 km a z7 y 400 m a z13 — la misma capa
 * diciendo dos cosas distintas según cuánto se haya acercado el usuario.
 *
 * Los valores están calibrados sobre la resolución real a la latitud de
 * Valparaíso (~1 025 m/px a z7): el radio queda algo por encima del paso de la
 * grilla, que es lo que hace que los nodos vecinos se fundan en un campo
 * continuo en vez de leerse como una nube de puntos.
 */
export const HAZARD_HEAT_RADIUS: readonly (readonly [number, number])[] = [
  [7, 14],
  [9, 30],
  [11, 62],
  [13, 130],
]

/**
 * Intensidad por zoom.
 *
 * Sube poco y a propósito. La intensidad multiplica la densidad acumulada antes
 * de mapearla a color: pasada de rosca, la rampa satura en su extremo caliente
 * y el mapa entero se vuelve del mismo color — que es el modo de falla clásico
 * del `heatmap` y el que lo hace ver «hecho a la rápida». La grilla es regular,
 * así que la densidad de puntos ya es constante; lo único que debe variar el
 * color es el PESO, o sea el PGA.
 */
export const HAZARD_HEAT_INTENSITY: readonly (readonly [number, number])[] = [
  [7, 0.9],
  [11, 1.25],
  [13, 1.5],
]

export interface HazardHeatRamp {
  /**
   * Paradas de `heatmap-density`, de 0 a 1.
   *
   * **La primera tiene que ser transparente.** `heatmap-color` se evalúa sobre
   * TODO el lienzo, también donde la densidad es cero: un color opaco en la
   * parada 0 pinta un velo sobre la región entera, incluido el mar. Es el error
   * que hace que un mapa de calor se vea como una mancha sucia.
   */
  stops: readonly (readonly [number, string])[]
  /** Opacidad de la capa en su rango dominante. */
  opacity: number
}

/**
 * Rampas de densidad, una por tema.
 *
 * Misma lógica que el relleno de celdas y por la misma razón: sobre un mapa
 * oscuro el ojo lee intensidad como más luz; sobre uno claro, como más
 * saturación y menos luz. Y la familia sigue siendo violeta — el mapa de calor
 * no puede invadir el rojo y el naranja de las emergencias sólo porque la
 * convención de los `heatmap` sea el arcoíris de siempre. Un degradado que
 * termina en rojo diría «acá está pasando algo», que es exactamente lo que esta
 * capa NO afirma.
 */
export const HAZARD_HEAT: Record<'light' | 'dark', HazardHeatRamp> = {
  light: {
    stops: [
      [0, 'rgba(237, 233, 254, 0)'],
      [0.2, 'rgba(196, 181, 253, 0.35)'],
      [0.45, 'rgba(139, 92, 246, 0.5)'],
      [0.7, 'rgba(109, 40, 217, 0.62)'],
      [1, 'rgba(76, 29, 149, 0.74)'],
    ],
    opacity: 0.85,
  },
  dark: {
    stops: [
      [0, 'rgba(30, 27, 75, 0)'],
      [0.2, 'rgba(76, 29, 149, 0.4)'],
      [0.45, 'rgba(124, 58, 237, 0.55)'],
      [0.7, 'rgba(167, 139, 250, 0.68)'],
      [1, 'rgba(221, 214, 254, 0.82)'],
    ],
    opacity: 0.8,
  },
}

/** Etiquetas de la leyenda: los extremos, en el lenguaje de la variable. */
export const HAZARD_LEGEND = {
  title: 'Amenaza sísmica (PGA)',
  subtitle: '10 % de excedencia en 50 años',
  low: `${HAZARD_MIN_G} g`,
  high: `${HAZARD_MAX_G} g`,
  /** Lo que el usuario ve según cuánto se haya acercado. Ver `HAZARD_CROSSFADE`. */
  heat: 'Concentración regional',
  cells: 'Celdas del modelo',
  zoomHint: 'Acércate para ver el valor por celda',
  caveat:
    'Modelo probabilístico del CSN. Describe la amenaza esperada del terreno, no un evento en curso.',
} as const
