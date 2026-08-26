"""Clientes genéricos para servicios geoespaciales institucionales.

CONAF y SENAPRED publican sus capas a través de servidores geoespaciales
estándar (ArcGIS FeatureServer, MapServer/GeoServer con WFS). Este módulo
concentra lo que ambos comparten —paginación, detección de errores, parseo de
GeoJSON, normalización de coordenadas y fechas— para que cada collector se
ocupe sólo de su mapeo de dominio.

Principio de diseño heredado del collector de FIRMS: **una respuesta que no se
entiende NO es una respuesta vacía**. Un servicio que cambia de formato, devuelve
HTML de un portal caído o un `{"error": ...}` con HTTP 200 tiene que hacer
fallar la corrida y quedar registrado en `collector_runs`, no producir cero
eventos en silencio durante días.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from app.core.exceptions import CollectorError

logger = logging.getLogger(__name__)

SourceKind = Literal["arcgis", "wfs", "geojson"]

#: Rango de latitudes de Chile continental + insular cercano. Se usa sólo para
#: detectar ejes invertidos (lat/lon en vez de lon/lat), un clásico de WFS 1.1.0
#: con EPSG:4326. No filtra datos: si ambas lecturas son plausibles, no se toca.
_CHILE_LAT_MIN = -57.0
_CHILE_LAT_MAX = -17.0

#: Tope de páginas por consulta. Evita un bucle infinito si un servicio ignora
#: `resultOffset` y devuelve siempre la primera página.
_MAX_PAGES = 50

#: Cuerpos que delatan que no estamos hablando con un servicio de datos.
_HTML_MARKERS = ("<!doctype html", "<html", "<head", "<body")


# --- Modelo intermedio -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GeoFeature:
    """Una feature ya desenvuelta, independiente del formato de origen."""

    properties: dict[str, Any]
    lat: float | None = None
    lon: float | None = None
    #: Identificador que trae la propia feature (`id` de GeoJSON, OBJECTID, …).
    feature_id: str | None = None
    #: Geometría original, para conservarla íntegra en `raw_data`.
    geometry: dict[str, Any] | None = None

    def get(self, *names: str, default: Any = None) -> Any:
        """Primer valor no nulo entre varios nombres de campo posibles.

        Los servicios institucionales renombran columnas sin avisar
        (`estado` → `ESTADO` → `estado_incendio`). Aceptar alias explícitos es
        más barato que un despliegue de emergencia en plena temporada.
        """
        lowered = {str(key).lower(): value for key, value in self.properties.items()}
        for name in names:
            value = self.properties.get(name)
            if value is None:
                value = lowered.get(name.lower())
            if value is not None and str(value).strip() != "":
                return value
        return default

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lon is not None


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """Un endpoint concreto del que se puede intentar leer una capa.

    Se declaran en el `.env` como `kind|url|layer`, separando varias fuentes con
    `;`. La primera que responde algo interpretable gana; las demás quedan como
    respaldo. Así, si una institución cambia de plataforma, se corrige con una
    variable de entorno y no con un despliegue.
    """

    kind: SourceKind
    url: str
    layer: str | None = None
    params: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.url}" + (f"#{self.layer}" if self.layer else "")


def parse_source_specs(raw: str | Sequence[str] | None) -> list[SourceSpec]:
    """Convierte la declaración textual del `.env` en especificaciones.

    Formato: ``kind|url[|layer]`` separado por ``;``. Ejemplo::

        arcgis|https://host/arcgis/rest/services/x/FeatureServer|0;wfs|https://host/wfs|ns:capa
    """
    if raw is None:
        return []
    chunks: Iterable[str]
    chunks = raw.split(";") if isinstance(raw, str) else raw

    specs: list[SourceSpec] = []
    for chunk in chunks:
        token = chunk.strip()
        if not token:
            continue
        parts = [part.strip() for part in token.split("|")]
        if len(parts) < 2:
            raise ValueError(
                f"fuente mal declarada: {token!r}. Formato esperado 'kind|url[|layer]'"
            )
        kind = parts[0].lower()
        if kind not in ("arcgis", "wfs", "geojson"):
            raise ValueError(
                f"kind desconocido {kind!r} en {token!r}; use arcgis, wfs o geojson"
            )
        layer = parts[2] if len(parts) > 2 and parts[2] else None
        specs.append(SourceSpec(kind=kind, url=parts[1], layer=layer))  # type: ignore[arg-type]
    return specs


# --- Normalización de valores ------------------------------------------------


def parse_timestamp(value: Any, *, offset_minutes: int = 0) -> datetime | None:
    """Normaliza a datetime UTC las fechas que devuelven estos servicios.

    Acepta:
      * epoch en milisegundos (ArcGIS: `esriFieldTypeDate` viaja como int ms UTC),
      * epoch en segundos,
      * ISO 8601 con o sin zona (`2026-08-16T14:32:00Z`, `2026-08-16 14:32:00`).

    `offset_minutes` corrige fuentes que publican hora local etiquetada como UTC.
    Se deja configurable en vez de adivinado: un desfase de 4 horas en el
    timestamp arruina cualquier correlación espaciotemporal, así que debe ser una
    decisión explícita y calibrable, no un supuesto enterrado en el código.
    """
    if value is None or value == "":
        return None

    moment: datetime | None = None

    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        seconds = float(value)
        # ArcGIS entrega milisegundos. 1e11 s serían el año 5138: cualquier cosa
        # por encima de ese umbral son milisegundos, no segundos.
        if abs(seconds) > 1e11:
            seconds /= 1000.0
        try:
            moment = datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        token = str(value).strip()
        if not token:
            return None
        if token.isdigit() or (token.startswith("-") and token[1:].isdigit()):
            return parse_timestamp(int(token), offset_minutes=offset_minutes)
        normalised = token.replace("Z", "+00:00").replace("z", "+00:00")
        try:
            moment = datetime.fromisoformat(normalised)
        except ValueError:
            for pattern in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M", "%d-%m-%Y"):
                try:
                    moment = datetime.strptime(token, pattern)
                    break
                except ValueError:
                    continue
        if moment is None:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)

    moment = moment.astimezone(UTC)
    if offset_minutes:
        moment = moment - _minutes(offset_minutes)
    return moment


def _minutes(value: int):  # pequeño helper para no importar timedelta en todo el módulo
    from datetime import timedelta

    return timedelta(minutes=value)


def as_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def normalise_text(value: Any) -> str:
    """Minúsculas sin tildes: para comparar 'Valparaíso' con 'VALPARAISO'."""
    import unicodedata

    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFD", str(value))
    stripped = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join(stripped.lower().split())


# --- Parseo de GeoJSON -------------------------------------------------------


def _representative_point(geometry: Mapping[str, Any] | None) -> tuple[float, float] | None:
    """Un punto representativo de cualquier geometría GeoJSON.

    Las capas de incendios pueden ser puntos (foco) o polígonos (perímetro). El
    esquema de eventos guarda un par lat/lon, así que de un polígono se toma el
    centro de su caja envolvente: no es el centroide exacto, pero es estable,
    barato y suficiente para correlacionar a escala de kilómetros.
    """
    if not geometry:
        return None
    coordinates = geometry.get("coordinates")
    if coordinates is None:
        return None

    xs: list[float] = []
    ys: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, list | tuple):
            if (
                len(node) >= 2
                and isinstance(node[0], int | float)
                and isinstance(node[1], int | float)
            ):
                xs.append(float(node[0]))
                ys.append(float(node[1]))
                return
            for child in node:
                walk(child)

    walk(coordinates)
    if not xs or not ys:
        return None
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def resolve_coordinates(
    x: float | None, y: float | None, *, origin: str
) -> tuple[float | None, float | None]:
    """Devuelve `(lat, lon)` a partir del par `(x, y)` de GeoJSON.

    GeoJSON define el orden `[lon, lat]`, pero varios servidores WFS 1.1.0
    configurados con EPSG:4326 lo emiten al revés. Como en Chile continental la
    latitud siempre cae en [-57, -17] y la longitud nunca, el orden real se puede
    deducir sin ambigüedad. Si ninguna de las dos lecturas es plausible se
    conserva la interpretación estándar: es mejor un punto raro y visible que un
    punto silenciosamente corregido.
    """
    if x is None or y is None:
        return (None, None)

    standard_ok = _CHILE_LAT_MIN <= y <= _CHILE_LAT_MAX
    swapped_ok = _CHILE_LAT_MIN <= x <= _CHILE_LAT_MAX

    if not standard_ok and swapped_ok:
        logger.warning(
            "ejes invertidos detectados; se corrige lat/lon",
            extra={"origin": origin, "x": x, "y": y},
        )
        return (x, y)
    return (y, x)


def parse_feature_collection(payload: Any, *, origin: str) -> list[GeoFeature]:
    """Extrae features de un FeatureCollection GeoJSON.

    Distingue tres situaciones que un `except: return []` confundiría:
      * colección válida y vacía → `[]` legítimo,
      * colección válida con features → lista poblada,
      * cualquier otra cosa → `CollectorError`.
    """
    if not isinstance(payload, Mapping):
        raise CollectorError(
            f"{origin}: se esperaba un objeto GeoJSON y llegó {type(payload).__name__}"
        )

    raise_if_service_error(payload, origin=origin)

    features = payload.get("features")
    if features is None:
        raise CollectorError(
            f"{origin}: la respuesta no tiene 'features'. Claves recibidas: "
            f"{sorted(str(key) for key in payload)[:15]}"
        )
    if not isinstance(features, list):
        raise CollectorError(
            f"{origin}: 'features' debería ser una lista y es {type(features).__name__}"
        )

    parsed: list[GeoFeature] = []
    for raw_feature in features:
        if not isinstance(raw_feature, Mapping):
            continue
        geometry = raw_feature.get("geometry")
        geometry = dict(geometry) if isinstance(geometry, Mapping) else None
        properties = raw_feature.get("properties")
        properties = dict(properties) if isinstance(properties, Mapping) else {}

        point = _representative_point(geometry)
        lat, lon = resolve_coordinates(
            point[0] if point else None, point[1] if point else None, origin=origin
        )

        feature_id = raw_feature.get("id")
        parsed.append(
            GeoFeature(
                properties=properties,
                lat=lat,
                lon=lon,
                feature_id=str(feature_id) if feature_id is not None else None,
                geometry=geometry,
            )
        )
    return parsed


#: Claves donde un servidor deja el texto del error. Ordenadas por preferencia:
#: se busca la más específica primero para no quedarse con un `status: "error"`
#: cuando al lado hay un `message` que dice qué pasó de verdad.
_ERROR_MESSAGE_KEYS: tuple[str, ...] = (
    "message",
    "mensaje",
    "error_description",
    "errorMessage",
    "detail",
    "description",
    "error",
    "title",
)

#: Claves donde suele venir el código. Un `code: 429` cambia el diagnóstico por
#: completo respecto de un `code: 404`.
_ERROR_CODE_KEYS: tuple[str, ...] = ("code", "status", "statusCode", "codigo", "error_code")

#: Códigos que describen algo que se cura solo. La distinción no es cosmética:
#: decide si el operador tiene que hacer algo ahora o si la próxima corrida —a
#: cinco minutos— probablemente ya traiga datos.
_TRANSIENT_CODES: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class ServiceErrorEnvelope:
    """Un error que el servidor sirvió con HTTP 2xx."""

    message: str
    code: int | None = None
    #: `True` si conviene esperar a la próxima corrida en vez de tocar nada.
    transient: bool = False
    #: Las claves que traía el cuerpo, para el mensaje de diagnóstico.
    keys: tuple[str, ...] = ()

    def describe(self) -> str:
        partes = [f"«{self.message}»"]
        if self.code is not None:
            partes.append(f"code={self.code}")
        partes.append("transitorio" if self.transient else "requiere revisión")
        return " · ".join(partes)


def detect_service_error(payload: Any) -> ServiceErrorEnvelope | None:
    """¿Es esto un sobre de error en vez de datos? Devuelve el error, o None.

    Complementa a `raise_if_service_error`, que sólo reconoce el formato de
    ArcGIS y GeoServer (`error`, `exception`). Este reconoce el sobre genérico
    `{"code": …, "message": …, "status": …}` que emiten pasarelas de API, WAF y
    manejadores de error por defecto — y que llega **con HTTP 200**, así que el
    transporte lo deja pasar como si fuera una respuesta buena.

    La guarda contra falsos positivos
    ---------------------------------
    Un payload legítimo puede traer `status` o incluso `message` junto a los
    datos. Por eso no basta con encontrar una clave conocida: **si el cuerpo
    contiene cualquier lista, no es un error**. Un sobre de error no trae
    colecciones; un volcado de cortes siempre trae la suya, aunque venga vacía.

    Esa condición es la que hace seguro llamar a esta función desde collectors
    que hoy funcionan: para confundirse tendría que llegar un payload sin una
    sola lista y con un campo de mensaje, que es exactamente la definición de
    un error.
    """
    if not isinstance(payload, Mapping) or not payload:
        return None

    # Si hay datos, no es un error. Se mira en profundidad uno: un sobre de dos
    # niveles con la lista dentro sigue siendo datos.
    for valor in payload.values():
        if isinstance(valor, list):
            return None
        if isinstance(valor, Mapping) and any(
            isinstance(anidado, list) for anidado in valor.values()
        ):
            return None

    mensaje = _first_present(payload, _ERROR_MESSAGE_KEYS)
    if mensaje is None:
        return None

    codigo = _as_status_code(_first_present(payload, _ERROR_CODE_KEYS))
    return ServiceErrorEnvelope(
        message=" ".join(str(mensaje).split())[:300],
        code=codigo,
        transient=codigo in _TRANSIENT_CODES if codigo is not None else False,
        keys=tuple(sorted(str(clave) for clave in payload)),
    )


def _first_present(payload: Mapping[str, Any], keys: Sequence[str]) -> Any:
    """Primer valor no vacío entre las claves dadas, sin distinguir mayúsculas."""
    normalizado = {
        str(clave).lower().replace("_", ""): valor for clave, valor in payload.items()
    }
    for clave in keys:
        valor = normalizado.get(clave.lower().replace("_", ""))
        if valor is not None and str(valor).strip():
            return valor
    return None


def _as_status_code(value: Any) -> int | None:
    """`429`, `"429"` o `"error"` → entero o None.

    Muchos servidores ponen texto en `status` (`"error"`, `"KO"`) y el número en
    `code`. Devolver None ante lo no numérico deja que el llamador siga
    buscando en la otra clave en vez de inventar un código.
    """
    if isinstance(value, bool):  # `True` no es un código de estado
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def raise_if_service_error(payload: Mapping[str, Any], *, origin: str) -> None:
    """ArcGIS y GeoServer devuelven errores con HTTP 200. Hay que mirarlos.

    Este es el modo de falla más peligroso de todo el pipeline: si se ignora, el
    collector reporta `success` con cero eventos y el hueco en los datos parece
    "no hubo incendios".
    """
    error = payload.get("error") or payload.get("exception") or payload.get("exceptions")
    if not error:
        return
    if isinstance(error, Mapping):
        message = error.get("message") or error.get("text") or json.dumps(error)[:300]
        details = error.get("details")
        raise CollectorError(
            f"{origin}: el servicio devolvió un error: {message}",
            detail={"details": details, "code": error.get("code")},
        )
    raise CollectorError(f"{origin}: el servicio devolvió un error: {str(error)[:300]}")


# --- Transporte HTTP ---------------------------------------------------------


async def request_response(
    client: httpx.AsyncClient,
    url: str,
    params: Mapping[str, Any],
    *,
    origin: str,
    retries: int = 2,
    backoff: float = 1.5,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    json_body: Any = None,
) -> httpx.Response:
    """Petición con reintentos, o `CollectorError` con un mensaje diagnosticable.

    Reintenta ante errores de red y 5xx, que en portales institucionales son
    frecuentes y transitorios. No reintenta ante 4xx: eso es un contrato roto y
    reintentarlo sólo retrasa el diagnóstico.

    Toda excepción de httpx —timeout, DNS, TLS, conexión rechazada— sale de acá
    convertida en `CollectorError`. Es lo que permite que los collectors declaren
    un único tipo de fallo esperado y que `BaseCollector.run()` lo registre en
    `collector_runs` sin dejar escapar nada al orquestador.

    Método, cabeceras y cuerpo
    --------------------------
    `method`, `headers` y `json_body` son opcionales y con los valores por
    defecto la función se comporta exactamente igual que antes: un GET sin
    cabeceras propias. Existen porque no todas las fuentes son capas
    geoespaciales abiertas — el visor de Chilquinta consulta su backend por POST
    y exige una API key estática en `x-api-key`.

    Se añadieron acá y no en cada collector a propósito. Una fuente que necesita
    cabeceras no deja de necesitar reintentos, detección de HTML ni conversión de
    errores a `CollectorError`; si cada collector abriera su propio camino de red
    para poder mandar una cabecera, perdería todo lo demás y el proyecto acabaría
    con tantos manejos de fallo como fuentes.

    Sobre reintentar un POST: estos endpoints son de **lectura** (una consulta
    disfrazada de POST, que es lo habitual cuando el visor manda filtros en el
    cuerpo), así que repetirlos es idempotente en la práctica. Si alguna vez se
    usa este transporte para escribir algo, hay que pasar `retries=0`.
    """
    last_error: str = "sin intentos"

    # `params` vacío se omite en vez de pasarse como `{}`. httpx **reemplaza** el
    # query string de la URL cuando recibe `params`, aunque venga vacío:
    #
    #     client.get("https://host/mapas?emp=006", params={})
    #     → https://host/mapas          ← el `emp=006` desapareció
    #
    # Varios collectors llaman con `{}` porque su filtro ya viene dentro de la
    # URL configurada. Sin esta guarda, ese filtro se perdía en silencio y la
    # fuente devolvía el catálogo completo — un fallo que no rompe nada, sólo
    # trae de más.
    #
    # `None` es el valor que httpx interpreta como «no toques la query», tanto en
    # `params` como en `headers`, así que la guarda se expresa igual para ambos.
    consulta = dict(params) if params else None
    cabeceras = dict(headers) if headers else None
    verbo = method.upper()

    for attempt in range(retries + 1):
        try:
            # `client.request` y no `client.get`: la firma sobrecargada de `get`
            # no acepta cuerpo y obligaba a bifurcar la llamada para que mypy
            # siguiera el rastro. `request` tiene una sola firma, admite los tres
            # argumentos nuevos y deja una única línea que ejercita todos los
            # caminos.
            response = await client.request(
                verbo,
                url,
                params=consulta,
                headers=cabeceras,
                json=json_body,
            )
            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                raise httpx.HTTPStatusError(
                    last_error, request=response.request, response=response
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code < 500:
                raise CollectorError(
                    f"{origin}: HTTP {status_code} — {exc.response.text[:200]}",
                    detail={"url": url, "status": status_code},
                ) from exc
            last_error = f"HTTP {status_code}"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            return response

        if attempt < retries:
            await asyncio.sleep(backoff * (2**attempt))

    raise CollectorError(
        f"{origin}: sin respuesta tras {retries + 1} intentos ({last_error})",
        detail={"url": url, "method": verbo},
    )


async def request_json(
    client: httpx.AsyncClient,
    url: str,
    params: Mapping[str, Any],
    *,
    origin: str,
    retries: int = 2,
    backoff: float = 1.5,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    json_body: Any = None,
) -> Any:
    """Petición que devuelve JSON o falla con un mensaje diagnosticable.

    `method`, `headers` y `json_body` se pasan tal cual a `request_response`;
    ver allí por qué existen. Con los valores por defecto sigue siendo el GET
    de siempre.
    """
    response = await request_response(
        client,
        url,
        params,
        origin=origin,
        retries=retries,
        backoff=backoff,
        method=method,
        headers=headers,
        json_body=json_body,
    )
    return _decode_json(response, origin=origin)


async def request_text(
    client: httpx.AsyncClient,
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    origin: str,
    retries: int = 2,
    backoff: float = 1.5,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    json_body: Any = None,
) -> str:
    """GET que devuelve el cuerpo como texto. Para RSS y para HTML.

    Existe para que las fuentes sin JSON —el feed de Bomberos, el portal del
    MTT— hereden exactamente los mismos reintentos y la misma conversión de
    errores que ya tenían las capas institucionales, en vez de que cada scraper
    invente su propio manejo de red y su propia idea de qué es un fallo.

    Un cuerpo vacío se trata como error: un 200 sin contenido es un portal caído
    que responde igual, y dejarlo pasar produciría cero eventos con estado
    `success` — el modo de fallo que este proyecto persigue en todas partes.
    """
    response = await request_response(
        client,
        url,
        params or {},
        origin=origin,
        retries=retries,
        backoff=backoff,
        method=method,
        headers=headers,
        json_body=json_body,
    )
    body = response.text
    if not body.strip():
        raise CollectorError(
            f"{origin}: la respuesta llegó vacía (HTTP {response.status_code})",
            detail={"url": url},
        )
    return body


def _decode_json(response: httpx.Response, *, origin: str) -> Any:
    body = response.text
    stripped = body.lstrip()[:200].lower()
    if any(marker in stripped for marker in _HTML_MARKERS):
        raise CollectorError(
            f"{origin}: se recibió HTML en vez de JSON. ¿Cambió el endpoint o hay "
            f"un portal de error delante? Inicio del cuerpo: {body[:200]!r}",
            detail={"url": str(response.request.url)},
        )
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise CollectorError(
            f"{origin}: respuesta no es JSON válido ({exc}). "
            f"Inicio del cuerpo: {body[:200]!r}",
            detail={"url": str(response.request.url)},
        ) from exc


# --- Clientes ----------------------------------------------------------------


class ArcGisFeatureClient:
    """Consulta una capa de un ArcGIS FeatureServer/MapServer y pagina sola.

    Se pide `f=geojson` porque evita tener que traducir la geometría propietaria
    de Esri y porque, si el servicio dejara de soportarlo, falla ruidosamente en
    vez de devolver una estructura que el parser interpretaría como vacía.
    """

    def __init__(self, *, timeout: float = 45.0, page_size: int = 1000) -> None:
        self.timeout = timeout
        self.page_size = page_size

    def query_url(self, base_url: str, layer: str | None) -> str:
        base = base_url.rstrip("/")
        if base.endswith("/query"):
            return base
        if layer is not None and not base.split("/")[-1].isdigit():
            base = f"{base}/{layer}"
        return f"{base}/query"

    async def fetch(
        self,
        spec: SourceSpec,
        *,
        where: str = "1=1",
        out_fields: str = "*",
        order_by: str | None = None,
        extra_params: Mapping[str, Any] | None = None,
    ) -> list[GeoFeature]:
        url = self.query_url(spec.url, spec.layer)
        origin = spec.label
        collected: list[GeoFeature] = []
        offset = 0

        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True
        ) as client:
            for page in range(_MAX_PAGES):
                params: dict[str, Any] = {
                    "where": where,
                    "outFields": out_fields,
                    "outSR": 4326,
                    "returnGeometry": "true",
                    "resultOffset": offset,
                    "resultRecordCount": self.page_size,
                    "f": "geojson",
                }
                if order_by:
                    params["orderByFields"] = order_by
                params.update(spec.params)
                if extra_params:
                    params.update(extra_params)

                payload = await request_json(client, url, params, origin=origin)
                features = parse_feature_collection(payload, origin=origin)
                collected.extend(features)

                if not _has_more_pages(payload, len(features), self.page_size):
                    break
                offset += len(features)
                if page == _MAX_PAGES - 1:
                    logger.warning(
                        "se alcanzó el tope de páginas; puede haber datos sin leer",
                        extra={"origin": origin, "collected": len(collected)},
                    )

        logger.debug(
            "ArcGIS consultado", extra={"origin": origin, "features": len(collected)}
        )
        return collected


def _has_more_pages(payload: Any, page_len: int, page_size: int) -> bool:
    if page_len == 0:
        return False
    if isinstance(payload, Mapping):
        if payload.get("exceededTransferLimit") is True:
            return True
        properties = payload.get("properties")
        if isinstance(properties, Mapping) and properties.get("exceededTransferLimit") is True:
            return True
    return page_len >= page_size


class WfsClient:
    """Cliente OGC WFS que pide GeoJSON.

    Es el camino previsto para el SIT de CONAF (MapServer sobre PostGIS): si
    publican la capa de incendios activos vía WFS, basta declararla en
    `CONAF_SOURCES` como `wfs|<url>|<typename>` sin tocar código.
    """

    def __init__(self, *, timeout: float = 45.0, version: str = "2.0.0") -> None:
        self.timeout = timeout
        self.version = version

    async def fetch(
        self,
        spec: SourceSpec,
        *,
        max_features: int | None = None,
        extra_params: Mapping[str, Any] | None = None,
    ) -> list[GeoFeature]:
        if not spec.layer:
            raise CollectorError(
                f"{spec.label}: una fuente WFS necesita el typename de la capa "
                f"(formato 'wfs|url|typename')"
            )

        version = str(spec.params.get("version", self.version))
        # WFS 2.0.0 renombró `maxFeatures` a `count` y `typeName` a `typeNames`.
        type_key = "typeNames" if version.startswith("2") else "typeName"
        count_key = "count" if version.startswith("2") else "maxFeatures"

        params: dict[str, Any] = {
            "service": "WFS",
            "version": version,
            "request": "GetFeature",
            type_key: spec.layer,
            "outputFormat": spec.params.get("outputFormat", "application/json"),
            "srsName": spec.params.get("srsName", "EPSG:4326"),
        }
        if max_features:
            params[count_key] = max_features
        params.update({k: v for k, v in spec.params.items() if k not in ("version",)})
        if extra_params:
            params.update(extra_params)

        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True
        ) as client:
            payload = await request_json(client, spec.url, params, origin=spec.label)
        return parse_feature_collection(payload, origin=spec.label)


class GeoJsonClient:
    """Descarga un GeoJSON plano publicado como archivo estático."""

    def __init__(self, *, timeout: float = 45.0) -> None:
        self.timeout = timeout

    async def fetch(
        self, spec: SourceSpec, *, extra_params: Mapping[str, Any] | None = None
    ) -> list[GeoFeature]:
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True
        ) as client:
            payload = await request_json(
                client, spec.url, {**spec.params, **(extra_params or {})}, origin=spec.label
            )
        return parse_feature_collection(payload, origin=spec.label)


@dataclass(slots=True)
class SourceAttempt:
    """Resultado de intentar leer una fuente. Alimenta la traza de la corrida."""

    spec: SourceSpec
    ok: bool
    features: int = 0
    error: str | None = None


class FailoverFetcher:
    """Recorre las fuentes declaradas hasta que una responde algo interpretable.

    Que una institución cambie de plataforma no debería dejar el sistema ciego.
    Se declara una cadena de respaldos y se registra qué se intentó y qué falló:
    aunque la corrida termine bien, el uso de un respaldo queda visible como
    advertencia en `collector_runs`.
    """

    def __init__(
        self,
        specs: Sequence[SourceSpec],
        *,
        timeout: float = 45.0,
        page_size: int = 1000,
    ) -> None:
        self.specs = list(specs)
        self.arcgis = ArcGisFeatureClient(timeout=timeout, page_size=page_size)
        self.wfs = WfsClient(timeout=timeout)
        self.geojson = GeoJsonClient(timeout=timeout)
        self.attempts: list[SourceAttempt] = []

    async def _fetch_one(self, spec: SourceSpec, **kwargs: Any) -> list[GeoFeature]:
        if spec.kind == "arcgis":
            return await self.arcgis.fetch(spec, **kwargs)
        if spec.kind == "wfs":
            # `where` es sintaxis de ArcGIS y no viaja a WFS: allí el filtrado
            # equivalente sería un CQL/OGC filter, que cada servidor implementa a
            # su manera. Se descarta a propósito y el respaldo trae la capa
            # completa; el filtro por región y estado del collector es de todos
            # modos el que manda sobre el resultado.
            wfs_kwargs = {
                key: value
                for key, value in kwargs.items()
                if key in ("max_features", "extra_params")
            }
            return await self.wfs.fetch(spec, **wfs_kwargs)
        return await self.geojson.fetch(
            spec, extra_params=kwargs.get("extra_params")
        )

    async def fetch(self, **kwargs: Any) -> list[GeoFeature]:
        if not self.specs:
            raise CollectorError(
                "no hay fuentes declaradas para este collector; revisar la "
                "variable *_SOURCES en el .env"
            )

        self.attempts = []
        for spec in self.specs:
            try:
                features = await self._fetch_one(spec, **kwargs)
            except CollectorError as exc:
                self.attempts.append(SourceAttempt(spec=spec, ok=False, error=exc.message))
                logger.warning(
                    "fuente no disponible; se intenta la siguiente",
                    extra={"source": spec.label, "error": exc.message},
                )
                continue
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                self.attempts.append(SourceAttempt(spec=spec, ok=False, error=message))
                logger.warning(
                    "fuente falló de forma inesperada; se intenta la siguiente",
                    extra={"source": spec.label, "error": message},
                )
                continue

            self.attempts.append(
                SourceAttempt(spec=spec, ok=True, features=len(features))
            )
            return features

        raise CollectorError(
            "ninguna de las fuentes declaradas respondió",
            detail={
                "attempts": [
                    {"source": attempt.spec.label, "error": attempt.error}
                    for attempt in self.attempts
                ]
            },
        )

    @property
    def used_fallback(self) -> bool:
        """¿Se llegó al dato por un respaldo en vez de por la fuente primaria?"""
        return any(not attempt.ok for attempt in self.attempts)

    def failure_summary(self) -> list[str]:
        return [
            f"{attempt.spec.label}: {attempt.error}"
            for attempt in self.attempts
            if not attempt.ok
        ]
