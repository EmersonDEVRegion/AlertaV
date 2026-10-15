/**
 * Las fuentes de AlertaV, para la página «Fuentes» del menú.
 *
 * Una sola lista y no texto suelto en el componente: si mañana entra o sale una
 * fuente, se cambia acá y la página la sigue. Cada fila dice qué se toma de la
 * fuente, porque «usamos datos de X» no le sirve a nadie para saber qué tanto
 * fiarse de lo que ve.
 */

interface DataSource {
  name: string
  url: string
  /** Qué toma AlertaV de esta fuente, en una frase. */
  what: string
}

export interface SourceGroup {
  title: string
  sources: readonly DataSource[]
}

export const SOURCE_GROUPS: readonly SourceGroup[] = [
  {
    title: 'Emergencias',
    sources: [
      {
        name: 'Bomberos de la región',
        url: 'https://x.com/CGI_CBV',
        what:
          'Despachos que publican en X las centrales de Valparaíso (@CGI_CBV), Viña del Mar (@CBVM132), Los Andes y Calle Larga (@despachoscbla), Quilpué (@CBQuilpue) y Quillota (@cbquillota).',
      },
      {
        name: 'SIG Bomberos de Chile',
        url: 'https://sig.bomberos.cl/',
        what: 'Ubicación de cuarteles y compañías.',
      },
      {
        name: 'CONAF',
        url: 'https://sit.conaf.cl/',
        what: 'Incendios forestales en combate, controlados y extinguidos.',
      },
      {
        name: 'SENAPRED',
        url: 'https://www.senapred.cl/',
        what: 'Alertas vigentes (temprana preventiva, amarilla y roja).',
      },
      {
        name: 'NASA FIRMS',
        url: 'https://firms.modaps.eosdis.nasa.gov/',
        what: 'Anomalías térmicas detectadas por satélite. Corroboran; no confirman un incendio por sí solas.',
      },
    ],
  },
  {
    title: 'Sismos',
    sources: [
      {
        name: 'Centro Sismológico Nacional',
        url: 'https://www.sismologia.cl/',
        what: 'Sismos informados y el modelo de amenaza sísmica de la capa de referencia.',
      },
      {
        name: 'USGS',
        url: 'https://earthquake.usgs.gov/',
        what: 'Sismos, como respaldo del CSN.',
      },
    ],
  },
  {
    title: 'Tránsito y rutas',
    sources: [
      {
        name: 'Transporte Informa (MTT)',
        url: 'https://valparaiso.transporteinforma.cl/estado-de-la-movilidad/',
        what: 'Accidentes, desvíos y cortes de tránsito, también desde @TTIValparaiso en X.',
      },
      {
        name: 'Vialidad (MOP)',
        url: 'https://www.vialidad.cl/',
        what: 'Emergencias y cortes en la red vial.',
      },
    ],
  },
  {
    title: 'Servicios',
    sources: [
      {
        name: 'Chilquinta',
        url: 'https://mapainterrupciones.chilquinta.cl/',
        what: 'Cortes de luz.',
      },
      {
        name: 'CGE',
        url: 'https://www.cge.cl/',
        what: 'Cortes de luz.',
      },
      {
        name: 'Esval',
        url: 'https://www.esval.cl/',
        what: 'Cortes de agua y sus zonas.',
      },
    ],
  },
  {
    title: 'Clima',
    sources: [
      {
        name: 'Open-Meteo',
        url: 'https://open-meteo.com/',
        what: 'Pronóstico por comuna: viento, humedad, temperatura, lluvia e índice UV. Con él se calcula la condición de propagación (regla 30-30-30).',
      },
    ],
  },
  {
    title: 'Prensa local',
    sources: [
      {
        name: 'Alerta Noticias',
        url: 'https://alertanoticias.cl/',
        what: 'Noticias de la región (se muestran aparte, no crean incidentes).',
      },
      {
        name: 'Pura Noticia',
        url: 'https://puranoticia.pnt.cl/',
        what: 'Noticias de la región.',
      },
      {
        name: 'Prensa Marga Marga',
        url: 'https://prensamargamarga.cl/',
        what: 'Noticias de la provincia de Marga Marga.',
      },
    ],
  },
  {
    title: 'Vehículos',
    sources: [
      {
        name: 'GBV — Grupo Búsqueda de Vehículos',
        url: 'https://gbvspa.cl/',
        what: 'Denuncias de vehículos robados, recuperados y abandonados de las últimas 48 h. Cada ficha enlaza a la original en GBV.',
      },
    ],
  },
  {
    title: 'Mapa y ubicación',
    sources: [
      {
        name: 'OpenStreetMap',
        url: 'https://www.openstreetmap.org/copyright',
        what: 'Cartografía, búsqueda de direcciones (Nominatim) y cruces de calles (Overpass).',
      },
      {
        name: 'CARTO',
        url: 'https://carto.com/attributions',
        what: 'Estilo del mapa base.',
      },
    ],
  },
]

/** Correo de contacto del proyecto. */
export const CONTACT_EMAIL = 'emerson.alcorce.silva@gmail.com'

/** Repositorio público. */
export const REPO_URL = 'https://github.com/EmersonDEVRegion/AlertaV'
