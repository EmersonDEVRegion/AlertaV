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
 * ===========================================================================
 * LA REESCRITURA: de una nube de puntos a una superficie
 * ===========================================================================
 *
 * Esta capa tenía **dos** representaciones que se relevaban por zoom: un
 * `heatmap` sobre los nodos de la grilla a escala regional y el relleno por
 * celda al acercarse. En pantalla se veía como una cuadrícula de puntos
 * violetas sobre un territorio vacío. Las dos mitades del problema:
 *
 *   1. **Las celdas no teselaban.** `scripts/fetch_seismic_hazard.py` infería
 *      el paso de la grilla en longitud sobre el conjunto completo de nodos, y
 *      la grilla del CSN —definida en una proyección métrica— tiene longitudes
 *      distintas en cada fila. La mediana medía la deriva entre filas
 *      (0,00134°) y no el paso real (0,0537°): cada celda salía **cuarenta
 *      veces más angosta de lo que representa**, y entre dos vecinas quedaban
 *      5 km de hueco. El detalle está en `infer_row_step`, en ese script.
 *   2. **El `heatmap` era la herramienta equivocada.** Un mapa de calor es un
 *      estimador de densidad por kernel: sirve para revelar concentración en
 *      una nube **irregular** de puntos. La grilla del CSN es regular, así que
 *      la densidad es constante por construcción y lo único que el kernel podía
 *      hacer era **sumar el valor de los vecinos**. Eso no es PGA: un nodo
 *      moderado rodeado de nodos altos salía más caliente que su propio valor.
 *      Y con el radio calibrado por debajo del paso de la grilla, cada nodo se
 *      dibujaba como una mota aislada — literalmente la cuadrícula de puntos.
 *
 * Con las celdas teselando, la segunda representación sobra: **el relleno por
 * celda ES la superficie de intensidad continua**, en todo el rango de zoom, y
 * es la misma gramática que usan los mapas oficiales del CSN. Una sola fuente,
 * una sola capa de color, y cada píxel dice el valor que el modelo calculó para
 * ese punto en vez de una suma de vecindad.
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
 * # Por qué cambiaron, y por qué eso importaba tanto como la geometría
 *
 * Eran 0,15 g y 0,60 g. El artefacto real de la V Región va de **0,276 a
 * 0,940 g** (mediana 0,42), así que la rampa estaba descuadrada por los dos
 * extremos a la vez: ningún dato llegaba a tocar el primer color, y **todo el
 * cuartil superior —la franja costera entera, que es justo la zona de mayor
 * amenaza— saturaba en el último**. El resultado era un mapa de un solo violeta
 * plano en el que la variación real, que es un gradiente limpio de costa a
 * cordillera, no se veía.
 *
 * Los valores nuevos encuadran el rango observado con un margen mínimo. Siguen
 * siendo una rampa continua y no cortes de clasificación **a propósito**: el
 * archivo se regenera con un script y su distribución puede cambiar entre
 * versiones del modelo. Una interpolación degrada con elegancia si los valores
 * caen fuera del rango —satura en un extremo— mientras que unos cortes fijos
 * mal calibrados producirían un mapa de un solo color sin avisar. Que es
 * exactamente lo que acababa de pasar.
 */
export const HAZARD_MIN_G = 0.25
export const HAZARD_MAX_G = 0.95

export interface HazardRamp {
  /** Paradas [valor en g, color]. De menor a mayor amenaza. */
  stops: readonly (readonly [number, string])[]
  /** Color del borde de celda. Muy sutil: la grilla no es el dato. */
  line: string
}

/**
 * Dos rampas, una por tema.
 *
 * No es la misma paleta con otra opacidad. Sobre un mapa **oscuro** el ojo lee
 * intensidad como "más luz", así que la rampa va de violeta apagado a violeta
 * brillante. Sobre un mapa **claro** la lectura se invierte: más amenaza es más
 * oscuro. Usar una sola rampa dejaría la zona de mayor amenaza casi invisible
 * en uno de los dos temas.
 *
 * Seis paradas y no cuatro. Con cuatro, el salto entre dos colores consecutivos
 * cubría 0,15 g de rango y la interpolación tenía que cruzar demasiada
 * distancia perceptual de una vez: sobre celdas de 5 km eso se ve como bandas.
 * Las paradas están repartidas sobre los cuartiles reales del artefacto, que es
 * donde hay dato que separar.
 *
 * # El extremo bajo NO puede desaparecer
 *
 * La tentación es arrancar la rampa en el color de fondo, para que la capa
 * «entre» sin peso donde la amenaza es menor. Se probó y está descartado: en el
 * mapa oscuro, un violeta casi negro al 36 % de opacidad sobre `#09090b` es
 * indistinguible del terreno sin capa, y toda la mitad oriental de la región
 * quedaba en blanco. Eso no dice «poca amenaza»: dice «acá no hay dato», o peor,
 * «la capa se apagó». Y ninguna de las dos es cierta sobre 0,3 g — que es más de
 * lo que muchos países consideran zona sísmica.
 *
 * El piso de cada rampa es, entonces, el primer color que se **lee** sobre su
 * mapa base. La escala de amenaza empieza donde empieza la visibilidad.
 */
export const HAZARD_RAMP: Record<'light' | 'dark', HazardRamp> = {
  light: {
    stops: [
      [HAZARD_MIN_G, '#ddd6fe'],
      [0.4, '#c4b5fd'],
      [0.55, '#a78bfa'],
      [0.7, '#7c3aed'],
      [0.82, '#5b21b6'],
      [HAZARD_MAX_G, '#3b0764'],
    ],
    line: '#6d28d9',
  },
  dark: {
    stops: [
      [HAZARD_MIN_G, '#3730a3'],
      [0.4, '#5b21b6'],
      [0.55, '#7c3aed'],
      [0.7, '#a78bfa'],
      [0.82, '#c4b5fd'],
      [HAZARD_MAX_G, '#f5f3ff'],
    ],
    line: '#a78bfa',
  },
}

/**
 * Opacidad del relleno por zoom: `[zoom, opacidad]`.
 *
 * Ya no es un desvanecido cruzado con otra capa —no hay otra capa— sino una
 * sola envolvente, y **baja al acercarse**. Es deliberado y es la regla que
 * mantiene honesta a esta capa:
 *
 *   - A escala regional el usuario encendió la amenaza para leer *la amenaza*,
 *     y el mapa base es sólo referencia. La capa puede pesar.
 *   - Al acercarse a una calle, la pregunta vuelve a ser dónde está uno. Un
 *     velo del 40 % sobre los nombres de calle convierte una capa de contexto
 *     en un estorbo.
 *
 * El techo es distinto por tema porque el punto de partida lo es: sobre un mapa
 * claro, un violeta pálido translúcido se pierde contra el papel; sobre uno
 * oscuro, el mismo valor ya destaca.
 */
export const HAZARD_FILL_OPACITY: Record<
  'light' | 'dark',
  readonly (readonly [number, number])[]
> = {
  light: [
    [7, 0.46],
    [11, 0.42],
    [14, 0.3],
  ],
  dark: [
    [7, 0.4],
    [11, 0.36],
    [14, 0.26],
  ],
}

/**
 * Ventana de aparición de la retícula: `[oculta, visible]`, en niveles de zoom.
 *
 * La retícula **no es el dato** y a escala regional era justamente lo que hacía
 * ver la capa como una cuadrícula. Sólo entra cuando una celda ya ocupa buena
 * parte de la pantalla, que es cuando pasa a ser información útil: recuerda que
 * el modelo se resuelve cada ~5 km y que el degradado suave de más lejos era
 * una interpolación, no una medición continua del terreno.
 */
export const HAZARD_RETICULE: readonly [number, number] = [12.8, 14.2]

/** Opacidad de la retícula en su rango visible. Un hilo, no una reja. */
export const HAZARD_LINE_OPACITY = 0.16

/** Etiquetas de la leyenda: los extremos, en el lenguaje de la variable. */
export const HAZARD_LEGEND = {
  title: 'Amenaza sísmica (PGA)',
  subtitle: '10 % de excedencia en 50 años',
  low: `${HAZARD_MIN_G} g`,
  high: `${HAZARD_MAX_G} g`,
  /** Qué se está mirando. Ya no hay relevo de representación: hay una sola. */
  scale: 'Celdas del modelo, ~5 km',
  reticule: 'La retícula aparece al acercarse',
  caveat:
    'Modelo probabilístico del CSN. Describe la amenaza esperada del terreno, no un evento en curso.',
} as const
