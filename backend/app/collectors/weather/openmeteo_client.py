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

Pocas peticiones para toda la región, pero no una sola
------------------------------------------------------

`latitude` y `longitude` aceptan listas separadas por coma, y con más de una
coordenada **la respuesta pasa de ser un objeto a ser una lista** de objetos. Eso
convierte 36 llamadas por corrida en un puñado, y por eso el presupuesto del
nivel abierto no es un problema ni siquiera consultando cada media hora (ver la
nota de cadencia en `openmeteo_worker`).

Durante un tiempo las 36 viajaron en **una** petición, y en producción eso se
rompió con `JSONDecodeError: Expecting ',' delimiter: line 1 column 95490`. El
cuerpo no venía mal formado por culpa nuestra ni por un error de la API: 36
objetos × 7 variables × 48 pasos horarios es un JSON de cientos de kB que el
nivel abierto corta a mitad de escritura. Ese modo de fallo es especialmente
malo porque **llega con HTTP 200 y sin `{"error": true}`**: no hay nada que
`raise_if_openmeteo_error` pueda ver, el único síntoma es el decodificador
reventando, y se pierde la corrida entera —las 36 comunas— por no haber podido
leer la última.

Así que las comunas se consultan en **lotes secuenciales** de `chunk_size`
(12 por defecto, ver `OPENMETEO_CHUNK_SIZE`) y las respuestas se concatenan.
Tres llamadas por corrida en vez de una son 144 al día contra un presupuesto de
10.000: el ahorro que este cliente persigue nunca fue "una sola llamada", fue
"no una llamada por comuna". A cambio, cada respuesta baja a decenas de kB —muy
lejos del punto de truncamiento— y la URL se mantiene corta, que es el otro
límite que 36 pares de coordenadas rozaban.

Secuenciales y no en paralelo, a propósito: tres peticiones simultáneas contra
un servicio público gratuito es exactamente la forma de que empiece a
responder 429, y la corrida no tiene ninguna prisa (cada media hora).

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
TEMPERATURE_KEY = "temperature_2m"
HUMIDITY_KEY = "relative_humidity_2m"
WIND_KEY = "wind_speed_10m"
GUST_KEY = "wind_gusts_10m"
UV_KEY = "uv_index"

#: Variables horarias que se piden. **Siete, y cada una participa de una
#: decisión**: ése sigue siendo el criterio, sólo que desde la v2 el collector
#: decide sobre cuatro familias de amenaza en vez de una.
#:
#: `precipitation` es lluvia + chubascos + nieve derretida, en mm por hora. Se
#: prefiere a `rain` porque en las comunas de cordillera —Los Andes, San
#: Esteban, Putaendo— parte de la precipitación cae como nieve, `rain` la
#: ignoraría, y el deshielo posterior es uno de los mecanismos de crecida del
#: Aconcagua.
#:
#: `precipitation_probability` no la publican todos los modelos: puede llegar
#: ausente o en nulos, y por eso `umbrales.py` no la usa para decidir nada.
#:
#: `temperature_2m` y `relative_humidity_2m` son dos de las tres patas del
#: 30-30-30 y, la primera, la métrica de riesgo por calor.
#:
#: **`wind_gusts_10m` Y `wind_speed_10m`, las dos.** No es redundancia: las
#: reglas se deciden con la RÁFAGA —lo que tumba una rama sobre un cable o
#: levanta una pavesa es el pico, no el promedio— pero el widget muestra el
#: viento MEDIO en su estado silencioso, porque «18 km/h» describe la tarde y
#: «47 km/h» describiría un instante que probablemente no coincide con el
#: momento en que alguien mira la pantalla.
#:
#: `uv_index` es la única variable de la lista que el modelo puede no publicar
#: según la combinación de `models`; con `best_match` viene, y si faltara el
#: `None` se propaga y la amenaza simplemente no se evalúa.
#:
#: El coste de una llamada en Open-Meteo crece con el número de variables, así
#: que pasar de 2 a 7 multiplica por ~3,5 el peso de la respuesta. Ese peso es
#: justo lo que hizo estallar la petición única de 36 comunas (ver el
#: encabezado del módulo) y lo que fija el tamaño del lote: siete variables por
#: doce comunas caben holgadamente, siete por treinta y seis no. En llamadas
#: siguen siendo tres por corrida, 144 al día, despreciable frente al
#: presupuesto de 10.000 del nivel abierto, que es lo que este cliente cuida de
#: verdad (unas pocas por corrida, no una por comuna).
HOURLY_VARIABLES: tuple[str, ...] = (
    PRECIPITATION_KEY,
    PROBABILITY_KEY,
    TEMPERATURE_KEY,
    HUMIDITY_KEY,
    WIND_KEY,
    GUST_KEY,
    UV_KEY,
)

#: Las cinco variables tácticas nuevas, con su nombre legible. Se usa para
#: construir la advertencia cuando el modelo no publica alguna: decir «falta
#: `relative_humidity_2m`» obliga a quien lea el log a traducir; decir «falta la
#: humedad relativa, así que no se evalúa el 30-30-30» dice qué se perdió.
VARIABLES_TACTICAS: dict[str, str] = {
    TEMPERATURE_KEY: "temperatura",
    HUMIDITY_KEY: "humedad relativa",
    WIND_KEY: "viento medio",
    GUST_KEY: "ráfagas",
    UV_KEY: "índice UV",
}

#: Puente entre el nombre de la variable en Open-Meteo y el atributo de
#: `PuntoHorario`. Existe para que el nombre de la API viva en UN solo sitio: el
#: día que Open-Meteo renombre `wind_gusts_10m`, se cambia acá y arriba, y
#: `umbrales.py` —que es la política y no debería saber cómo se llaman las
#: cosas en un servicio ajeno— no se entera.
_CAMPO_DE: dict[str, str] = {
    TEMPERATURE_KEY: "temp_c",
    HUMIDITY_KEY: "humedad",
    WIND_KEY: "viento_kmh",
    GUST_KEY: "rafaga_kmh",
    UV_KEY: "uv",
}

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

    #: Nombres legibles de las variables tácticas que el modelo no publicó para
    #: esta comuna. Vacío es lo normal. Ver `variables_ausentes`.
    faltantes: tuple[str, ...] = ()

    @property
    def datos_validos(self) -> int:
        """Pasos con precipitación numérica.

        Cero **no** significa que no vaya a llover: significa que no llegó el
        dato. La distinción es la que separa un invierno seco de un campo
        renombrado en la API, y el collector las trata de forma distinta.

        Sigue midiéndose **sólo sobre la precipitación**, y no sobre las cinco
        variables nuevas, a propósito: es el testigo que decide si la corrida
        entera falla (ver el `CollectorError` de `openmeteo_worker.fetch`). Un
        modelo que deje de publicar el índice UV degrada una amenaza; uno que
        deje de publicar la precipitación rompe la capa fundacional, y esas dos
        cosas no pueden compartir semáforo.
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

    # Las variables tácticas se leen igual que la probabilidad: si no vienen o
    # no son una lista, quedan vacías y cada paso horario recibe `None`.
    #
    # **Ausente NO es cero.** Es la regla que sostiene toda la política: una
    # humedad ausente leída como 0 % pondría la región entera en 30-30-30
    # crítico de forma permanente, y leída como 100 % apagaría la amenaza para
    # siempre sin que nadie se entere. `None` significa "no sabemos", y
    # `umbrales.py` no dispara ninguna regla con un "no sabemos".
    tacticas = {
        clave: horario.get(clave) if isinstance(horario.get(clave), list) else []
        for clave in VARIABLES_TACTICAS
    }

    # Las series son paralelas por contrato. Si alguna viniera más corta se
    # recorta al mínimo común en vez de reventar: perder las últimas horas de la
    # ventana es una degradación, no un fallo, y el `warn` del worker lo deja
    # anotado en `collector_runs`.
    #
    # El mínimo se toma sólo sobre tiempo y precipitación: una variable táctica
    # corta no puede recortar la ventana de lluvia, que es la capa fundacional.
    largo = min(len(momentos), len(lluvias))

    def valor(clave: str, indice: int) -> float | None:
        serie = tacticas[clave]
        return as_float(serie[indice]) if indice < len(serie) else None

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
                temp_c=valor(TEMPERATURE_KEY, indice),
                humedad=valor(HUMIDITY_KEY, indice),
                viento_kmh=valor(WIND_KEY, indice),
                rafaga_kmh=valor(GUST_KEY, indice),
                uv=valor(UV_KEY, indice),
            )
        )

    # Una variable se declara ausente cuando NINGÚN paso la trae. Que falten
    # tres horas de índice UV en una serie de 48 es normal —el modelo publica 0
    # de noche y algunos entregan nulos— y no es una degradación; que no venga
    # ni una es un campo renombrado o un modelo que no la sirve, y eso sí hay
    # que decirlo porque apaga una amenaza entera en silencio.
    faltantes = tuple(
        etiqueta
        for clave, etiqueta in VARIABLES_TACTICAS.items()
        if not any(getattr(punto, _CAMPO_DE[clave]) is not None for punto in puntos)
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
        faltantes=faltantes,
    )


def parse_lote(
    payload: Any,
    comunas: Sequence[Comuna],
    *,
    origin: str,
    max_drift: float = 0.5,
) -> tuple[list[SerieComunal], list[str]]:
    """Empareja la respuesta de UN lote con las comunas de ese lote.

    Es `parse_payload` sin el chequeo de variables ausentes, que por definición
    no se puede resolver mirando un lote: "no llegó en NINGUNA comuna" sólo se
    sabe cuando están las 36. `fetch_forecast` llama a esta función por lote y
    aplica `_faltantes_globales` una vez al final; `parse_payload` sigue siendo
    la versión de una tacada para quien tenga la respuesta completa.
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


def parse_payload(
    payload: Any,
    comunas: Sequence[Comuna],
    *,
    origin: str,
    max_drift: float = 0.5,
) -> tuple[list[SerieComunal], list[str]]:
    """Empareja una respuesta completa con las comunas pedidas. Función pura.

    Devuelve `(series, advertencias)`. Ver el encabezado del módulo para las dos
    guardas del emparejamiento por posición.
    """
    series, advertencias = parse_lote(
        payload, comunas, origin=origin, max_drift=max_drift
    )
    advertencias.extend(_faltantes_globales(series))
    return series, advertencias


def en_lotes(comunas: Sequence[Comuna], tamano: int) -> list[list[Comuna]]:
    """Parte la lista de comunas en lotes de `tamano`, conservando el orden.

    El orden es la única cosa que empareja cada pronóstico con su comuna (ver el
    encabezado), así que se conserva dentro de cada lote y entre lotes: concatenar
    los resultados en el mismo orden en que se pidieron los lotes reconstruye
    exactamente la lista original.

    `tamano` por debajo de 1 se trata como 1 en vez de reventar con un
    `range(0)` infinito o una lista vacía silenciosa. La validación de verdad la
    hace el `Field(ge=1)` de `settings`; esto es la red por si alguien construye
    el cliente a mano.
    """
    paso = max(1, tamano)
    return [list(comunas[inicio : inicio + paso]) for inicio in range(0, len(comunas), paso)]


def _faltantes_globales(series: Sequence[SerieComunal]) -> list[str]:
    """Variables tácticas que no llegaron en NINGUNA comuna.

    # Por qué el umbral es "en todas" y no "en alguna"

    Es la misma lección que dejó el estado `partial` permanente del USGS, y vale
    la pena escribirla porque la tentación de avisar por cada hueco es fuerte.

    Que a una comuna le falte el índice UV en tres de cuarenta y ocho pasos es
    normal: los modelos publican cero de noche y algunos entregan nulos. Avisar
    de eso dejaría este collector en `partial` todas las noches, y un estado que
    está siempre en ámbar deja de significar nada — cuando de verdad se rompa
    algo, nadie lo va a distinguir del ruido de fondo.

    Que una variable no llegue en **ninguna** de las 36 comunas es otra cosa:
    o el modelo dejó de servirla o le cambiaron el nombre, y en cualquiera de
    los dos casos hay una amenaza entera que se apagó sin avisar. Ese es el
    fallo silencioso que este proyecto persigue, y por eso sí levanta la corrida
    a `partial` — una vez, con un mensaje que dice qué se perdió.
    """
    if not series:
        return []

    ausentes_en_todas = set(series[0].faltantes)
    for serie in series[1:]:
        ausentes_en_todas &= set(serie.faltantes)

    if not ausentes_en_todas:
        return []

    # Se ordena por el orden declarado en `VARIABLES_TACTICAS` y no por el del
    # conjunto: un mensaje que cambia de orden entre corridas es un mensaje que
    # no se puede deduplicar ni buscar en el log.
    nombres = [
        etiqueta for etiqueta in VARIABLES_TACTICAS.values() if etiqueta in ausentes_en_todas
    ]
    return [
        f"ninguna de las {len(series)} comunas trajo "
        f"{', '.join(nombres)}: la amenaza que depende de esas variables no se "
        f"evaluará. Revisar si el modelo dejó de publicarlas o si les cambiaron "
        f"el nombre en la API"
    ]


def _deriva(serie: SerieComunal) -> float | None:
    """Distancia en grados entre el punto pedido y el centro de celda devuelto."""
    if serie.lat_grilla is None or serie.lon_grilla is None:
        return None
    return max(
        abs(serie.lat_grilla - serie.comuna.lat),
        abs(serie.lon_grilla - serie.comuna.lon),
    )


class OpenMeteoClient:
    """Descarga el pronóstico horario de las comunas en lotes secuenciales."""

    def __init__(
        self,
        *,
        comunas: Sequence[Comuna] | None = None,
        url: str | None = None,
        timeout: float | None = None,
        forecast_days: int | None = None,
        model: str | None = None,
        max_drift: float | None = None,
        chunk_size: int | None = None,
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
        self.chunk_size = max(
            1,
            chunk_size if chunk_size is not None else settings.OPENMETEO_CHUNK_SIZE,
        )

    @property
    def origin(self) -> str:
        return f"open-meteo:{self.url}"

    def lotes(self) -> list[list[Comuna]]:
        """Las comunas repartidas en peticiones. Ver `en_lotes`."""
        return en_lotes(self.comunas, self.chunk_size)

    def params(self, comunas: Sequence[Comuna] | None = None) -> dict[str, str]:
        """Parámetros de la consulta de UN lote.

        Sin argumento pide todas las comunas de golpe, que es lo que hacía antes
        de existir los lotes: se conserva porque es la forma en que los tests
        inspeccionan la consulta y porque con pocas comunas sigue siendo un solo
        lote.

        Tres decisiones que no son obvias:

        * ``forecast_days=2`` — la ventana táctica máxima del sistema es de 24 h
          móviles, así que a las 22:00 hacen falta las horas de mañana y ninguna
          más. El defecto de la API son **siete días**, y esos cinco de más eran
          la mayor parte del payload que se truncaba en producción (ver el
          encabezado del módulo). Sale de `settings`, no está fijo acá, pero el
          motivo por el que no se sube está escrito en `OPENMETEO_FORECAST_DAYS`.

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
        objetivo = list(self.comunas if comunas is None else comunas)
        return {
            "latitude": ",".join(f"{comuna.lat:.4f}" for comuna in objetivo),
            "longitude": ",".join(f"{comuna.lon:.4f}" for comuna in objetivo),
            "hourly": ",".join(HOURLY_VARIABLES),
            "forecast_days": str(self.forecast_days),
            "timezone": "UTC",
            "cell_selection": "land",
            "models": self.model,
        }

    async def fetch_forecast(self) -> tuple[list[SerieComunal], list[str]]:
        """Devuelve `(series, advertencias)`, consultando lote por lote.

        El transporte es `geoservices.request_json`, igual que en las capas
        institucionales: de ahí salen los reintentos con espera exponencial ante
        5xx y errores de red, el fallo inmediato ante 4xx —un 400 de Open-Meteo es
        un parámetro mal escrito y reintentarlo sólo retrasa el diagnóstico—, la
        detección de HTML de portal caído y la conversión de cualquier excepción
        de httpx (timeout, DNS, TLS) en `CollectorError`. Escribir un manejo de
        red propio acá habría sido volver a resolver todo eso peor.

        Los lotes comparten **un solo `AsyncClient`**: reusa la conexión TCP y el
        handshake TLS, así que tres peticiones secuenciales cuestan poco más que
        una en tiempo de pared. Y son secuenciales dentro del mismo `async with`,
        no un `gather`, por lo que dice el encabezado del módulo.

        Un lote que falle sube su `CollectorError` y corta la corrida entera. Es
        deliberado: media región pronosticada y media en silencio se vería en el
        widget exactamente igual que media región sin amenazas, y ése es el fallo
        silencioso que este proyecto persigue. Lo que sí cambia respecto de la
        petición única es que el mensaje ahora dice **qué lote** falló, que con 36
        comunas en una sola llamada era imposible de acotar.
        """
        lotes = self.lotes()
        series: list[SerieComunal] = []
        advertencias: list[str] = []

        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            for numero, lote in enumerate(lotes, start=1):
                # El origen lleva el lote sólo cuando hay más de uno: con una
                # sola petición, un "[lote 1/1]" pegado a cada mensaje de error
                # es ruido que además rompería los mensajes que ya se leen en
                # los tests y en los logs históricos.
                origen = (
                    self.origin
                    if len(lotes) == 1
                    else f"{self.origin} [lote {numero}/{len(lotes)}]"
                )
                payload = await request_json(
                    client, self.url, self.params(lote), origin=origen
                )
                parciales, avisos = parse_lote(
                    payload, lote, origin=origen, max_drift=self.max_drift
                )
                series.extend(parciales)
                advertencias.extend(avisos)

        # Una vez, sobre las 36: ver `_faltantes_globales` para por qué el umbral
        # es "en ninguna comuna" y por qué eso no se puede decidir por lote.
        advertencias.extend(_faltantes_globales(series))

        logger.debug(
            "pronóstico leído",
            extra={
                "origin": self.origin,
                "comunas": len(series),
                "lotes": len(lotes),
                "advertencias": len(advertencias),
            },
        )
        return series, advertencias
