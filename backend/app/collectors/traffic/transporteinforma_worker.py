"""Transporte Informa (MTT) — avisos en texto libre, georreferenciados en dos pasos.

El problema que resuelve
------------------------
El MTT publica en prosa: *"Accidente vehicular en Av. España con Uno Norte, Viña
del Mar. Tránsito lento hacia el poniente."* Es la fuente oficial más rápida
sobre accidentes en carretera y no entrega ni una coordenada.

Ninguna de las dos soluciones obvias funciona:

* **Expresiones regulares.** El texto no tiene formato fijo. "Av. España con Uno
  Norte", "cruce de Av. España y Uno Norte", "Ruta 68 a la altura del km 42" y
  "acceso sur a Viña" son todas la misma clase de dato escrita de cuatro maneras.
  Una regex que las cubra todas se vuelve inmantenible en un mes.
* **Mandarle el texto crudo a Nominatim.** El geocodificador espera una
  dirección, no una oración. "Accidente vehicular en Av. España con Uno Norte,
  Viña del Mar" no resuelve; "Av. España y Uno Norte, Viña del Mar" sí.

El enfoque híbrido
------------------
**Paso A — extracción (`extract_streets_via_llm`).** Un LLM ligero convierte
prosa en un diccionario limpio: calle, calle transversal, ciudad, región. Es
exactamente la tarea para la que sirve un modelo de lenguaje —comprensión de
texto no estructurado— y exactamente donde NO debe tomar decisiones: no inventa
coordenadas, no juzga gravedad, no decide si es accidente. Sólo extrae.

**Paso B — geocodificación (`app.collectors.nominatim`).** El diccionario limpio
se convierte en una consulta que Nominatim sí entiende, con el rate limit de
1 req/s respetado por un limitador global al proceso.

La división importa porque acota el daño de un error del LLM: lo peor que puede
hacer es devolver una calle mal leída, y eso produce una geocodificación fallida
o un punto discutible —marcado como tal en `raw_data._geocoding`— pero jamás un
accidente inventado ni un punto sin trazabilidad.

Estado de la implementación
---------------------------
`extract_streets_via_llm` está **mockeada**: no llama a ningún modelo. La
heurística que trae es deliberadamente simple y sirve como línea base honesta
para medir contra ella cuando se conecte el modelo real. `_MOCK_MODE` deja
constancia en cada señal (`raw_data._extraction.mode == "mock"`), de modo que
mañana se pueda distinguir en la base qué se extrajo con reglas y qué con el LLM
— sin esa marca, la comparación sería imposible retroactivamente.

Confianza
---------
0.80: un organismo del Estado informando por su canal oficial. Ese número
califica **el hecho**, no el punto. El error de la geocodificación es un eje
aparte y queda en `raw_data._geocoding` (`importance`, `display_name`, consulta
usada) para que un operador pueda mirar el punto y desconfiar de él sin
desconfiar del aviso.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.collectors.base import BaseCollector
from app.collectors.geoservices import normalise_text, parse_timestamp, request_json
from app.collectors.nominatim import GeocodeResult, build_client, geocode
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: Canal oficial del MTT. Ver el docstring: califica el hecho, no el punto.
TRANSPORTE_INFORMA_CONFIDENCE = 0.80

#: Marca que queda en cada señal mientras el LLM sea un mock. Permitirá separar
#: en la base lo extraído con reglas de lo extraído con modelo.
_MOCK_MODE = "mock"

#: Palabras que denotan un siniestro vial. Se comparan sin tildes.
_ACCIDENT_MARKERS: tuple[str, ...] = (
    "accidente",
    "colision",
    "choque",
    "volcamiento",
    "atropello",
    "siniestro",
)

#: Conectores de intersección tal como los escribe el MTT.
_INTERSECTION_SPLIT = re.compile(
    r"\s+(?:con|esquina(?:\s+de)?|/|y)\s+", re.IGNORECASE
)

#: Preposición que introduce el lugar dentro de la oración.
_PLACE_LEAD = re.compile(
    r"\b(?:en|sobre|a la altura de|frente a)\s+(?P<place>.+)", re.IGNORECASE
)

#: Corta el lugar cuando empieza la parte narrativa del aviso.
_PLACE_STOP = re.compile(
    r"\s*[.;]|\s+(?:transito|tránsito|se recomienda|precaucion|precaución|"
    r"personal|carabineros|equipos)\b",
    re.IGNORECASE,
)

#: Abreviaturas que llevan punto y NO terminan la oración. Sin protegerlas,
#: `_PLACE_STOP` corta "Av. España" en "Av" — y como casi toda calle chilena se
#: escribe abreviada, el extractor devolvería basura para la mayoría de los
#: avisos. El punto se sustituye por un centinela antes de buscar el final de la
#: frase y se restaura después.
_ABBREVIATIONS: tuple[str, ...] = (
    "av",
    "avda",
    "gral",
    "pdte",
    "pje",
    "ptje",
    "sta",
    "sto",
    "dr",
    "cnel",
    "cap",
    "km",
    "psje",
)
_ABBREV_SENTINEL = "\x00"
_ABBREV_PATTERN = re.compile(
    r"\b(" + "|".join(_ABBREVIATIONS) + r")\.", re.IGNORECASE
)


def _protect_abbreviations(text: str) -> str:
    return _ABBREV_PATTERN.sub(lambda m: f"{m.group(1)}{_ABBREV_SENTINEL}", text)


def _restore_abbreviations(value: str | None) -> str | None:
    if value is None:
        return None
    restored = value.replace(_ABBREV_SENTINEL, ".").strip(" ,")
    return restored or None

#: Comunas de la V Región. Sirven para separar ciudad de calle cuando el aviso
#: las mezcla en la misma frase. No pretende ser exhaustiva: es la lista de las
#: que aparecen en los avisos de tránsito.
_KNOWN_CITIES: tuple[str, ...] = (
    "valparaiso",
    "vina del mar",
    "quilpue",
    "villa alemana",
    "concon",
    "casablanca",
    "quillota",
    "la calera",
    "san antonio",
    "cartagena",
    "el quisco",
    "algarrobo",
    "limache",
    "olmue",
    "los andes",
    "san felipe",
    "la ligua",
    "petorca",
    "quintero",
    "puchuncavi",
)

_DEFAULT_REGION = "Región de Valparaíso"


@dataclass(frozen=True, slots=True)
class TrafficNotice:
    """Un aviso del MTT, tal como llega."""

    notice_id: str
    text: str
    published_at: datetime | None
    raw: Mapping[str, Any] = field(default_factory=dict)


# --- Paso A: extracción del lugar -------------------------------------------


def extract_streets_via_llm(text: str) -> dict[str, Any]:
    """Prosa → diccionario limpio de lugar. **Mockeada**: no llama a ningún LLM.

    Contrato de salida, que el modelo real deberá respetar tal cual::

        {
          "street":       "Av. España" | None,   # vía principal
          "cross_street": "Uno Norte"  | None,   # transversal, si hay intersección
          "city":         "Viña del Mar" | None,
          "region":       "Región de Valparaíso",
          "is_accident":  True,                  # ¿el aviso describe un siniestro?
          "mode":         "mock",                # "llm" cuando se conecte el modelo
        }

    Cuando se conecte el modelo, el prompt debe ser explícito en tres cosas, que
    son las que hacen que este paso sea seguro:

    1. Devolver **sólo** JSON con esas claves. Nada de prosa explicativa.
    2. `null` antes que adivinar. Una calle inventada produce un punto plausible
       y falso en el mapa, que es mucho peor que un aviso sin ubicación: el
       segundo se ve, el primero no.
    3. No inferir coordenadas jamás. Eso es trabajo de Nominatim, que es
       verificable y auditable; un LLM recitando lat/lon de memoria no lo es.

    La heurística de abajo es la línea base contra la que habrá que medir el
    modelo. Resuelve los avisos con estructura "en <calle> con <calle>, <ciudad>"
    y falla —devolviendo None, que es el fallo correcto— con los tramos de ruta
    por kilómetro.
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return _empty_extraction()

    normalised = normalise_text(cleaned)
    is_accident = any(marker in normalised for marker in _ACCIDENT_MARKERS)

    # A partir de acá se trabaja con los puntos de las abreviaturas neutralizados;
    # se restauran al construir el resultado.
    protected = _protect_abbreviations(cleaned)

    place_match = _PLACE_LEAD.search(protected)
    if not place_match:
        return _empty_extraction(is_accident=is_accident)

    place = place_match.group("place")
    stop = _PLACE_STOP.search(place)
    if stop:
        place = place[: stop.start()]
    place = place.strip(" ,.")

    # La ciudad es el último segmento separado por coma si coincide con una
    # comuna conocida. Sin ese cotejo, "Av. España, sentido poniente" dejaría
    # "sentido poniente" como ciudad y Nominatim buscaría una localidad que no
    # existe.
    city: str | None = None
    segments = [segment.strip() for segment in place.split(",") if segment.strip()]
    if len(segments) > 1 and normalise_text(segments[-1]) in _KNOWN_CITIES:
        city = segments[-1]
        segments = segments[:-1]

    street_part = ", ".join(segments).strip()
    if not street_part:
        return _empty_extraction(is_accident=is_accident, city=city)

    pieces = [piece.strip(" ,.") for piece in _INTERSECTION_SPLIT.split(street_part, 1)]
    street = pieces[0] or None
    cross = pieces[1] if len(pieces) > 1 and pieces[1] else None

    # La ciudad puede venir pegada a la transversal sin coma:
    # "Uno Norte Viña del Mar". Se recorta si el final coincide con una comuna.
    if city is None and cross:
        cross, city = _split_trailing_city(cross)
    if city is None and street:
        street, city = _split_trailing_city(street)

    return {
        "street": _restore_abbreviations(street),
        "cross_street": _restore_abbreviations(cross),
        "city": _restore_abbreviations(city),
        "region": _DEFAULT_REGION,
        "is_accident": is_accident,
        "mode": _MOCK_MODE,
    }


def _empty_extraction(
    *, is_accident: bool = False, city: str | None = None
) -> dict[str, Any]:
    return {
        "street": None,
        "cross_street": None,
        "city": city,
        "region": _DEFAULT_REGION,
        "is_accident": is_accident,
        "mode": _MOCK_MODE,
    }


def _split_trailing_city(value: str) -> tuple[str | None, str | None]:
    """Separa una comuna pegada al final de un nombre de calle."""
    normalised = normalise_text(value)
    for city in sorted(_KNOWN_CITIES, key=len, reverse=True):
        if normalised.endswith(city) and normalised != city:
            cut = len(value) - len(city)
            head = value[:cut].strip(" ,.")
            return (head or None, value[cut:].strip(" ,."))
    return (value, None)


# --- Ingesta -----------------------------------------------------------------


def parse_notice(payload: Any) -> TrafficNotice | None:
    """Normaliza un elemento del feed. None si no trae texto aprovechable."""
    if not isinstance(payload, Mapping):
        return None

    text = ""
    for key in ("text", "titulo", "title", "descripcion", "description", "mensaje"):
        value = payload.get(key)
        if value and str(value).strip():
            text = str(value).strip()
            break
    if not text:
        return None

    notice_id = ""
    for key in ("id", "uuid", "guid", "link", "url"):
        value = payload.get(key)
        if value and str(value).strip():
            notice_id = str(value).strip()
            break

    published_at = None
    for key in ("published_at", "fecha", "date", "pubDate", "created_at"):
        if payload.get(key) is not None:
            published_at = parse_timestamp(payload.get(key))
            if published_at is not None:
                break

    if not notice_id:
        notice_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]

    return TrafficNotice(
        notice_id=notice_id, text=text, published_at=published_at, raw=dict(payload)
    )


class TransporteInformaCollector(BaseCollector):
    """Avisos del MTT: extracción del lugar con LLM y geocodificación con Nominatim.

    Rompe una convención del proyecto y conviene decirlo en voz alta: en el resto
    de los collectors `normalize()` es una función pura y todo el I/O vive en
    `fetch()`. Acá la geocodificación **también ocurre en `fetch()`**, no en
    `normalize()`, precisamente para no romperla. `fetch` devuelve avisos ya
    resueltos a coordenadas y `normalize` sigue siendo pura y testeable sin red.
    """

    name = "transporte_informa"
    source = EventSource.TRANSPORTE_INFORMA
    default_interval_seconds = 600

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.TRANSPORTE_INFORMA_POLL_INTERVAL_SECONDS

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        if not settings.TRANSPORTE_INFORMA_URL.strip():
            raise CollectorError(
                "TRANSPORTE_INFORMA_URL no está configurada; el collector no "
                "tiene de dónde leer."
            )
        self.url = settings.TRANSPORTE_INFORMA_URL.strip()
        self.max_geocodes = settings.TRANSPORTE_INFORMA_MAX_GEOCODES

    def run_params(self) -> dict[str, Any]:
        return {
            "max_geocodes": self.max_geocodes,
            "extraction_mode": _MOCK_MODE,
            "nominatim_min_interval_s": settings.NOMINATIM_MIN_INTERVAL_SECONDS,
        }

    async def fetch(self) -> Sequence[tuple[TrafficNotice, dict[str, Any], Any]]:
        """Trae los avisos, extrae el lugar y geocodifica los que son accidentes.

        Devuelve tripletas `(aviso, extracción, geocodificación|None)`.
        """
        async with httpx.AsyncClient(
            timeout=settings.TRANSPORTE_INFORMA_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            payload = await request_json(
                client, self.url, {}, origin="transporte_informa"
            )

        items = payload if isinstance(payload, list) else None
        if items is None and isinstance(payload, Mapping):
            for key in ("items", "avisos", "results", "data"):
                if isinstance(payload.get(key), list):
                    items = payload[key]
                    break
        if items is None:
            claves = sorted(payload)[:10] if isinstance(payload, Mapping) else "—"
            raise CollectorError(
                "transporte_informa: no se encontró una lista de avisos en la "
                f"respuesta (claves: {claves})"
            )

        notices = [notice for item in items if (notice := parse_notice(item))]

        resolved: list[tuple[TrafficNotice, dict[str, Any], GeocodeResult | None]] = []
        geocoded = 0

        # Un solo cliente para todas las llamadas a Nominatim: reutiliza la
        # conexión TLS y, sobre todo, mantiene un único User-Agent identificable,
        # que es parte del contrato de uso del servicio.
        async with build_client() as geo_client:
            for notice in notices:
                streets = extract_streets_via_llm(notice.text)

                if not streets.get("is_accident"):
                    # El MTT publica también cortes programados y desvíos. No son
                    # siniestros y no deben entrar a la capa de accidentes.
                    continue

                point: GeocodeResult | None = None
                if geocoded < self.max_geocodes and streets.get("street"):
                    try:
                        point = await geocode(geo_client, streets)
                        geocoded += 1
                    except CollectorError as exc:
                        # Una geocodificación fallida NO pierde el aviso: la señal
                        # entra sin coordenadas. Perder un accidente confirmado por
                        # el MTT porque OpenStreetMap no conoce una esquina sería
                        # el peor intercambio posible.
                        self.warn(f"Nominatim falló para un aviso: {exc}")
                elif geocoded >= self.max_geocodes:
                    self.warn(
                        f"se alcanzó el tope de {self.max_geocodes} geocodificaciones "
                        f"por corrida; el resto queda sin coordenadas"
                    )

                resolved.append((notice, streets, point))

        logger.info(
            "avisos del MTT procesados",
            extra={
                "collector": self.name,
                "avisos": len(notices),
                "accidentes": len(resolved),
                "geocodificados": geocoded,
            },
        )
        return resolved

    def normalize(
        self, records: Sequence[tuple[TrafficNotice, dict[str, Any], Any]]
    ) -> list[EventCreate]:
        now = datetime.now(UTC)
        events: list[EventCreate] = []
        sin_punto = 0

        for notice, streets, point in records:
            timestamp = notice.published_at or now
            if timestamp > now:
                timestamp = now
            if point is None:
                sin_punto += 1

            events.append(
                EventCreate(
                    timestamp=timestamp,
                    source=EventSource.TRANSPORTE_INFORMA,
                    type=EventType.ACCIDENT,
                    lat=point.lat if point else None,
                    lon=point.lon if point else None,
                    text=notice.text[:10_000],
                    external_id=f"mtt:{notice.notice_id}",
                    confidence=TRANSPORTE_INFORMA_CONFIDENCE,
                    raw_data={
                        **dict(notice.raw),
                        "comuna": streets.get("city"),
                        "_collector": self.name,
                        # Los dos pasos quedan separados y auditables: qué leyó el
                        # extractor y qué resolvió el geocodificador. Si mañana un
                        # punto está mal, esto dice cuál de los dos falló.
                        "_extraction": streets,
                        "_geocoding": point.as_dict() if point else None,
                    },
                )
            )

        if sin_punto:
            self.warn(
                f"{sin_punto} avisos quedaron sin coordenadas; no entran al Paso A "
                f"del motor pero sí quedan registrados"
            )
        return events


__all__ = [
    "TRANSPORTE_INFORMA_CONFIDENCE",
    "TrafficNotice",
    "TransporteInformaCollector",
    "extract_streets_via_llm",
    "parse_notice",
]
