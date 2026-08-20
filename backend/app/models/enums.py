"""Enumeraciones de dominio.

La mayoría de estos valores deben coincidir exactamente con los tipos ENUM de
PostgreSQL definidos en `sql/001_schema.sql`. Agregar un valor requiere una
migración con `ALTER TYPE ... ADD VALUE`.

Las excepciones —`ConfidenceLevel`, `SOURCE_BASE_CONFIDENCE`, `INCIDENT_FAMILY`—
no son columnas: son vocabulario derivado. Viven acá porque `app.schemas` y
`app.services` los necesitan a los dos lados, y `app.models` es la única capa que
ambos pueden importar sin invertir la dependencia. Ponerlos en `services` creaba
un ciclo real (`schemas.incident` → `services.correlation` → `services.__init__`
→ `services.incident_service` → `schemas.incident`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

#: Familia de fenómeno por defecto. Se declara acá arriba —lejos de
#: `INCIDENT_FAMILY`, que es donde se la esperaría— porque `style_for` la usa
#: como valor por defecto de un parámetro, y eso se evalúa al importar el
#: módulo, no al llamar la función.
DEFAULT_FAMILY = "other"


class EventSource(str, Enum):
    """Origen de la señal."""

    CITIZEN = "citizen"
    BROADCASTIFY = "broadcastify"
    NASA_FIRMS = "nasa_firms"
    CONAF = "conaf"
    SENAPRED = "senapred"
    BOMBEROS = "bomberos"
    MUNICIPALITY = "municipality"
    MEDIA = "media"
    SOCIAL_MEDIA = "social_media"
    WEATHER = "weather"
    CAMERA = "camera"
    USGS = "usgs"
    #: Centro Sismológico Nacional (U. de Chile). La red oficial chilena: su
    #: umbral de detección baja muy por debajo del feed global del USGS, que en
    #: Chile ignora prácticamente todo lo menor a M4.5.
    CSN = "csn"
    #: Reportes de tránsito de la comunidad de Waze (feed CCP). Son avisos de
    #: conductores: masivos y rápidos, pero sin verificar por nadie.
    WAZE = "waze"
    #: Cuenta oficial del Ministerio de Transportes. Publica en texto libre, sin
    #: coordenadas: la georreferenciación es reconstruida, no informada.
    TRANSPORTE_INFORMA = "transporte_informa"
    #: Distribuidoras eléctricas de la V Región. Sobre SU red son la autoridad:
    #: nadie sabe mejor que ellas dónde no hay luz.
    CHILQUINTA = "chilquinta"
    CGE = "cge"
    OTHER = "other"


class EventType(str, Enum):
    """Naturaleza de la señal.

    Distinción crítica del proyecto:
      - THERMAL_ANOMALY: detección satelital. NO es un incendio confirmado.
      - SMOKE: avistamiento. NO es un incendio confirmado.
      - WILDFIRE / STRUCTURAL_FIRE: sólo cuando la fuente lo confirma.
      - EARTHQUAKE: medición instrumental de una red sismológica. Es un hecho
        confirmado, pero NO es un siniestro: es la causa posible de varios.
    """

    WILDFIRE = "wildfire"
    STRUCTURAL_FIRE = "structural_fire"
    SMOKE = "smoke"
    THERMAL_ANOMALY = "thermal_anomaly"
    DISPATCH = "dispatch"
    ALERT = "alert"
    EVACUATION = "evacuation"
    RESCUE = "rescue"
    ACCIDENT = "accident"
    FLOOD = "flood"
    LANDSLIDE = "landslide"
    EARTHQUAKE = "earthquake"
    POWER_OUTAGE = "power_outage"
    WEATHER_OBSERVATION = "weather_observation"
    OTHER = "other"
    UNKNOWN = "unknown"


class CollectorStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class IncidentType(str, Enum):
    """Naturaleza del FENÓMENO consolidado.

    Deliberadamente NO es `EventType`. Un incidente representa un hecho del
    mundo, no la respuesta administrativa a ese hecho: por eso aquí no existen
    `alert` ni `evacuation`. Una alerta de SENAPRED se adjunta a un incidente y
    fija su `alert_level`; jamás lo tipifica.

    `POSSIBLE_FIRE` es la pieza que evita el falso positivo más caro del
    sistema: un racimo de detecciones satelitales o de avistamientos de humo es
    exactamente eso —un posible incendio— y pintarlo como `WILDFIRE` en el mapa
    sería afirmar algo que ninguna fuente confirmó.
    """

    POSSIBLE_FIRE = "possible_fire"
    WILDFIRE = "wildfire"
    STRUCTURAL_FIRE = "structural_fire"
    FLOOD = "flood"
    LANDSLIDE = "landslide"
    ACCIDENT = "accident"
    RESCUE = "rescue"
    POWER_OUTAGE = "power_outage"
    OTHER = "other"


class IncidentStatus(str, Enum):
    """Ciclo de vida del incidente.

    `STALE` no es lo mismo que `EXTINGUISHED`: significa que dejaron de llegar
    señales, no que alguien haya declarado el fin de la emergencia. Confundirlos
    sería inventar un dato institucional que nadie entregó.

    `MERGED` existe porque dos racimos que crecen pueden terminar siendo el
    mismo incendio. En vez de borrar el más nuevo se lo marca y se apunta a su
    sucesor: los enlaces históricos siguen siendo navegables.
    """

    ACTIVE = "active"
    CONTROLLED = "controlled"
    EXTINGUISHED = "extinguished"
    STALE = "stale"
    MERGED = "merged"
    DISMISSED = "dismissed"


#: Estados en los que el incidente sigue siendo relevante para el mapa.
OPEN_INCIDENT_STATUSES: frozenset[IncidentStatus] = frozenset(
    {IncidentStatus.ACTIVE, IncidentStatus.CONTROLLED}
)


class ConfidenceLevel(str, Enum):
    """Tramo de confianza del incidente. Es lo que decide el color del mapa.

    **No confundir con `is_official_confirmed`.** `CONFIRMED` acá significa
    "la evidencia acumulada supera el 60 %", que es un juicio del motor;
    `is_official_confirmed` significa "CONAF o Bomberos fueron al lugar", que es
    un hecho institucional. Un racimo de despachos radiales llega a `CONFIRMED`
    con `is_official_confirmed = False`, y esa diferencia tiene que seguir siendo
    legible en la UI: son las dos preguntas distintas que el sistema responde.

    No es una columna: se recalcula desde `confidence` en cada lectura. Guardarlo
    sería tener el mismo dato en dos sitios con dos políticas distintas.
    """

    #: < 30 %. Señal aislada o ruido. Rojo de ADVERTENCIA, no de emergencia.
    UNSAFE = "unsafe"
    #: 30 % – 60 %. Hay algo, no sabemos qué. Amarillo.
    POSSIBLE = "possible"
    #: > 60 %. Naranja/fuego.
    CONFIRMED = "confirmed"


#: Cortes de los tramos. Se declaran una sola vez, acá, porque el color del mapa,
#: la etiqueta de la tarjeta y el filtro de la API tienen que salir del mismo
#: número o el operador verá tres verdades distintas sobre el mismo incidente.
UNSAFE_THRESHOLD = 0.30
CONFIRMED_THRESHOLD = 0.60

#: Tolerancia con la que se comparan dos confianzas **en SQL**.
#:
#: `incidents.confidence` y `raw_events.confidence` son columnas `REAL` —float4,
#: 24 bits de mantisa— mientras que el motor calcula y compara en float8. La
#: conversión no es simétrica: 0.40 escrito desde Python vuelve de la base como
#: 0.4000000059604645, que es **estrictamente mayor** que 0.40. Un
#: `WHERE confidence <= 0.40` no matchea entonces con el mismo 0.40 que el motor
#: acaba de escribir, y el `UPDATE` cuenta 0 filas sin error ninguno.
#:
#: No es teórico: es exactamente lo que dejó vivos indefinidamente a los reportes
#: ciudadanos que la regla de muerte súbita tenía que descartar a los 5 minutos.
#: El defecto es simétrico y muerde igual en la otra dirección — un filtro
#: `confidence >= 0.35` esconde los incidentes que valen exactamente 0.35,
#: porque float4 los guarda como 0.3499999940395355.
#:
#: 1e-6 está dos órdenes de magnitud por encima del error de float4 en [0,1]
#: (≈6e-8) y tres por debajo de la resolución que la política de confianza tiene
#: de verdad: `score()` redondea a 4 decimales y ninguna decisión del sistema
#: distingue 0.400000 de 0.400001. Comparar con esta tolerancia no afloja la
#: regla, sólo deja de exigirle a un float4 una exactitud que no tiene.
CONFIDENCE_EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class LevelStyle:
    label: str
    color: str
    meaning: str


#: Estilos **neutrales respecto del fenómeno**. Son la base y el fallback.
#:
#: La etiqueta de `CONFIRMED` decía "Incendio confirmado" desde que el sistema
#: sólo veía incendios, y siguió diciéndolo cuando empezaron a entrar accidentes
#: viales: la API rotulaba "Incendio confirmado" un choque en la Ruta 68 y el
#: frontend no tenía más remedio que descartar la etiqueta y fabricar la suya.
#:
#: El default es genérico a propósito, y esa es la decisión de diseño de este
#: bloque: si alguien llama a `style_for` sin familia, o llega una familia nueva
#: que nadie mapeó, el resultado es "Emergencia confirmada" —vago pero cierto—
#: en vez de una afirmación falsa sobre un fenómeno que no ocurrió. El modo de
#: fallo apunta hacia lo impreciso, nunca hacia lo incorrecto.
LEVEL_STYLES: dict[ConfidenceLevel, LevelStyle] = {
    ConfidenceLevel.UNSAFE: LevelStyle(
        label="Baja confianza",
        color="#dc2626",
        meaning="Señal aislada sin corroborar. Puede ser ruido o spam.",
    ),
    ConfidenceLevel.POSSIBLE: LevelStyle(
        label="Posible emergencia",
        color="#eab308",
        meaning="Hay evidencia, no alcanza para afirmar que haya una emergencia.",
    ),
    ConfidenceLevel.CONFIRMED: LevelStyle(
        label="Emergencia confirmada",
        color="#ea580c",
        meaning="Evidencia acumulada por sobre el 60 %.",
    ),
}

#: Etiqueta de `CONFIRMED` por familia de fenómeno. Sólo este tramo cambia de
#: sustantivo: `UNSAFE` ("Baja confianza") y `POSSIBLE` ("Posible emergencia")
#: ya son neutros y sirven igual para un incendio que para un choque.
CONFIRMED_LABEL_BY_FAMILY: dict[str, str] = {
    "fire": "Incendio confirmado",
    "traffic": "Accidente confirmado",
    "hydro": "Emergencia confirmada",
    "power": "Corte de suministro confirmado",
    "other": "Emergencia confirmada",
}

#: Lo que el tramo `POSSIBLE` significa en cada familia. La frase original
#: —"no alcanza para afirmar que hay fuego"— es el mismo defecto que la etiqueta
#: quemada, sólo que en el campo de al lado.
POSSIBLE_MEANING_BY_FAMILY: dict[str, str] = {
    "fire": "Hay evidencia, no alcanza para afirmar que hay fuego.",
    "traffic": "Hay evidencia, no alcanza para afirmar que hubo un accidente.",
    "hydro": "Hay evidencia, no alcanza para afirmar que hay una emergencia.",
    "power": "Hay evidencia, no alcanza para afirmar que haya un corte.",
    "other": "Hay evidencia, no alcanza para afirmar que hay una emergencia.",
}


def style_for(level: ConfidenceLevel, family: str = DEFAULT_FAMILY) -> LevelStyle:
    """Estilo de un tramo de confianza para una familia de fenómeno.

    El color no depende de la familia: lo fija el nivel de certeza y sólo el
    nivel. Que un accidente confirmado y un incendio confirmado compartan el
    mismo naranja es intencional — el color comunica *cuánto sabemos*, y la
    forma del ícono y la etiqueta comunican *de qué se trata*. Mezclar ambas
    dimensiones en el color obligaría a leer una leyenda para saber si algo es
    urgente.
    """
    base = LEVEL_STYLES[level]
    if level is ConfidenceLevel.CONFIRMED:
        return replace(base, label=confirmed_label_for(family))
    if level is ConfidenceLevel.POSSIBLE:
        return replace(
            base,
            meaning=POSSIBLE_MEANING_BY_FAMILY.get(
                family, POSSIBLE_MEANING_BY_FAMILY[DEFAULT_FAMILY]
            ),
        )
    return base


def confirmed_label_for(family: str) -> str:
    """Etiqueta del tramo `CONFIRMED`. Genérica ante una familia desconocida."""
    return CONFIRMED_LABEL_BY_FAMILY.get(
        family, CONFIRMED_LABEL_BY_FAMILY[DEFAULT_FAMILY]
    )


def label_for(
    confidence: float, family: str = DEFAULT_FAMILY
) -> str:
    """Atajo: confianza + familia → etiqueta legible del tramo.

    Es lo que consumen el schema de salida y el GeoJSON, para que la etiqueta se
    derive siempre del mismo sitio y no haya dos formas de calcularla.
    """
    return style_for(level_for(confidence), family).label


def level_for(confidence: float) -> ConfidenceLevel:
    """Tramo de una confianza.

    Los bordes están fijados como los describe la política: 0.30 ya es
    `POSSIBLE` y 0.60 exacto **todavía** lo es. Sólo se cruza a `CONFIRMED`
    *por encima* de 0.60, para que un empate en el borde no ascienda solo.
    """
    if confidence < UNSAFE_THRESHOLD:
        return ConfidenceLevel.UNSAFE
    if confidence > CONFIRMED_THRESHOLD:
        return ConfidenceLevel.CONFIRMED
    return ConfidenceLevel.POSSIBLE


class LinkMethod(str, Enum):
    """Por qué una señal quedó unida a un incidente.

    Guardar el método —y no sólo el vínculo— es lo que permite auditar el motor:
    un `COMMUNE_TEXT` es una heurística sobre texto y merece un escrutinio
    distinto al de un `SPATIAL`, que es una coincidencia geométrica medible.
    """

    SPATIAL = "spatial"
    COMMUNE_TEXT = "commune_text"
    MANUAL = "manual"


#: Tipos de señal que el Paso A puede agrupar geométricamente.
#: Se excluyen los actos administrativos (`alert`, `evacuation`), que entran por
#: el Paso B, y el contexto meteorológico, que no es evidencia de emergencia.
#:
#: `earthquake` queda fuera por una razón distinta a las anteriores y que conviene
#: dejar escrita: no es que sea poco confiable —es la señal más confiable que
#: entra al sistema—, es que el motor está construido para resolver *incertidumbre
#: por corroboración*, y un sismo no tiene ninguna que resolver. Agruparlo haría
#: daño: el radio de 1500 m y la ventana de 4 h son exactamente la escala de una
#: réplica, así que DBSCAN fusionaría el sismo principal con sus réplicas en un
#: solo "incidente" y borraría la secuencia, que es el dato sismológico relevante.
#: Un sismo es contexto —causa posible de incendios, derrumbes o tsunami—, no un
#: siniestro con ubicación puntual en el mapa. Mismo tratamiento que `weather_observation`.
CORRELATABLE_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.WILDFIRE,
        EventType.STRUCTURAL_FIRE,
        EventType.SMOKE,
        EventType.THERMAL_ANOMALY,
        EventType.DISPATCH,
        EventType.RESCUE,
        EventType.ACCIDENT,
        EventType.POWER_OUTAGE,
        EventType.FLOOD,
        EventType.LANDSLIDE,
        EventType.OTHER,
        EventType.UNKNOWN,
    }
)

#: Traducción señal → fenómeno. `smoke` y `thermal_anomaly` degradan a
#: `possible_fire`: son indicios, no confirmaciones.
EVENT_TO_INCIDENT_TYPE: dict[EventType, IncidentType] = {
    EventType.WILDFIRE: IncidentType.WILDFIRE,
    EventType.STRUCTURAL_FIRE: IncidentType.STRUCTURAL_FIRE,
    EventType.SMOKE: IncidentType.POSSIBLE_FIRE,
    EventType.THERMAL_ANOMALY: IncidentType.POSSIBLE_FIRE,
    EventType.FLOOD: IncidentType.FLOOD,
    EventType.LANDSLIDE: IncidentType.LANDSLIDE,
    EventType.ACCIDENT: IncidentType.ACCIDENT,
    EventType.POWER_OUTAGE: IncidentType.POWER_OUTAGE,
    EventType.RESCUE: IncidentType.RESCUE,
    EventType.DISPATCH: IncidentType.OTHER,
    EventType.OTHER: IncidentType.OTHER,
    EventType.UNKNOWN: IncidentType.OTHER,
}

#: Familias de fenómeno. El Paso B sólo une una alerta a un incidente si ambos
#: pertenecen a la misma familia: una alerta roja por crecida no debe adosarse a
#: un incendio que casualmente ocurre en la misma comuna.
INCIDENT_FAMILY: dict[IncidentType, str] = {
    IncidentType.POSSIBLE_FIRE: "fire",
    IncidentType.WILDFIRE: "fire",
    IncidentType.STRUCTURAL_FIRE: "fire",
    IncidentType.FLOOD: "hydro",
    IncidentType.LANDSLIDE: "hydro",
    # `traffic` es familia propia desde que existe la capa de accidentes viales.
    # Antes compartía "other" con rescates y despachos genéricos, y eso bastaba
    # mientras nadie reportara accidentes: hoy sería el camino por el cual un
    # choque en la Ruta 68 termina fundido con un despacho de origen desconocido
    # ocurrido a 800 m. Ver `FAMILY_OF_EVENT` más abajo.
    IncidentType.ACCIDENT: "traffic",
    # `power` es familia propia por la misma razón que `traffic`: un corte de
    # luz y un incendio pueden coincidir en la misma manzana —de hecho suelen
    # hacerlo, porque un incendio derriba la línea— y siguen siendo dos hechos
    # distintos que el mapa debe poder mostrar por separado. Fundirlos convertiría
    # "hay un incendio" y "800 casas sin luz" en un solo punto que no describe
    # ninguna de las dos cosas.
    IncidentType.POWER_OUTAGE: "power",
    IncidentType.RESCUE: "other",
    IncidentType.OTHER: "other",
}

#: `DEFAULT_FAMILY` es la familia de quien no esté mapeado acá. Vive al principio
#: del módulo por una restricción de evaluación; ver el comentario allá arriba.
#: Que sea una constante y no un literal suelto importa: el SQL de la partición y
#: el filtro en memoria tienen que caer en el mismo valor o el motor agruparía
#: distinto de lo que dice esta tabla.


def family_of_incident(incident_type: IncidentType) -> str:
    return INCIDENT_FAMILY.get(incident_type, DEFAULT_FAMILY)


def family_of_event(event_type: EventType) -> str:
    """Familia de fenómeno a la que pertenece una señal cruda.

    Es la clave por la que el Paso A particiona antes de medir distancias. Un
    `smoke` y un `wildfire` caen ambos en `fire` y por eso siguen corroborándose
    —que es de lo que vive este sistema—, mientras que un `accident` cae en
    `traffic` y no puede fundirse con ninguno de los dos por más que compartan
    coordenada y minuto.

    Los tipos que no producen incidente (`alert`, `evacuation`, `earthquake`,
    `weather_observation`) no llegan nunca acá: quedan fuera de
    `CORRELATABLE_EVENT_TYPES`. Si alguno llegara, cae en la familia por defecto
    en vez de reventar.
    """
    incident_type = EVENT_TO_INCIDENT_TYPE.get(event_type)
    if incident_type is None:
        return DEFAULT_FAMILY
    return family_of_incident(incident_type)


#: Confianza base por fuente. Espejo de `alertav.source_confidence`; sirve como
#: fallback en memoria cuando la fuente no entrega confianza propia.
#: Calibrar con datos reales tras la ventana de recolección.
SOURCE_BASE_CONFIDENCE: dict[EventSource, float] = {
    EventSource.BOMBEROS: 1.00,
    EventSource.SENAPRED: 1.00,
    EventSource.CONAF: 1.00,
    # Red sismológica global. Mide un fenómeno físico con instrumentos: que el
    # sismo ocurrió no está en duda. Su 1.0 dice eso y sólo eso; no dice que
    # haya un siniestro en ese punto.
    EventSource.USGS: 1.00,
    # Misma lectura que USGS: certeza sobre el HECHO del sismo, no sobre que
    # haya una emergencia en ese punto.
    EventSource.CSN: 1.00,
    # La distribuidora es la autoridad sobre su propia red. No "fue al lugar a
    # constatar" como CONAF: es que el corte lo registra su propio sistema.
    EventSource.CHILQUINTA: 1.00,
    EventSource.CGE: 1.00,
    EventSource.MUNICIPALITY: 0.90,
    EventSource.MEDIA: 0.70,
    EventSource.BROADCASTIFY: 0.65,
    EventSource.NASA_FIRMS: 0.55,
    EventSource.CITIZEN: 0.50,
    EventSource.CAMERA: 0.50,
    EventSource.SOCIAL_MEDIA: 0.45,
    # Organismo del Estado informando por su canal oficial. No va al lugar a
    # constatar —por eso no llega a 1.0 como Bomberos o CONAF— pero lo que
    # publica es institucional.
    EventSource.TRANSPORTE_INFORMA: 0.80,
    # Reporte de un conductor sin verificar. Vale más que una red social genérica
    # porque el aviso es contemporáneo y georreferenciado por el propio GPS del
    # teléfono, y menos que un ciudadano que llama a reportar: en Waze basta
    # pulsar un botón, y los falsos positivos por congestión son habituales.
    EventSource.WAZE: 0.40,
    EventSource.OTHER: 0.30,
    EventSource.WEATHER: 0.10,
}
