"""Lectura del JSON nativo de ArcGIS que publica Vialidad.

Por qué no se reutiliza `ArcGisFeatureClient`
----------------------------------------------
El proyecto ya tiene un cliente de ArcGIS —el que usa CONAF— y la primera
intención fue apuntarlo acá. No sirve, por dos motivos comprobados contra el
servicio real y no deducidos del manual de Esri:

1. **No hay GeoJSON.** `ArcGisFeatureClient` pide `f=geojson` a propósito, para
   no traducir la geometría propietaria. Este MapServer corre ArcGIS 10.21 y
   declara `supportedQueryFormats: "JSON, AMF"`. Pedirle `f=geojson` devuelve
   **cuerpo vacío** —no un error—, que es justamente la forma de fallo que el
   proyecto persigue: el parser vería cero emergencias y la corrida diría
   `success`.
2. **No hay paginación.** El cliente pagina con `resultOffset` /
   `resultRecordCount`. Este servicio **ignora ambos**: se le pidió
   `resultRecordCount=2` y devolvió los 30 registros. Un bucle de paginación
   contra un servidor que no pagina relee la misma página hasta el tope y
   multiplica los eventos.

De ahí este módulo: lee el JSON de Esri directamente. Es poco código y no toca
el camino de CONAF, que funciona.

Qué hay dentro de una respuesta
--------------------------------
::

    {"displayFieldName": "ESTADO",
     "geometryType": "esriGeometryPoint",
     "spatialReference": {"wkid": 4326},
     "fields": [...],
     "features": [{"attributes": {...}, "geometry": {"x": lon, "y": lat}}]}

**`x` es LONGITUD e `y` es LATITUD.** Es el mismo orden cartográfico de Waze y
la misma trampa: quien asuma que `x` es la primera coordenada «como en lat/lon»
deposita las emergencias de Valparaíso en el Índico. Se lee por nombre y se
valida el rango.

Las fechas (`esriFieldTypeDate`) son **epoch en milisegundos UTC**. Leerlas como
segundos las manda a 1970 y cualquier filtro por antigüedad las descarta todas.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.collectors.geoservices import (
    as_float,
    detect_service_error,
    raise_if_service_error,
)
from app.core.exceptions import CollectorError

logger = logging.getLogger(__name__)

#: Clave donde ArcGIS deja las filas. Si falta, no hubo consulta válida.
FEATURES_KEY = "features"

#: Estados de transitabilidad que publica Vialidad, de mejor a peor. El orden
#: importa: es el que usa `severity_rank` para que la UI pueda ordenar sin
#: reimplementar la escala.
TRANSITO_ORDEN: tuple[str, ...] = (
    "Operativo",
    "Parcialmente Operativo",
    "No Operativo",
)

#: Gravedad declarada por Vialidad. Cuatro niveles, verificados contra la
#: respuesta real: no es un campo libre.
GRAVEDAD_ORDEN: tuple[str, ...] = ("Leve", "Moderado", "Grave", "Muy Grave")


@dataclass(frozen=True, slots=True)
class RoadEmergency:
    """Una emergencia vial vigente, ya desenvuelta y con los tipos resueltos.

    Existe por la misma razón que `WazeAlert` y `SeismicRecord`: que
    `normalize()` sea una función pura sobre datos planos y el mapeo se pueda
    testear con una respuesta real guardada, sin tocar la red.
    """

    correlativo: str
    lat: float
    lon: float
    transito: str | None
    estado: str | None
    gravedad: str | None
    restriccion: str | None
    camino: str | None
    rol: str | None
    resumen: str | None
    detalle: str | None
    ocurrida_en: datetime | None
    raw: Mapping[str, Any]

    @property
    def es_transitable(self) -> bool:
        """False sólo cuando Vialidad declara la ruta cortada.

        `None` se considera transitable a propósito: un campo vacío no es una
        declaración de corte, y pintar de rojo una ruta por un dato ausente es
        peor que no pintarla.
        """
        return self.transito != "No Operativo"


def parse_features(payload: Any, *, origin: str) -> list[Mapping[str, Any]]:
    """Valida el sobre de ArcGIS y devuelve la lista cruda de `features`.

    Distingue cuatro cosas que desde arriba se ven iguales y significan lo
    contrario:

    * **Error de ArcGIS con HTTP 2xx.** Responde 200 con
      `{"error": {"code": …, "message": …, "details": [...]}}` cuando la
      consulta es inválida. Lo atrapa `raise_if_service_error`, que existe en
      este proyecto desde CONAF precisamente para esta forma anidada.
    * **Sobre de error de una pasarela, también con HTTP 2xx.** Es plano
      —`{code, message, status}` en la raíz— y lo atrapa `detect_service_error`,
      la pieza que se añadió tras el incidente de Chilquinta.

      Hacen falta **los dos**, y el orden importa: `detect_service_error` trae
      una guarda que descarta cualquier cuerpo con una lista dentro, porque un
      volcado de datos siempre trae la suya. El `details` de ArcGIS ES una
      lista, así que por sí solo dejaría pasar el error anidado como si fueran
      datos. Se comprobó con el payload real de una consulta inválida.
    * **`features` ausente.** No es «no hay emergencias»: es que la respuesta no
      es la de una consulta. Levanta.
    * **`features` vacío.** Sí es «no hay emergencias», y es un estado normal.
      Devuelve lista vacía sin ruido.
    """
    if payload is None or payload == "":
        # El cuerpo vacío es el síntoma exacto de haber pedido `f=geojson` a este
        # servidor. Se nombra en el mensaje porque es el error que alguien va a
        # cometer al «mejorar» este módulo para alinearlo con el de CONAF.
        raise CollectorError(
            f"{origin}: el servicio devolvió un cuerpo vacío. Suele significar "
            f"que se pidió un formato que no soporta: este MapServer sólo "
            f"entrega 'JSON, AMF', y ante `f=geojson` responde vacío en vez de "
            f"un error.",
        )

    if not isinstance(payload, Mapping):
        raise CollectorError(
            f"{origin}: se esperaba un objeto JSON de ArcGIS, " f"llegó {type(payload).__name__}.",
        )

    # Primero la forma anidada de ArcGIS, que es la que este servicio produce.
    raise_if_service_error(payload, origin=origin)

    # Y después la plana, por si alguna vez se interpone un CDN o un WAF entre
    # nosotros y el MOP — que es exactamente lo que le pasó a Chilquinta.
    sobre = detect_service_error(payload)
    if sobre is not None:
        raise CollectorError(
            f"{origin}: el servicio respondió con un error dentro de una "
            f"respuesta HTTP 2xx — {sobre.describe()}. No es un cambio de "
            f"formato: la consulta no llegó a ejecutarse.",
            detail={
                "server_message": sobre.message,
                "server_code": sobre.code,
                "transient": sobre.transient,
            },
        )

    features = payload.get(FEATURES_KEY)
    if features is None:
        raise CollectorError(
            f"{origin}: la respuesta no trae '{FEATURES_KEY}'. Claves "
            f"recibidas: {sorted(payload)}.",
        )
    if not isinstance(features, list):
        raise CollectorError(
            f"{origin}: '{FEATURES_KEY}' debería ser una lista, llegó "
            f"{type(features).__name__}.",
        )

    return [fila for fila in features if isinstance(fila, Mapping)]


def parse_emergency(feature: Any) -> RoadEmergency | None:
    """Convierte un `feature` de ArcGIS en `RoadEmergency`. None si es inservible.

    Descarta y devuelve None en vez de lanzar: un registro sin coordenada es un
    dato incompleto de la fuente, no una señal de que el formato cambió. Perder
    la corrida entera —y con ella las otras 29 emergencias— por una fila mocha
    sería cambiar un dato faltante por treinta.

    Quien necesite saber cuántas se cayeron lo tiene en el contador de
    `load_records`, no en una excepción.
    """
    if not isinstance(feature, Mapping):
        return None

    atributos = feature.get("attributes")
    if not isinstance(atributos, Mapping):
        return None

    geometria = feature.get("geometry")
    if not isinstance(geometria, Mapping):
        return None

    # x = LONGITUD, y = LATITUD. Ver el encabezado del módulo.
    lon = as_float(geometria.get("x"))
    lat = as_float(geometria.get("y"))
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None

    correlativo = _correlativo(atributos.get("CORRELATIVO"))
    if correlativo is None:
        # Sin identificador estable no hay idempotencia posible: cada corrida
        # insertaría la misma emergencia otra vez. Mejor perderla.
        return None

    return RoadEmergency(
        correlativo=correlativo,
        lat=lat,
        lon=lon,
        transito=_texto(atributos.get("TRANSITO")),
        estado=_texto(atributos.get("ESTADO")),
        gravedad=_texto(atributos.get("NIVEL_DE_GRAVEDAD")),
        restriccion=_texto(atributos.get("RESTRICCION")),
        camino=_texto(atributos.get("NOMBRE_CAMINO")),
        rol=_texto(atributos.get("ROL")),
        resumen=_texto(atributos.get("RESUMEN")) or _texto(atributos.get("RESUMEN_EMERGENCIA")),
        detalle=_texto(atributos.get("DESCRIPCION_DETALLADA")) or _texto(atributos.get("DETALLE")),
        ocurrida_en=from_epoch_millis(atributos.get("FECHA_EMERGENCIA")),
        raw=dict(atributos),
    )


def _correlativo(value: Any) -> str | None:
    """`CORRELATIVO` viene como Double y se usa como identificador.

    Se normaliza a entero en texto: `14868.0` y `14868` tienen que producir el
    mismo `external_id`, o el upsert deja de reconocer la fila y la duplica en
    la primera corrida en que el servidor cambie de representación.
    """
    numero = as_float(value)
    if numero is None:
        texto = _texto(value)
        return texto
    return str(int(numero))


def _texto(value: Any) -> str | None:
    """Cadena limpia, o None. Los nulos de este servicio son reales y frecuentes.

    En la muestra verificada, `RESTRICCION` viene `null` en 15 de 30 registros y
    hay una fila con `CLASE` y `TIPO` nulos. Un `str(None)` acá pondría la
    cadena "None" en la ficha del mapa.
    """
    if value is None:
        return None
    texto = str(value).strip()
    return texto or None


def from_epoch_millis(value: Any) -> datetime | None:
    """Epoch en milisegundos → datetime UTC.

    ArcGIS publica `esriFieldTypeDate` en milisegundos. Interpretarlos como
    segundos situaría todo en 1970; multiplicar de más, en el año 58000. Ambos
    extremos se descartan con el rango de abajo en vez de propagarlos.
    """
    millis = as_float(value)
    if millis is None or millis <= 0:
        return None
    try:
        momento = datetime.fromtimestamp(millis / 1000.0, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
    # Una emergencia vial no puede ser anterior a que existiera la Dirección de
    # Vialidad en formato digital, ni posterior al próximo siglo. Fuera de eso
    # es un campo mal leído, y vale más un None que una fecha absurda.
    if not (1990 <= momento.year <= 2100):
        return None
    return momento


def severity_rank(emergency: RoadEmergency) -> int:
    """Orden de atención, de 0 (menor) a 5 (ruta cortada y grave).

    Combina las dos escalas que Vialidad publica por separado, y **la
    transitabilidad manda**: la gravedad sólo desempata dentro de un mismo
    estado de la vía. Una ruta cortada por algo leve queda por encima de una
    abierta con daño muy grave, porque quien mira el mapa decide si puede pasar
    y no cuánto le costará al MOP repararla.

    La aritmética: `transito` aporta 0, 2 o 4 —los tres estados— y la gravedad
    suma 1 sólo a partir de «Grave». Así ningún salto de gravedad alcanza para
    cruzar un escalón de transitabilidad.
    """
    transito = (
        TRANSITO_ORDEN.index(emergency.transito) if emergency.transito in TRANSITO_ORDEN else 0
    )
    gravedad = (
        GRAVEDAD_ORDEN.index(emergency.gravedad) if emergency.gravedad in GRAVEDAD_ORDEN else 0
    )
    return transito * 2 + (1 if gravedad >= 2 else 0)


def build_text(emergency: RoadEmergency) -> str:
    """Descripción legible. El servicio no trae una sola frase armada.

    Se antepone el estado de la vía porque es lo único accionable: quien mira el
    mapa decide si puede pasar, no qué le ocurrió al pavimento.
    """
    cabeza = emergency.transito or "Emergencia vial"
    donde = emergency.camino or emergency.rol or "ruta sin identificar"
    partes = [f"{cabeza}: {donde}"]

    if emergency.resumen:
        partes.append(emergency.resumen)
    if emergency.restriccion:
        partes.append(f"Restricción: {emergency.restriccion}")
    if emergency.gravedad:
        partes.append(f"gravedad {emergency.gravedad.lower()}")

    return " · ".join(partes) + " · Vialidad (MOP)"


def summarise(emergencies: Sequence[RoadEmergency]) -> dict[str, int]:
    """Conteo por transitabilidad, para `run_params` y el log.

    Sirve para que una corrida diga «30 emergencias, 11 rutas cortadas» en vez
    de sólo «30», que es el número que no permite notar nada.
    """
    conteo: dict[str, int] = {}
    for emergency in emergencies:
        clave = emergency.transito or "sin dato"
        conteo[clave] = conteo.get(clave, 0) + 1
    return conteo


__all__ = [
    "FEATURES_KEY",
    "GRAVEDAD_ORDEN",
    "TRANSITO_ORDEN",
    "RoadEmergency",
    "build_text",
    "from_epoch_millis",
    "parse_emergency",
    "parse_features",
    "severity_rank",
    "summarise",
]
