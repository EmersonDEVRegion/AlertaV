"""Despachos de Bomberos — clave 10-4, rescate vehicular.

Qué es una clave 10-4 dentro de este sistema
---------------------------------------------
La confirmación más fuerte que puede recibir la capa de accidentes. Una 10-4 no
es un aviso de que "puede haber" un choque: es la central despachando carros
porque hay gente atrapada en un vehículo. Alguien con autoridad ya decidió que
el hecho es real y comprometió recursos.

Por eso `EventSource.BOMBEROS` es `confirming=True` con peso 1.00 en
`confidence.py`, y una sola 10-4 lleva el incidente a certeza. Es la misma regla
que ya aplicaba a los incendios: el organismo que va al lugar *es* la
confirmación del hecho.

Por qué es un scraper y no un cliente de API
---------------------------------------------
No hay API. Cada Cuerpo de Bomberos publica sus despachos en su propio portal, en
HTML pensado para leerse con los ojos. Eso trae dos consecuencias que este módulo
asume de frente en vez de disimular:

* **El parseo es frágil por naturaleza.** Un rediseño del portal lo rompe. La
  respuesta no es blindarlo con más selectores sino hacer que el fallo sea
  ruidoso: si la página se lee pero no aparece ni un despacho, se registra una
  degradación (`partial`) en `collector_runs`. Un scraper que reporta `success`
  con cero filas es indistinguible de una noche tranquila, y esa ambigüedad es
  exactamente lo que hace que un hueco de datos pase semanas sin detectarse.
* **La dirección viene en texto libre y aproximada.** "Ruta 68 km 42",
  "Av. Argentina con Pedro Montt". Este worker NO geocodifica: emite la señal con
  su dirección en `raw_data` y sin coordenadas. Geocodificar acá significaría
  meter una llamada de red por despacho dentro del rate limit de Nominatim, y una
  10-4 vale por su certeza sobre el hecho, no por la precisión del punto.

Consecuencia de lo anterior, escrita para que nadie la descubra depurando: una
señal sin `lat`/`lon` **no entra al Paso A** —el motor sólo agrupa lo que tiene
geometría— pero sí queda registrada, consultable y contabilizada. Si el
emparejamiento por texto con los accidentes de Waze resulta necesario, el lugar
correcto es el Paso B, junto a las alertas comunales de SENAPRED.

Idempotencia
------------
El portal no entrega ids. `external_id` es un hash determinista de
`(clave, dirección, fecha-hora)`: releer la misma página cada 3 minutos
actualiza la fila en vez de duplicar el despacho.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.collectors.base import BaseCollector
from app.collectors.geoservices import normalise_text, parse_timestamp
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: Certeza institucional: la central despachó carros. Coincide con
#: SOURCE_BASE_CONFIDENCE[BOMBEROS] y con el peso 1.0 pedido para esta capa.
BOMBEROS_CONFIDENCE = 1.0

#: Filas de la tabla de despachos. Se acepta cualquier contenedor con celdas
#: porque los portales varían: unos usan <table>, otros <div class="row">.
_ROW_PATTERN = re.compile(
    r"<(?:tr|li)\b[^>]*>(?P<body>.*?)</(?:tr|li)>", re.IGNORECASE | re.DOTALL
)
_CELL_PATTERN = re.compile(
    r"<(?:td|th|span|div)\b[^>]*>(?P<cell>.*?)</(?:td|th|span|div)>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")

#: Fecha y hora dentro del texto de la fila. Se aceptan los formatos que usan los
#: portales chilenos: dd-mm-aaaa y dd/mm/aaaa, con hora opcional.
_DATETIME_PATTERN = re.compile(
    r"(?P<date>\d{1,2}[-/]\d{1,2}[-/]\d{2,4})(?:[\sT]+(?P<time>\d{1,2}:\d{2}(?::\d{2})?))?"
)

#: Etiquetas que preceden a la dirección en los portales observados.
_ADDRESS_MARKERS: tuple[str, ...] = ("direccion", "dirección", "lugar", "ubicacion")


@dataclass(frozen=True, slots=True)
class Dispatch:
    """Un despacho ya extraído del HTML."""

    key: str
    address: str | None
    occurred_at: datetime | None
    commune: str | None
    raw_text: str


def strip_html(fragment: str) -> str:
    """HTML → texto plano normalizado en espacios."""
    return _WHITESPACE.sub(" ", _TAG_PATTERN.sub(" ", fragment)).strip()


def matches_key(text: str, keys: Sequence[str]) -> str | None:
    """Devuelve la clave encontrada en el texto, o None.

    Se compara sobre el texto normalizado sin tildes y se exige que la clave no
    esté pegada a otro dígito: sin ese cuidado, "10-40" contendría "10-4" y un
    despacho por emanación de gas entraría al sistema como accidente vehicular.
    """
    haystack = normalise_text(text)
    for key in keys:
        needle = normalise_text(key)
        if not needle:
            continue
        pattern = rf"(?<![0-9]){re.escape(needle)}(?![0-9])"
        if re.search(pattern, haystack):
            return key
    return None


def extract_address(cells: Sequence[str]) -> str | None:
    """Dirección aproximada del despacho.

    Dos estrategias, en orden. Primero la explícita: una celda rotulada
    "Dirección" o "Lugar". Si el portal no rotula nada, se cae a la heurística de
    tomar la celda más larga que no sea la clave ni la fecha — en las tablas de
    despacho la dirección es, casi siempre, el campo con más texto.
    """
    for index, cell in enumerate(cells):
        lowered = normalise_text(cell)
        if any(marker in lowered for marker in (normalise_text(m) for m in _ADDRESS_MARKERS)):
            # La etiqueta puede estar en la misma celda ("Dirección: Ruta 68")
            # o en la anterior ("Dirección" | "Ruta 68").
            if ":" in cell:
                candidate = cell.split(":", 1)[1].strip()
                if candidate:
                    return candidate
            if index + 1 < len(cells) and cells[index + 1].strip():
                return cells[index + 1].strip()

    candidates = [
        cell.strip()
        for cell in cells
        if cell.strip()
        and not _DATETIME_PATTERN.search(cell)
        and len(cell.strip()) > 8
    ]
    return max(candidates, key=len) if candidates else None


def extract_datetime(text: str, *, offset_minutes: int = 0) -> datetime | None:
    """Fecha y hora del despacho, si la fila las trae."""
    match = _DATETIME_PATTERN.search(text)
    if not match:
        return None
    raw = match.group("date")
    if match.group("time"):
        raw = f"{raw} {match.group('time')}"
    return parse_timestamp(raw, offset_minutes=offset_minutes)


def parse_dispatches(html: str, keys: Sequence[str]) -> list[Dispatch]:
    """Extrae del HTML los despachos cuya clave coincide. Función pura.

    Separada de la clase para poder testearla contra una página real guardada,
    que es la única forma honesta de verificar un scraper.
    """
    dispatches: list[Dispatch] = []

    for row_match in _ROW_PATTERN.finditer(html):
        body = row_match.group("body")
        cells = [strip_html(cell) for cell in _CELL_PATTERN.findall(body)]
        row_text = strip_html(body)
        if not row_text:
            continue

        key = matches_key(row_text, keys)
        if key is None:
            continue

        dispatches.append(
            Dispatch(
                key=key,
                address=extract_address(cells) if cells else None,
                occurred_at=extract_datetime(row_text),
                commune=None,
                raw_text=row_text[:2000],
            )
        )

    return dispatches


def build_external_id(dispatch: Dispatch) -> str:
    """Hash determinista. El portal no entrega ids propios.

    Entran la clave, la dirección y el instante: es la terna que identifica un
    despacho. Se excluye el resto del texto de la fila a propósito, porque los
    portales incluyen contadores de unidades en camino que cambian entre lecturas
    — incluirlos convertiría cada refresco en un despacho nuevo.
    """
    stamp = dispatch.occurred_at.isoformat() if dispatch.occurred_at else "sin-fecha"
    payload = f"{dispatch.key}|{dispatch.address or ''}|{stamp}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"bomberos:{digest}"


def build_text(dispatch: Dispatch) -> str:
    where = f" — {dispatch.address}" if dispatch.address else ""
    return f"Clave {dispatch.key} (rescate vehicular){where} · despacho de Bomberos"


class Bomberos104Collector(BaseCollector):
    """Scraper de despachos de Bomberos con clave de rescate vehicular."""

    name = "bomberos_10_4"
    source = EventSource.BOMBEROS
    default_interval_seconds = 180

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.BOMBEROS_POLL_INTERVAL_SECONDS

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        if not settings.BOMBEROS_DISPATCH_URL.strip():
            raise CollectorError(
                "BOMBEROS_DISPATCH_URL no está configurada. Cada Cuerpo de "
                "Bomberos publica en su propio portal: no hay un valor por "
                "defecto que no sea una suposición."
            )
        self.url = settings.BOMBEROS_DISPATCH_URL.strip()
        self.keys = [key.strip() for key in settings.BOMBEROS_ACCIDENT_KEYS if key.strip()]
        if not self.keys:
            raise CollectorError("BOMBEROS_ACCIDENT_KEYS quedó vacía")

    def run_params(self) -> dict[str, Any]:
        return {"keys": self.keys, "url": self.url}

    async def fetch(self) -> Sequence[Dispatch]:
        async with httpx.AsyncClient(
            timeout=settings.BOMBEROS_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
        ) as client:
            try:
                response = await client.get(self.url)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise CollectorError(
                    f"bomberos: HTTP {exc.response.status_code} al leer el portal",
                    detail={"url": self.url},
                ) from exc
            except httpx.HTTPError as exc:
                raise CollectorError(
                    f"bomberos: {type(exc).__name__} al leer el portal: {exc}",
                    detail={"url": self.url},
                ) from exc

        html = response.text
        if not html.strip():
            raise CollectorError("bomberos: el portal devolvió una página vacía")

        dispatches = parse_dispatches(html, self.keys)

        # La distinción que salva el dato: una página que se leyó pero de la que
        # no se extrajo NINGUNA fila es sospechosa de rediseño, no de calma. Una
        # página con filas donde ninguna es 10-4 es simplemente una noche sin
        # rescates vehiculares, y eso no merece alarma.
        if not _ROW_PATTERN.search(html):
            self.warn(
                "no se reconoció ninguna fila en el portal: probable cambio de "
                "maquetación. El parseo necesita revisión."
            )

        return dispatches

    def normalize(self, records: Sequence[Dispatch]) -> list[EventCreate]:
        now = datetime.now(UTC)
        events: list[EventCreate] = []
        undated = 0

        for dispatch in records:
            if dispatch.occurred_at is None:
                undated += 1
            timestamp = dispatch.occurred_at or now
            # Un portal con el reloj adelantado haría fallar la validación de
            # `EventCreate` y perdería el lote entero por una fila.
            if timestamp > now:
                timestamp = now

            events.append(
                EventCreate(
                    timestamp=timestamp,
                    source=EventSource.BOMBEROS,
                    type=EventType.ACCIDENT,
                    # Sin lat/lon: ver el docstring del módulo. La dirección va en
                    # `raw_data` para que sea auditable y reutilizable.
                    text=build_text(dispatch),
                    external_id=build_external_id(dispatch),
                    confidence=BOMBEROS_CONFIDENCE,
                    raw_data={
                        "_collector": self.name,
                        "_bomberos": {
                            "clave": dispatch.key,
                            "direccion": dispatch.address,
                            "fila": dispatch.raw_text,
                            "fecha_declarada": (
                                dispatch.occurred_at.isoformat()
                                if dispatch.occurred_at
                                else None
                            ),
                        },
                    },
                )
            )

        if undated:
            self.warn(
                f"{undated} despachos sin fecha reconocible; se usó la hora de la corrida"
            )
        return events


__all__ = [
    "BOMBEROS_CONFIDENCE",
    "Bomberos104Collector",
    "Dispatch",
    "build_external_id",
    "build_text",
    "extract_address",
    "extract_datetime",
    "matches_key",
    "parse_dispatches",
    "strip_html",
]
