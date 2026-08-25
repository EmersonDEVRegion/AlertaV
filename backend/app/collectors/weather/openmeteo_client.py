"""Cliente de la API de pronóstico de Open-Meteo.

Por qué Open-Meteo y no otra
----------------------------

Se evaluaron tres caminos:

* **Open-Meteo** (`api.open-meteo.com/v1/forecast`) — sin credencial, sin
  registro, 10.000 llamadas al día en su nivel abierto, y `best_match` elige por
  coordenada el modelo de mayor resolución disponible (ECMWF, GFS, ICON…). Es lo
  que se usa acá.
* **Dirección Meteorológica de Chile (DMC)** — es la autoridad nacional y sería
  la fuente institucional preferible por el mismo criterio que hace de CONAF la
  fuente de incendios: sus servicios de datos exigen convenio y su catálogo
  abierto entrega observación de estaciones, no pronóstico horario por comuna. Es
  el reemplazo natural del día que exista el convenio; el resto del módulo no
  cambiaría, sólo este archivo.
* **Flood API de Open-Meteo** (GloFAS, `flood-api.open-meteo.com`) — pronóstico
  de **caudal de río** en m³/s. Suena a lo que se busca y no lo es: su grilla es
  de 5 km sobre cauces grandes y resolución **diaria**, así que describe la crecida
  del Aconcagua, no el chubasco que anega la Avenida España. Vale la pena como
  segunda capa para el valle del Aconcagua —Quillota, La Calera, Los Andes— y está
  fuera del alcance de este collector, que es de precipitación.

Una sola petición para toda la región
--------------------------------------

`latitude` y `longitude` aceptan listas separadas por coma, y con más de una
coordenada **la respuesta pasa de ser un objeto a ser una lista** de objetos. Las
36 comunas caben en una única petición HTTP: es la diferencia entre 36 llamadas
por corrida y una, y por eso el presupuesto del nivel abierto no es un problema
ni siquiera consultando cada media hora (ver la nota de cadencia en
`openmeteo_worker`).

El emparejamiento es por POSICIÓN, y eso hay que vigilarlo
-----------------------------------------------------------

La respuesta no trae ningún identificador de la ubicación: la documentación sólo
añade `location_id` en los formatos CSV y XLSX, no en JSON. Lo único que empareja
cada objeto con su comuna es **el orden**, que es lo que la API promete
implícitamente al aceptar listas paralelas.

Depender de un orden no verificado sería exactamente el tipo de suposición que
en este proyecto ya salió cara una vez (ver la historia de `/obtieneImage` en
`chilquinta_worker`), así que hay dos guardas:

1. Si la lista no trae **exactamente** tantos objetos como comunas se pidieron,
   la corrida falla con el conteo en el mensaje. Nunca se emparejan a medias.
2. Cada objeto declara la coordenada del **centro de la celda de grilla** que
   usó, que puede estar a varios kilómetros del punto pedido. Si esa coordenada
   se aleja más de `max_drift` grados de la comuna que le tocó, se avisa: es la
   señal de que el orden se movió. No falla, porque un desfase grande también
   puede ser una celda legítima en zona costera; queda como degradación y la
   corrida termina en `partial`.

La comparación es en grados sin corregir por latitud. A -33° un grado de longitud
son ~93 km y uno de latitud ~111 km: la diferencia no importa para detectar una
lista desordenada, que es lo único que esta guarda persigue.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from app.collectors.geoservices import as_float, parse_timestamp, request_json
from app.collectors.weather.comunas import Comuna, parse_comunas
from app.collectors.weather.umbrales import PuntoHorario
from app.core.config import settings
from app.core.exceptions import CollectorError

logger = logging.getLogger(__name__)

HOURLY_KEY = "hourly"
TIME_KEY = "time"
PRECIPITATION_KEY = "precipitation"
PROBABILITY_KEY = "precipitation_probability"

#: Variables horarias que se piden. Dos y no veinte: el coste de una llamada en
#: Open-Meteo crece con el número de variables, y todo lo demás —temperatura,
#: viento, presión— no participa de ninguna decisión de este collector.
#:
#: `precipitation` es lluvia + chubascos + nieve derretida, en mm por hora. Se
#: prefiere a `rain` porque en las comunas de cordillera —Los Andes, San
#: Esteban, Putaendo— parte de la precipitación cae como nieve, `rain` la
#: ignoraría, y el deshielo posterior es uno de los mecanismos de crecida del
#: Aconcagua.
#:
#: `precipitation_probability` no la publican todos los modelos: puede llegar
#: ausente o en nulos, y por eso `umbrales.py` no la usa para decidir nada.
HOURLY_VARIABLES: tuple[str, ...] = (PRECIPITATION_KEY, PROBABILITY_KEY)

#: Cabecera de cortesía. Ningún servicio público obliga, pero identificarse
#: permite que el operador del servicio sepa a quién escribirle antes de
#: bloquear, y ya evitó un 403 con el USGS.
USER_AGENT = "AlertaV/1.0 (+https://github.com/alertav)"


@dataclass(frozen=True, slots=True)
class SerieComunal:
    """La serie horaria de una comuna, ya tipada.

    Existe para que `evaluar()` y `normalize()` operen sobre datos planos: los
    tests construyen `SerieComunal` a mano y no tocan la red.
    """

    comuna: Comuna
    puntos: tuple[PuntoHorario, ...]
    lat_grilla: float | None
    lon_grilla: float | None
    unidades: Mapping[str, str]

    @property
    def datos_validos(self) -> int:
        """Pasos con precipitación numérica.

        Cero **no** significa que no vaya a llover: significa que no llegó el
        dato. La distinción es la que separa un invierno seco de un campo
        renombrado en la API, y el collector las trata de forma distinta.
        """
        return sum(1 for punto in self.puntos if punto.mm is not None)


def describir_forma(payload: Any) -> dict[str, Any]:
    """Diagnóstico de lo que llegó, para el mensaje de error.

    Cuando una fuente cambia de forma, el mensaje tiene que decir *qué* llegó y
    no sólo que no se pudo leer. Es lo que hace `describe_kmz` con el archivo de
    CGE y lo que convierte una corrida roja en un arreglo de diez minutos.
    """
    if isinstance(payload, list):
        primero = payload[0] if payload else None
        return {
            "tipo": "list",
            "elementos": len(payload),
            "claves_primer_elemento": sorted(map(str, primero))
            if isinstance(primero, Mapping)
            else None,
        }
    if isinstance(payload, Mapping):
        horario = payload.get(HOURLY_KEY)
        return {
            "tipo": "dict",
            "claves": sorted(map(str, payload))[:15],
            "claves_hourly": sorted(map(str, horario))
            if isinstance(horario, Mapping)
            else None,
        }
    return {"tipo": type(payload).__name__}


def raise_if_openmeteo_error(payload: Any, *, origin: str) -> None:
    """Open-Meteo señala el error con `{"error": true, "reason": "..."}`.

    No sirve `geoservices.raise_if_service_error` acá: ese helper espera que el
    valor de `error` sea el mensaje —un string o un objeto, como en ArcGIS— y con
    un booleano produciría "el servicio devolvió un error: True", perdiendo justo
    la parte útil. El motivo de Open-Meteo es específico y accionable
    ("Cannot initialize WeatherVariable from invalid String value…"), así que se
    extrae a mano.

    Normalmente el error viene con HTTP 400 y `request_response` ya lo convierte
    en `CollectorError` con el cuerpo incluido. Esto cubre el caso de que alguna
    vez llegue con un 200 por delante, que es el modo de fallo que este proyecto
    ha visto en casi todas las demás fuentes.
    """
    if not isinstance(payload, Mapping):
        return
    if not payload.get("error"):
        return
    motivo = payload.get("reason") or payload.get("error")
    raise CollectorError(
        f"{origin}: la API rechazó la consulta — {str(motivo)[:300]}",
        detail={"payload": describir_forma(payload)},
    )


def _como_lista(payload: Any, *, origin: str) -> list[Mapping[str, Any]]:
    """Una coordenada devuelve un objeto; varias, una lista. Se unifica."""
    if isinstance(payload, Mapping):
        return [payload]
    if isinstance(payload, list):
        elementos = [item for item in payload if isinstance(item, Mapping)]
        if len(elementos) != len(payload):
            raise CollectorError(
                f"{origin}: la lista de pronósticos trae elementos que no son objetos",
                detail=describir_forma(payload),
            )
        return elementos
    raise CollectorError(
        f"{origin}: se esperaba un objeto o una lista y llegó {type(payload).__name__}",
        detail=describir_forma(payload),
    )


def parse_serie(item: Mapping[str, Any], comuna: Comuna, *, origin: str) -> SerieComunal:
    """Convierte un objeto de la respuesta en la serie horaria de una comuna.

    Falla —no devuelve una serie vacía— cuando falta la estructura mínima. Una
    serie vacía se confundiría con "no va a llover", y ese es el error que no se
    puede cometer: dejaría la capa apagada durante un temporal con la corrida en
    verde.
    """
    horario = item.get(HOURLY_KEY)
    if not isinstance(horario, Mapping):
        raise CollectorError(
            f"{origin}: la respuesta de {comuna.nombre} no trae bloque '{HOURLY_KEY}'",
            detail=describir_forma(item),
        )

    momentos = horario.get(TIME_KEY)
    lluvias = horario.get(PRECIPITATION_KEY)
    if not isinstance(momentos, list) or not isinstance(lluvias, list):
        raise CollectorError(
            f"{origin}: {comuna.nombre} sin series '{TIME_KEY}'/'{PRECIPITATION_KEY}' "
            f"utilizables",
            detail=describir_forma(item),
        )

    probabilidades = horario.get(PROBABILITY_KEY)
    probabilidades = probabilidades if isinstance(probabilidades, list) else []

    # Las tres series son paralelas por contrato. Si alguna viniera más corta se
    # recorta al mínimo común en vez de reventar: perder las últimas horas de la
    # ventana es una degradación, no un fallo, y el `warn` del worker lo deja
    # anotado en `collector_runs`.
    largo = min(len(momentos), len(lluvias))

    puntos: list[PuntoHorario] = []
    for indice in range(largo):
        momento = parse_timestamp(momentos[indice])
        if momento is None:
            continue
        probabilidad = (
            as_float(probabilidades[indice]) if indice < len(probabilidades) else None
        )
        puntos.append(
            PuntoHorario(
                momento=momento,
                mm=as_float(lluvias[indice]),
                probabilidad=None if probabilidad is None else int(probabilidad),
            )
        )

    unidades = item.get(f"{HOURLY_KEY}_units")
    return SerieComunal(
        comuna=comuna,
        puntos=tuple(puntos),
        lat_grilla=as_float(item.get("latitude")),
        lon_grilla=as_float(item.get("longitude")),
        unidades={str(k): str(v) for k, v in unidades.items()}
        if isinstance(unidades, Mapping)
        else {},
    )


def parse_payload(
    payload: Any,
    comunas: Sequence[Comuna],
    *,
    origin: str,
    max_drift: float = 0.5,
) -> tuple[list[SerieComunal], list[str]]:
    """Empareja la respuesta con las comunas pedidas. Función pura.

    Devuelve `(series, advertencias)`. Ver el encabezado del módulo para las dos
    guardas del emparejamiento por posición.
    """
    raise_if_openmeteo_error(payload, origin=origin)
    elementos = _como_lista(payload, origin=origin)

    if len(elementos) != len(comunas):
        raise CollectorError(
            f"{origin}: se pidieron {len(comunas)} comunas y llegaron "
            f"{len(elementos)} pronósticos; el emparejamiento por posición no es "
            f"seguro",
            detail=describir_forma(payload),
        )

    series: list[SerieComunal] = []
    advertencias: list[str] = []
    for item, comuna in zip(elementos, comunas, strict=True):
        serie = parse_serie(item, comuna, origin=origin)
        deriva = _deriva(serie)
        if deriva is not None and deriva > max_drift:
            advertencias.append(
                f"el pronóstico emparejado con {comuna.nombre} declara una celda a "
                f"{deriva:.2f}° del punto pedido (máximo {max_drift:.2f}°); "
                f"revisar el orden de la respuesta"
            )
        if serie.datos_validos == 0:
            advertencias.append(
                f"la serie de {comuna.nombre} llegó sin ningún valor de "
                f"precipitación utilizable"
            )
        series.append(serie)

    return series, advertencias


def _deriva(serie: SerieComunal) -> float | None:
    """Distancia en grados entre el punto pedido y el centro de celda devuelto."""
    if serie.lat_grilla is None or serie.lon_grilla is None:
        return None
    return max(
        abs(serie.lat_grilla - serie.comuna.lat),
        abs(serie.lon_grilla - serie.comuna.lon),
    )


class OpenMeteoClient:
    """Descarga el pronóstico horario de todas las comunas en una petición."""

    def __init__(
        self,
        *,
        comunas: Sequence[Comuna] | None = None,
        url: str | None = None,
        timeout: float | None = None,
        forecast_days: int | None = None,
        model: str | None = None,
        max_drift: float | None = None,
    ) -> None:
        self.comunas: list[Comuna] = (
            list(comunas)
            if comunas is not None
            else parse_comunas(settings.OPENMETEO_COMUNAS or None)
        )
        if not self.comunas:
            raise CollectorError(
                "no hay comunas configuradas para el pronóstico meteorológico"
            )
        self.url = url or settings.OPENMETEO_URL
        self.timeout = timeout if timeout is not None else settings.OPENMETEO_TIMEOUT_SECONDS
        self.forecast_days = (
            forecast_days if forecast_days is not None else settings.OPENMETEO_FORECAST_DAYS
        )
        self.model = model or settings.OPENMETEO_MODEL
        self.max_drift = (
            max_drift if max_drift is not None else settings.OPENMETEO_MAX_DRIFT_DEGREES
        )

    @property
    def origin(self) -> str:
        return f"open-meteo:{self.url}"

    def params(self) -> dict[str, str]:
        """Parámetros de la consulta.

        Dos decisiones que no son obvias:

        * ``timezone=UTC`` — las horas vuelven como ``2026-08-25T14:00`` sin
          desfase y `parse_timestamp` las ancla en UTC sin adivinar nada. Pedir
          hora local obligaría a reconstruir el desfase desde
          ``utc_offset_seconds``, que es una conversión más donde equivocarse; la
          hora chilena se calcula al final, sólo para el texto que lee una
          persona (`umbrales.describir`).
        * ``cell_selection=land`` — la mitad de las comunas de esta región son
          costeras y una celda de mar tiene otro régimen de precipitación.
          Explícito y no por defecto porque la propia documentación de Open-Meteo
          se contradice sobre cuál es el valor por defecto de este parámetro.
        """
        return {
            "latitude": ",".join(f"{comuna.lat:.4f}" for comuna in self.comunas),
            "longitude": ",".join(f"{comuna.lon:.4f}" for comuna in self.comunas),
            "hourly": ",".join(HOURLY_VARIABLES),
            "forecast_days": str(self.forecast_days),
            "timezone": "UTC",
            "cell_selection": "land",
            "models": self.model,
        }

    async def fetch_forecast(self) -> tuple[list[SerieComunal], list[str]]:
        """Devuelve `(series, advertencias)`.

        El transporte es `geoservices.request_json`, igual que en las capas
        institucionales: de ahí salen los reintentos con espera exponencial ante
        5xx y errores de red, el fallo inmediato ante 4xx —un 400 de Open-Meteo es
        un parámetro mal escrito y reintentarlo sólo retrasa el diagnóstico—, la
        detección de HTML de portal caído y la conversión de cualquier excepción
        de httpx (timeout, DNS, TLS) en `CollectorError`. Escribir un manejo de
        red propio acá habría sido volver a resolver todo eso peor.
        """
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            payload = await request_json(
                client, self.url, self.params(), origin=self.origin
            )

        series, advertencias = parse_payload(
            payload, self.comunas, origin=self.origin, max_drift=self.max_drift
        )
        logger.debug(
            "pronóstico leído",
            extra={
                "origin": self.origin,
                "comunas": len(series),
                "advertencias": len(advertencias),
            },
        )
        return series, advertencias
