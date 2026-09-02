/**
 * Diccionario visual de las emergencias.
 *
 * # Los tipos son los del backend, no los del enunciado
 *
 * No existe un tipo `fire` ni `earthquake` en `IncidentType`: el fuego llega
 * como `wildfire`, `structural_fire` o `possible_fire`, y los sismos ni siquiera
 * viven en esa fuente —vienen de `/events/seismic`, con su propio esquema—. El
 * `match` de MapLibre compara valores literales, así que un tipo inventado no
 * falla: simplemente cae en el respaldo y todos los puntos comparten icono.
 * De ahí que este mapa se declare contra los enum reales.
 *
 * # Siluetas macizas, no contornos
 *
 * Lienzo de 24, como lucide, pero los glifos son **siluetas rellenas** y están
 * dibujados para este mapa en vez de tomados de una librería. El motivo es el
 * tamaño real: con `icon-size` entre 0.3 y 0.6 sobre un lienzo de 64, el glifo
 * mide entre 14 y 29 px en pantalla. Ahí un contorno de 2 unidades sobre 24
 * queda en poco más de un píxel y todo detalle interior se cierra.
 *
 * Los dos que lo demostraron: la `flame` de lucide tiene un rizo interior que a
 * ese tamaño se cerraba y dejaba una espiral, y la `car-crash` es un vehículo
 * cuyo contorno nunca cierra —arcos abiertos, ruedas que son segmentos y una
 * marca de impacto flotando arriba—, que a 14 px se leía como una nave con tren
 * de aterrizaje. Ninguno de los dos era un error de la librería: son iconos
 * pensados para 24 px de interfaz, no para un símbolo de mapa recoloreado.
 *
 * Reglas del set, para que lo que se agregue no desentone:
 *
 * * **La silueta hace todo el trabajo.** Lo que no se lea en el contorno
 *   exterior no se va a leer. Los recortes interiores son un lujo que se ve a
 *   40 px y desaparece a 14; se usan para dar carácter, nunca para significar.
 * * **Ningún elemento por debajo de ~2 unidades.** A 14 px eso es medio píxel:
 *   se convierte en niebla y ensucia el halo.
 * * **Pocas piezas sueltas.** Un glifo de tres fragmentos separados se lee a 40
 *   px y se vuelve un borrón a 14. El vehículo son tres —carrocería y dos
 *   ruedas— y es el límite.
 * * **Cada silueta distinta de las demás EN NEGRO**, sin color que ayude: en el
 *   mapa el color codifica el nivel de confianza, no el tipo, así que dos
 *   glifos parecidos no se distinguen por el tono.
 */

/** Identificadores registrados en el estilo con `map.addImage`. */
export const ICON_IDS = [
  'av-flame',
  'av-waves',
  'av-barrier',
  'av-crash',
  'av-alert',
  'av-rescue',
  'av-flood',
] as const
export type IconId = (typeof ICON_IDS)[number]

export interface IconGlyph {
  /** Sub-trazos del glifo, en un lienzo de 24×24. */
  paths: readonly string[]
  /** Qué representa. Alimenta la leyenda. */
  label: string
}

export const ICON_GLYPHS: Record<IconId, IconGlyph> = {
  /*
   * Tres lenguas y una base ancha.
   *
   * Es lo que separa una llama de una gota, y no es sutil: con una sola punta y
   * fondo redondo la silueta se lee como gota SIEMPRE, por mucho que se le
   * insinúe un rizo. Los valles entre lenguas bajan casi un tercio de la altura
   * justamente para que sobrevivan al reescalado.
   *
   * El recorte del núcleo es decorativo: se ve a 40 px, se cierra a 14, y el
   * icono sigue leyéndose sin él.
   */
  'av-flame': {
    label: 'Incendio',
    paths: [
      'M12 1.2c1 3.8 1.8 6.6 2.7 8.6.7-1.3 1.5-2.8 2.2-4.2 1.1 2.5 2.4 5.6 2.4 8.8a7.3 7.3 0 0 1-14.6 0c0-2.9 1.1-6 2.4-8.6.7 1.3 1.5 2.6 2.2 3.8.4-1.9 1.7-4.7 2.7-8.4zm0 12.4c-1.6 1.5-2.5 2.9-2.5 4.2a2.5 2.5 0 0 0 5 0c0-1.3-.9-2.7-2.5-4.2z',
    ],
  },
  /*
   * Epicentro: el punto y dos ondas que se abren.
   *
   * Las ondas son medialunas macizas y no arcos trazados. Un arco no encierra
   * área, así que trazarlo obligaría a un modo aparte en el rasterizador; como
   * medialuna se dibuja con el mismo relleno que el resto y además engorda
   * hacia el centro, que es donde tiene que verse a 14 px.
   */
  'av-waves': {
    label: 'Sismo',
    paths: [
      'M12 9.2a2.8 2.8 0 1 1 0 5.6 2.8 2.8 0 0 1 0-5.6z',
      'M7.4 4.4 5.3 2.3a13.7 13.7 0 0 0 0 19.4l2.1-2.1a10.7 10.7 0 0 1 0-15.2z',
      'M16.6 4.4l2.1-2.1a13.7 13.7 0 0 1 0 19.4l-2.1-2.1a10.7 10.7 0 0 0 0-15.2z',
    ],
  },
  /* Barrera de obra: tablero con franjas recortadas y dos patas. */
  'av-barrier': {
    label: 'Corte de ruta',
    paths: [
      'M1.6 4.6h20.8v6.6H1.6zM6.4 4.6 2.8 11.2h2.9l3.6-6.6zM13 4.6 9.4 11.2h2.9l3.6-6.6zM19.6 4.6 16 11.2h2.9l3.6-6.6z',
      'M5.4 11.2h2.7v9.2H5.4zM15.9 11.2h2.7v9.2h-2.7z',
    ],
  },
  /*
   * Un vehículo, sin marca de impacto.
   *
   * La marca se probó y se descartó: a 14 px el estallido y la carrocería se
   * funden en un borrón erizado, y a 40 px compiten. El icono dice VEHÍCULO y
   * deja que el resto de la interfaz diga siniestro —la capa se llama
   * «Accidentes viales», el color codifica la confianza y la ficha lo escribe—.
   * Repetirlo en la silueta cuesta legibilidad y no agrega información.
   */
  'av-crash': {
    label: 'Accidente vial',
    paths: [
      'M6.3 6.4h9.3c.6 0 1.2.3 1.5.9l2.4 4 1.4.4c.9.3 1.6 1.1 1.6 2.1v2.7c0 .6-.5 1.1-1.1 1.1H2.6c-.6 0-1.1-.5-1.1-1.1v-3c0-1 .6-1.8 1.5-2.1l1.4-.4z',
      'M6.9 15.4a2.9 2.9 0 1 1 0 5.8 2.9 2.9 0 0 1 0-5.8zM17.1 15.4a2.9 2.9 0 1 1 0 5.8 2.9 2.9 0 0 1 0-5.8z',
    ],
  },
  /* Triángulo macizo con el signo recortado. El más robusto del set. */
  'av-alert': {
    label: 'Contingencia',
    paths: [
      'M12 2.2a1.6 1.6 0 0 1 1.4.8l9 15.6a1.6 1.6 0 0 1-1.4 2.4H3a1.6 1.6 0 0 1-1.4-2.4l9-15.6a1.6 1.6 0 0 1 1.4-.8zm-1.4 6.2v5.8h2.8V8.4zm0 7.6v2.8h2.8V16z',
    ],
  },
  /*
   * Salvavidas: anillo con cuatro amarras.
   *
   * Es el más parecido al del sismo —los dos son redondos con centro— y se
   * distinguen por la masa: éste es un anillo grueso y aquél un punto suelto
   * entre dos arcos finos. Además nunca comparten capa.
   */
  'av-rescue': {
    label: 'Rescate',
    paths: [
      'M12 1.8a10.2 10.2 0 1 0 0 20.4 10.2 10.2 0 0 0 0-20.4zm0 3.6a6.6 6.6 0 1 1 0 13.2 6.6 6.6 0 0 1 0-13.2z',
      'M10.5 5.2h3v4.6h-3zM10.5 14.2h3v4.6h-3zM5.2 10.5h4.6v3H5.2zM14.2 10.5h4.6v3h-4.6z',
    ],
  },
  /*
   * Dos bandas de agua, no tres.
   *
   * Con tres, el espacio entre bandas cae por debajo de dos unidades y a 14 px
   * se rellena: la silueta se vuelve un rectángulo con los bordes ondulados.
   */
  'av-flood': {
    label: 'Inundación',
    paths: [
      'M1.6 6c1.8 0 1.8 2 3.5 2s1.7-2 3.5-2 1.7 2 3.4 2 1.8-2 3.5-2 1.8 2 3.5 2 1.7-2 3.4-2v3.2c-1.7 0-1.7 2-3.4 2s-1.8-2-3.5-2-1.7 2-3.5 2-1.7-2-3.4-2-1.8 2-3.5 2-1.7-2-3.5-2z',
      'M1.6 13.6c1.8 0 1.8 2 3.5 2s1.7-2 3.5-2 1.7 2 3.4 2 1.8-2 3.5-2 1.8 2 3.5 2 1.7-2 3.4-2v3.2c-1.7 0-1.7 2-3.4 2s-1.8-2-3.5-2-1.7 2-3.5 2-1.7-2-3.4-2-1.8 2-3.5 2-1.7-2-3.5-2z',
    ],
  },
}

/**
 * Tipo de incidente → icono.
 *
 * Los tres tipos de fuego comparten glifo a propósito. La diferencia entre un
 * incendio forestal, uno estructural y un «posible incendio» ya está codificada
 * en el color por tramo de confianza y en la ficha; repetirla en la silueta
 * daría tres llamas casi iguales que nadie distinguiría a 12 px.
 */
export const INCIDENT_TYPE_ICON: Record<string, IconId> = {
  possible_fire: 'av-flame',
  wildfire: 'av-flame',
  structural_fire: 'av-flame',
  accident: 'av-crash',
  rescue: 'av-rescue',
  flood: 'av-flood',
  landslide: 'av-flood',
  // `power_outage` no aparece: los cortes de luz se dibujan como marcadores del
  // DOM con su propio pin, fuera del lienzo. Ver `OutagePinLayer`.
  other: 'av-alert',
}

/** Respaldo cuando el tipo no está en el diccionario. */
export const FALLBACK_ICON: IconId = 'av-alert'

/** Icono de las otras dos fuentes, que tienen un único tipo cada una. */
export const SEISMIC_ICON: IconId = 'av-waves'
export const CLOSURE_ICON: IconId = 'av-barrier'
