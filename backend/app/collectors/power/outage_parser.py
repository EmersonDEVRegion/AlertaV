"""Lectura tolerante del JSON de cortes de las distribuidoras eléctricas.

Por qué este parser es tan permisivo
------------------------------------
Porque **no se pudo verificar el contrato**. `mapainterrupciones.chilquinta.cl`
es un visor de mapa renderizado en el navegador: la URL raíz devuelve el HTML de
la aplicación, y los datos los pide después por una ruta interna que no está
documentada públicamente. CGE ni siquiera tiene URL asignada todavía.

Escribir un parser que asuma nombres de campo concretos sobre un esquema que
nadie miró sería inventar un contrato y llamarlo implementación. En su lugar:

* **Alias por campo.** Se prueban los nombres que estas empresas suelen usar,
  en español y en inglés, hasta que uno responde. Cubrir `latitud`, `lat` y `y`
  cuesta tres líneas y evita que el collector falle por una vocal.
* **Formas de sobre variadas.** Una lista en la raíz, o anidada bajo `data`,
  `features`, `interrupciones`… incluso GeoJSON, que es lo más probable en un
  visor de mapas.
* **Ausencias esperadas.** Todo se lee con `.get()` y toda ausencia produce
  `None`, nunca una excepción. Un corte sin hora de reposición estimada es un
  dato incompleto y perfectamente normal: la empresa aún no sabe cuándo va a
  reponer.

La única ausencia fatal es la coordenada: sin ella el corte no se puede pintar
ni correlacionar, y ese registro se descarta.

Qué pasa cuando el esquema real no coincide
--------------------------------------------
El collector lo detecta y falla con un mensaje que dice qué claves llegaron, en
vez de reportar cero cortes con estado `success`. Ver `describe_shape`, que
existe exactamente para que ese primer despliegue contra el endpoint real tarde
minutos en depurarse y no una tarde.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.collectors.geoservices import as_float, parse_timestamp

logger = logging.getLogger(__name__)

#: Las distribuidoras publican en hora chilena. Igual que el CSN, se resuelve
#: con la base IANA y no con un desplazamiento fijo: Chile cambia la hora dos
#: veces al año y un `-4` escrito a mano acierta la mitad del año.
CHILE_TZ = ZoneInfo("America/Santiago")

#: Claves donde puede venir la lista de cortes dentro del sobre.
_LIST_KEYS: tuple[str, ...] = (
    "features",  # GeoJSON: lo más probable en un visor de mapa
    "data",
    "items",
    "results",
    "interrupciones",
    "cortes",
    # Chilquinta llama "orden" a cada corte: es la orden de trabajo con la que
    # lo gestiona internamente, y la lista viene bajo `ordenes`.
    "ordenes",
    "outages",
    "eventos",
)

#: Alias por campo, en orden de preferencia.
_LAT_KEYS: tuple[str, ...] = ("lat", "latitud", "latitude", "y", "coord_y")
_LON_KEYS: tuple[str, ...] = ("lon", "lng", "longitud", "longitude", "x", "coord_x")
_CLIENTS_KEYS: tuple[str, ...] = (
    "clientes_afectados",
    "clientesafectados",
    "clientes",
    "affected_clients",
    "afectados",
    "customers",
    "cantidad_clientes",
    "cant_clientes",  # Chilquinta, y como string ("68")
)
_RESTORE_KEYS: tuple[str, ...] = (
    "hora_reposicion",
    "horareposicion",
    "fecha_reposicion",
    "reposicion",
    "estimated_restoration",
    "restoration_time",
    "hora_estimada_reposicion",
    "eta",
    # Chilquinta: `etr`, de "estimated time of restoration", en dd-mm-yyyy y
    # hora de Chile. La abreviatura es del gremio eléctrico, no una errata.
    "etr",
)
_START_KEYS: tuple[str, ...] = (
    "hora_inicio",
    "fecha_inicio",
    "inicio",
    "start_time",
    "started_at",
    "fecha",
)
_ID_KEYS: tuple[str, ...] = (
    "id",
    "codigo",
    "folio",
    "outage_id",
    "uuid",
    "gid",
    "orden",  # Chilquinta: número de orden de trabajo, estable durante el corte
)
_COMMUNE_KEYS: tuple[str, ...] = ("comuna", "commune", "municipio", "localidad")
_SECTOR_KEYS: tuple[str, ...] = ("sector", "direccion", "descripcion", "detalle")

#: Chile continental con holgura. Igual que en el parser del CSN: no es un
#: filtro de negocio sino detección de que se leyó el campo equivocado.
_CHILE_BOUNDS = {"lat": (-60.0, -15.0), "lon": (-115.0, -65.0)}


@dataclass(frozen=True, slots=True)
class PowerOutage:
    """Un corte de suministro, con los tipos ya resueltos."""

    #: Identificador en el sistema de la empresa, si lo entrega.
    outage_id: str | None
    lat: float
    lon: float
    #: Clientes sin suministro. `None` cuando la empresa no lo publica, que no es
    #: lo mismo que cero.
    affected_clients: int | None
    #: Hora estimada de reposición, en UTC. Estimada: las empresas la corrigen.
    restoration_at: datetime | None
    #: Inicio del corte, si viene. Si no, el collector usa la hora de la corrida.
    started_at: datetime | None
    commune: str | None
    sector: str | None
    #: El registro original, para poder reprocesar sin volver a consultar.
    raw: Mapping[str, Any]


def _first(payload: Mapping[str, Any], keys: Sequence[str]) -> Any:
    """Primer alias presente y no vacío. Comparación insensible a mayúsculas.

    Se normalizan las claves del payload una sola vez por registro: las empresas
    alternan entre `clientesAfectados`, `clientes_afectados` y `CLIENTES` sin
    criterio aparente, y probar cada variante a mano multiplicaría los alias.
    """
    normalizado = {
        str(clave).lower().replace(" ", "").replace("-", "").replace("_", ""): valor
        for clave, valor in payload.items()
    }
    for clave in keys:
        buscada = clave.lower().replace("_", "")
        valor = normalizado.get(buscada)
        if valor is not None and str(valor).strip() not in ("", "null", "None"):
            return valor
    return None


def _as_int(value: Any) -> int | None:
    """Entero no negativo, o None.

    Las empresas publican el conteo como número, como texto («1.250») y a veces
    como rango («100-200»). Se acepta lo que `as_float` sepa leer y se rechaza
    lo negativo, que sólo puede ser un error de la fuente.
    """
    numero = as_float(value)
    if numero is None or numero < 0:
        return None
    return int(numero)


#: Marcas de zona horaria al final de una fecha ISO: `Z`, `+03:00`, `-0400`.
_TZ_SUFFIX = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})\s*$", re.IGNORECASE)


def _as_local_datetime(value: Any) -> datetime | None:
    """Fecha de la fuente → UTC, interpretando lo naive como hora chilena.

    Aquí hay una trampa que costó un bug y conviene dejar escrita:
    `parse_timestamp` **ya devuelve un datetime con `tzinfo=UTC`** aunque la
    cadena de entrada no traiga zona — asume UTC, que es el criterio correcto
    para las capas institucionales que lo usaban hasta ahora (ArcGIS publica en
    epoch UTC). Un `if resuelto.tzinfo is None` después de llamarlo no se cumple
    nunca, y la fecha chilena se guardaba desplazada cuatro horas sin que nada
    fallara.

    Cuatro horas es exactamente el tamaño de error que hace daño acá: una
    reposición estimada para las 18:30 se guardaría como 18:30 UTC —las 14:30 de
    Chile—, así que el mapa mostraría como ya cumplida una reposición que aún no
    ha ocurrido.

    Por eso la decisión se toma sobre el **texto original**, antes de parsear:

    * Si trae marca de zona (`Z`, `+03:00`), es absoluta y sólo se convierte.
    * Si es un número, es epoch y por definición es UTC.
    * Si no trae nada, es hora de pared chilena y se la ancla con `zoneinfo`
      —nunca con un offset fijo: Chile cambia la hora dos veces al año.
    """
    resuelto = parse_timestamp(value)
    if resuelto is None:
        return None

    # Epoch: absoluto por definición.
    if isinstance(value, int | float):
        return resuelto.astimezone(UTC)

    texto = str(value).strip()
    if texto.replace(".", "", 1).replace("-", "", 1).isdigit():
        return resuelto.astimezone(UTC)

    if _TZ_SUFFIX.search(texto):
        return resuelto.astimezone(UTC)

    # Hora de pared chilena: se descarta el UTC que puso `parse_timestamp` y se
    # vuelve a anclar en la zona correcta.
    return resuelto.replace(tzinfo=CHILE_TZ).astimezone(UTC)


def _coordinates(record: Mapping[str, Any]) -> tuple[float, float] | None:
    """Coordenadas del registro, sea plano o GeoJSON.

    En GeoJSON el orden es `[lon, lat]` por la RFC 7946 y no al revés. Es el
    error clásico de estos parseos y deposita todos los cortes de Valparaíso en
    el océano Índico — con coordenadas válidas, así que sin ningún error visible.
    """
    geometry = record.get("geometry")
    if isinstance(geometry, Mapping):
        coords = geometry.get("coordinates")
        if isinstance(coords, list | tuple) and len(coords) >= 2:
            lon, lat = as_float(coords[0]), as_float(coords[1])
            if lat is not None and lon is not None:
                return _validated(lat, lon)

    lat = as_float(_first(record, _LAT_KEYS))
    lon = as_float(_first(record, _LON_KEYS))
    if lat is None or lon is None:
        return None
    return _validated(lat, lon)


def _validated(lat: float, lon: float) -> tuple[float, float] | None:
    lat_min, lat_max = _CHILE_BOUNDS["lat"]
    lon_min, lon_max = _CHILE_BOUNDS["lon"]
    if not (lat_min <= lat <= lat_max) or not (lon_min <= lon <= lon_max):
        return None
    return (lat, lon)


def parse_outage(payload: Any) -> PowerOutage | None:
    """Un registro del JSON → `PowerOutage`. None si no es utilizable.

    Se aplana el sobre de GeoJSON —`properties` al mismo nivel que el resto—
    para que los alias funcionen igual con estructura plana o geoespacial.

    Descarta en silencio y devuelve None en vez de lanzar: un feed operativo trae
    registros incompletos de rutina, y perder la corrida entera por uno sería
    cambiar un dato faltante por doscientos.
    """
    if not isinstance(payload, Mapping):
        return None

    plano: dict[str, Any] = dict(payload)
    propiedades = payload.get("properties")
    if isinstance(propiedades, Mapping):
        # Las propiedades de GeoJSON ganan sobre las del contenedor: son las del
        # corte, mientras que en la raíz sólo hay metadatos del feature.
        plano.update(propiedades)

    coords = _coordinates(payload if "geometry" in payload else plano)
    if coords is None:
        return None

    identificador = _first(plano, _ID_KEYS)
    lat, lon = coords

    return PowerOutage(
        outage_id=str(identificador).strip() if identificador is not None else None,
        lat=lat,
        lon=lon,
        affected_clients=_as_int(_first(plano, _CLIENTS_KEYS)),
        restoration_at=_as_local_datetime(_first(plano, _RESTORE_KEYS)),
        started_at=_as_local_datetime(_first(plano, _START_KEYS)),
        commune=_clean_text(_first(plano, _COMMUNE_KEYS)),
        sector=_clean_text(_first(plano, _SECTOR_KEYS)),
        raw=plano,
    )


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    texto = " ".join(str(value).split())
    return texto or None


def extract_records(payload: Any) -> list[Any] | None:
    """Encuentra la lista de cortes dentro del sobre. None si no hay ninguna.

    Devolver None y no `[]` es deliberado: son cosas distintas. Una lista vacía
    significa "no hay cortes ahora", que es una noche tranquila; no encontrar la
    lista significa que el esquema no es el que se esperaba, y eso necesita a una
    persona. El collector traduce esa diferencia a `success` o a `failed`.
    """
    if isinstance(payload, list):
        return payload

    if isinstance(payload, Mapping):
        for clave in _LIST_KEYS:
            valor = payload.get(clave)
            if isinstance(valor, list):
                return valor
        # Un sobre con una sola clave suele ser un envoltorio de un nivel más.
        if len(payload) == 1:
            return extract_records(next(iter(payload.values())))

    return None


def describe_shape(payload: Any, *, limit: int = 12) -> str:
    """Descripción corta de lo que llegó, para el mensaje de error.

    Existe porque el primer despliegue contra el endpoint real es donde este
    parser se va a equivocar, y la diferencia entre un mensaje que dice "no se
    encontró una lista" y uno que además enumera las claves recibidas es la
    diferencia entre depurarlo en minutos o a ciegas.
    """
    if isinstance(payload, Mapping):
        claves = sorted(str(clave) for clave in payload)[:limit]
        return f"objeto con claves: {claves}"
    if isinstance(payload, list):
        if not payload:
            return "lista vacía"
        primero = payload[0]
        if isinstance(primero, Mapping):
            claves = sorted(str(clave) for clave in primero)[:limit]
            return f"lista de {len(payload)}; el primero tiene: {claves}"
        return f"lista de {len(payload)} elementos de tipo {type(primero).__name__}"
    return f"tipo {type(payload).__name__}"


def build_external_id(company: str, outage: PowerOutage) -> str:
    """ID estable, con la empresa como namespace.

    Si la fuente entrega identificador propio, se usa: es lo que sobrevive a que
    la empresa corrija la hora de reposición o el conteo de clientes, que ocurre
    varias veces durante un corte largo.

    Si no lo entrega, se hashea **la ubicación y el inicio**, nunca los clientes
    afectados ni la reposición estimada: esos dos cambian entre lecturas, y
    meterlos en el id convertiría cada refresco en un corte nuevo — el mapa se
    llenaría de duplicados exactamente durante el evento que más importa.
    """
    if outage.outage_id:
        return f"{company}:{outage.outage_id}"

    inicio = outage.started_at.isoformat() if outage.started_at else "sin-inicio"
    semilla = f"{company}|{outage.lat:.5f}|{outage.lon:.5f}|{inicio}"
    digest = hashlib.sha256(semilla.encode("utf-8")).hexdigest()[:24]
    return f"{company}:{digest}"


__all__ = [
    "CHILE_TZ",
    "PowerOutage",
    "build_external_id",
    "describe_shape",
    "extract_records",
    "parse_outage",
]
