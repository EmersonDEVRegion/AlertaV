"""Geocodificación contra Nominatim (OpenStreetMap), con el rate limit respetado.

Por qué este módulo existe aparte
---------------------------------
Nominatim es un servicio donado. Su política de uso permite **1 petición por
segundo por IP**, y el incumplimiento no se castiga con un 429 que uno pueda
reintentar: se castiga bloqueando la IP, a veces de forma permanente y sin
aviso. Para este proyecto eso significaría perder la única vía de
georreferenciar los avisos del MTT — y perderla en silencio, porque el worker
seguiría corriendo y reportando cero geocodificaciones exitosas.

De ahí las dos decisiones que ordenan el archivo:

**El limitador es global al proceso, no por worker.** Nominatim cuenta por IP y
todo el backend sale por una sola. Un limitador por instancia de collector daría
1 req/s *cada uno*, que es exactamente la forma de superar el límite creyendo
que se lo respeta. `_LIMITER` es un singleton de módulo, y con collectors y
correlación compartiendo un intérprete (`app/workers.py`) eso cubre todo el
backend. Si algún día los workers vuelven a procesos separados, esta garantía se
rompe y habrá que mover el limitador a Redis o a la base.

**Serializa, no descarta.** El limitador hace esperar a quien llega temprano en
vez de rechazarlo. Un aviso de accidente sin geocodificar es un aviso perdido, y
el trabajo es de fondo: nadie está esperando la respuesta. Lo que sí se acota es
cuántas geocodificaciones intenta una corrida
(`TRANSPORTE_INFORMA_MAX_GEOCODES`), para que un día de temporal no deje al
worker cinco minutos colgado.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from app.collectors.geoservices import as_float, normalise_text, request_json
from app.collectors.lugares import sectores_compatibles
from app.core.config import settings

logger = logging.getLogger(__name__)


class RateLimiter:
    """Espaciador de llamadas. Garantiza `min_interval` entre dos adquisiciones.

    El `asyncio.Lock` es lo que hace que la garantía se sostenga con varias
    corrutinas compitiendo: sin él, diez tareas leerían el mismo `_last_call`,
    todas concluirían que ya pueden salir y dispararían a la vez.

    Se mide con `monotonic()` y no con `time()` a propósito: un ajuste de reloj
    del sistema —NTP corrigiendo hacia atrás en pleno arranque del contenedor—
    haría que el reloj de pared retrocediera y el limitador dejara pasar una
    ráfaga justo cuando menos conviene.
    """

    def __init__(self, min_interval: float) -> None:
        self.min_interval = max(0.0, min_interval)
        self._lock = asyncio.Lock()
        self._last_call: float | None = None

    async def acquire(self) -> float:
        """Espera lo necesario y reserva el turno. Devuelve los segundos dormidos."""
        async with self._lock:
            waited = 0.0
            now = time.monotonic()
            if self._last_call is not None:
                elapsed = now - self._last_call
                remaining = self.min_interval - elapsed
                if remaining > 0:
                    waited = remaining
                    await asyncio.sleep(remaining)
                    now = time.monotonic()
            self._last_call = now
            return waited


#: Singleton de proceso. Ver el docstring del módulo: Nominatim cuenta por IP.
_LIMITER = RateLimiter(settings.NOMINATIM_MIN_INTERVAL_SECONDS)


def get_limiter() -> RateLimiter:
    return _LIMITER


#: Precisión del punto devuelto. Viaja a `raw_data._geocoding` porque el punto
#: de una avenida y el de un cruce **no son el mismo dato**, y el mapa no los
#: distingue solo: los dos son un pin.
#:
#: `INTERSECTION` existe declarada y sin usar a propósito: es la precisión que
#: este sistema querría y que Nominatim no sabe dar (ver `build_queries`). El día
#: que haya un proveedor que resuelva cruces, el consumidor ya sabe leer el valor.
#:
#: `SECTOR` es un barrio, población o cerro: lo que devuelve Nominatim cuando se
#: le pide "Miraflores Alto, Viña del Mar", o cuando la "calle" que leyó el
#: extractor era en realidad un sector. `COMUNA` es la ciudad entera, y **nunca
#: se emite**: existe para poder nombrar lo que `geocode` descarta (ver
#: `precision_de`).
PRECISION_STREET = "street"
PRECISION_INTERSECTION = "intersection"
PRECISION_SECTOR = "sector"
PRECISION_COMUNA = "comuna"

#: `addresstype` de Nominatim → precisión. Lo que no está acá es una vía, un
#: edificio o un punto de interés, y cuenta como calle.
_ADDRESSTYPE_COMUNA = frozenset(
    {"city", "town", "municipality", "county", "province", "state", "region", "country"}
)
_ADDRESSTYPE_SECTOR = frozenset(
    {
        "suburb", "neighbourhood", "quarter", "residential", "city_district",
        "borough", "hamlet", "village", "locality", "isolated_dwelling",
        "city_block", "allotments", "croft", "farm",
    }
)

#: Campos de `address` donde OSM nombra la zona dentro de la comuna. Es contra
#: esto que se verifica la guarda de sector, igual que la de comuna se verifica
#: contra `_COMUNA_FIELDS`.
_SECTOR_FIELDS = (
    "suburb", "neighbourhood", "quarter", "residential", "city_district", "hamlet",
)


def precision_de(payload: Mapping[str, Any]) -> str:
    """Qué tan fino es un resultado de Nominatim: calle, sector o comuna.

    Se lee `addresstype` y, si falta, `place_rank` (16 o menos es una ciudad;
    de 17 a 25, un barrio o localidad; 26 o más, una vía o algo más fino). Un
    resultado que no trae ninguno de los dos cuenta como calle: es lo que
    devolvían los resultados antes de que esto existiera y no hay razón para
    degradarlos por un campo que no se pidió.

    Existe por la nota de Pura Noticia del 2026-09-03: el extractor leyó "Viña
    del Mar:" como calle, Nominatim devolvió el nodo de la ciudad, y el mapa
    puso un incendio de Miraflores Alto en la plaza de Viña. El punto venía con
    `precision="street"` y nada permitía sospechar de él.
    """
    tipo = str(payload.get("addresstype") or "").strip().lower()
    if tipo in _ADDRESSTYPE_COMUNA:
        return PRECISION_COMUNA
    if tipo in _ADDRESSTYPE_SECTOR:
        return PRECISION_SECTOR
    if tipo:
        return PRECISION_STREET

    rango = as_float(payload.get("place_rank"))
    if rango is None:
        return PRECISION_STREET
    if rango <= 16:
        return PRECISION_COMUNA
    if rango <= 25:
        return PRECISION_SECTOR
    return PRECISION_STREET


def result_sectores(payload: Mapping[str, Any]) -> list[str]:
    """Los nombres de zona que Nominatim le asigna a un resultado."""
    address = payload.get("address")
    if not isinstance(address, Mapping):
        return []
    return [
        str(address[campo]).strip()
        for campo in _SECTOR_FIELDS
        if address.get(campo) and str(address[campo]).strip()
    ]


def sector_matches(payload: Mapping[str, Any], sector: str | None) -> bool:
    """¿El resultado cae en el sector que nombró la fuente?

    Misma filosofía que `comuna_matches`: sin sector esperado todo vale, y un
    resultado que **no declara** zona también pasa, porque la guarda existe
    para descartar lo que está demostradamente en otra parte, no lo que no se
    sabe. Basta con que UNA de las zonas declaradas sea compatible —OSM puede
    dar el barrio y la población a la vez— y la comparación es la de
    `lugares.sectores_compatibles`, que acepta "Miraflores" por "Miraflores
    Alto" y rechaza "Recreo".

    El caso que la motivó: "calle once" de un tuit sobre Miraflores Alto
    resolvía a una calle homónima a 2,5 km, en otro sector. Con la comuna sola
    no había cómo verlo; las dos están en Viña.
    """
    if not sector:
        return True
    declaradas = result_sectores(payload)
    if not declaradas:
        return True
    return any(sectores_compatibles(sector, zona) for zona in declaradas)


@dataclass(frozen=True, slots=True)
class GeocodeResult:
    """Un punto resuelto, con lo necesario para dudar de él.

    `display_name`, `osm_type` e `importance` no son decoración: son lo que
    permite a un operador mirar una señal del MTT y decidir si el punto
    corresponde a la intersección informada o si Nominatim resolvió a la comuna
    entera. Todo esto termina en `raw_data._geocoding`.
    """

    lat: float
    lon: float
    display_name: str | None = None
    osm_type: str | None = None
    #: Heurística de relevancia de Nominatim, en [0,1]. Un valor bajo suele
    #: indicar que resolvió algo más genérico que lo pedido.
    importance: float | None = None
    query: str | None = None
    #: Qué tan fino es el punto. Ver `PRECISION_STREET`.
    precision: str = PRECISION_STREET
    #: Cuál de las calles del cruce resolvió: `street_1` o `street_2`. Que un
    #: punto venga de la transversal no es un defecto —es la única que existía
    #: en OSM— pero sí cambia dónde cae, y eso hay que poder leerlo después.
    matched: str | None = None
    #: Claves que el extractor sí resolvió y el punto NO representa. Sin esto,
    #: un punto a mitad de una avenida de dos kilómetros se ve idéntico a uno
    #: puesto en el cruce que la fuente informó.
    omitted: tuple[str, ...] = ()
    #: Comuna que Nominatim le asigna al punto. Es contra esto que se verifica
    #: la guarda, y queda escrito para poder auditar después que un pin cayó
    #: donde correspondía.
    comuna: str | None = None
    #: Caja que sesgó la búsqueda, si hubo. `None` = sin sesgo: la comuna de la
    #: señal no está en `COMUNA_VIEWBOX`.
    viewbox: tuple[float, float, float, float] | None = None
    #: Sector que nombró la fuente y contra el que se verificó el punto. Con
    #: `precision="sector"`, el punto ES el sector; con `street`, es una calle
    #: que pasó la guarda de sector (ver `sector_matches`).
    sector: str | None = None
    #: Zonas que Nominatim declara para el punto (`address.suburb` y afines).
    #: Es lo que permite auditar después por qué la guarda lo aceptó.
    zonas: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "sector": self.sector,
            "zonas": list(self.zonas),
            "lat": self.lat,
            "lon": self.lon,
            "display_name": self.display_name,
            "osm_type": self.osm_type,
            "importance": self.importance,
            "query": self.query,
            "precision": self.precision,
            "matched": self.matched,
            "omitted": list(self.omitted),
            "comuna": self.comuna,
            "viewbox": list(self.viewbox) if self.viewbox else None,
            "provider": "nominatim",
        }


#: Región que se añade a toda consulta. El sistema sólo cubre la V, y sin ella
#: "Av. Argentina" resuelve en Buenos Aires con toda naturalidad.
#:
#: **Añadirla NO acota nada, y también está medido.** Es un sesgo de ranking, no
#: un filtro: Nominatim la usa para ordenar y la ignora si el nombre de calle
#: gana por otro lado. Sirve para no salir de Chile y para nada más fino. Lo que
#: acota de verdad es `viewbox` + `bounded=1` — ver `COMUNA_VIEWBOX`.
DEFAULT_REGION = "Región de Valparaíso"


# =============================================================================
#  La guarda de comuna
# =============================================================================
#
# # El problema, medido
#
# El 2026-09-03 el CBV despachó una 4-1 en «PRIMERO DE MAYO / 12 DE OCTUBRE»,
# Valparaíso. Pedirle esa calle a Nominatim daba, de las tres formas posibles:
#
#     Primero de Mayo, Región de Valparaíso              → Quillota, a 40 km
#     Primero de Mayo, Valparaíso, Región de Valparaíso  → Quillota, igual
#     street=Primero de Mayo&city=Valparaíso&state=…     → Quillota, igual
#
# Nombrar la comuna en el texto libre **no acota nada**: es un sesgo de ranking
# que pierde contra el nombre de la calle. Y la búsqueda estructurada tampoco —
# `city=` es una pista, no una condición. Las tres formas devuelven Quillota con
# la misma naturalidad.
#
# Eso no es un punto impreciso, es un punto FALSO, y este proyecto ya tiene
# escrito en cinco archivos por qué eso es peor que no tener ninguno: el mapa
# dibuja igual el pin correcto y el que está a 40 km, y quien lo mira no tiene
# cómo distinguirlos. Un cero se puede marcar; un pin equivocado, no.
#
# # Lo que sí funciona: `addressdetails=1`
#
# Nominatim devuelve la comuna en `address.city`, limpia y separada del ruido:
#
#     12 de Octubre → {city: "Viña del Mar",  suburb: "Forestal"}
#     12 de Octubre → {city: "Valparaíso",    suburb: "Placeres"}
#
# Ésa es la guarda: se piden varios resultados y **se elige el primero cuya
# comuna sea la esperada**. Cuesta una sola petición, igual que antes.
#
# Sobre `display_name` no se puede hacer lo mismo, y conviene decir por qué para
# que nadie lo intente después: la de Viña dice «…, Viña del Mar, Provincia de
# Valparaíso, Región de Valparaíso, …». Buscar "valparaíso" ahí adentro acepta
# el resultado de Viña con toda naturalidad. El campo estructurado no tiene esa
# ambigüedad.
#
# # Y el viewbox, ¿para qué queda?
#
# Para sesgar el ranking hacia la zona correcta, de modo que los cinco
# resultados que se piden traigan el bueno. **Sin `bounded=1`**: acotar de
# verdad con un rectángulo descartaría resultados válidos por un borde mal
# puesto, y estos bordes no se pueden poner bien — la caja de Valparaíso que
# incluye Placilla incluye también el sector Forestal de Viña, porque las dos
# comunas se enredan y un rectángulo no las separa. El rectángulo orienta; la
# comuna decide.
#
# # Por qué las cajas están escritas a mano
#
# Porque la caja administrativa de Valparaíso que devuelve OSM es
# `(-80.12, -33.21, -71.38, -26.27)`: la comuna incluye Rapa Nui y Juan
# Fernández, así que su rectángulo envolvente cruza el Pacífico. Como sesgo no
# serviría de nada. Éstas son las de la mancha urbana.

#: Cajas urbanas por comuna, en `(oeste, sur, este, norte)`. Sesgo de búsqueda.
COMUNA_VIEWBOX: dict[str, tuple[float, float, float, float]] = {
    "valparaiso": (-71.72, -33.13, -71.53, -32.99),
    "vina del mar": (-71.59, -33.11, -71.44, -32.94),
    # Jurisdicción del CBVM junto con Viña: los despachos de @CBVM132 que no
    # resuelven en Viña se reintentan acá. Sólo sesga el ranking (sin
    # `bounded`), así que un borde aproximado no descarta nada.
    "concon": (-71.57, -32.98, -71.40, -32.89),
}

#: Cuántos resultados se piden para poder elegir por comuna. Cinco y no uno:
#: con `limit=1`, «12 de Octubre» devuelve el de Viña y el de Valparaíso —el
#: correcto— queda en segundo lugar, invisible. Y no cincuenta: la respuesta se
#: transporta entera y el bueno, si está, está arriba.
RESULT_LIMIT = 5


def viewbox_for(comuna: str | None) -> tuple[float, float, float, float] | None:
    """Caja de sesgo de la comuna, o None si no está declarada.

    Que falte no rompe nada: la comuna sigue filtrando por `address.city`, sólo
    que sin ayudar al ranking. Agregar una comuna es agregar una línea.
    """
    if not comuna:
        return None
    return COMUNA_VIEWBOX.get(normalise_text(comuna))


#: Campos donde Nominatim deja la comuna, en orden de preferencia. Varía con el
#: tipo de lugar: una calle urbana trae `city`, una rural puede traer sólo
#: `town` o `municipality`.
_COMUNA_FIELDS = ("city", "town", "municipality", "village")


def result_comuna(payload: Mapping[str, Any]) -> str | None:
    """La comuna de un resultado de Nominatim, si la declara."""
    address = payload.get("address")
    if not isinstance(address, Mapping):
        return None
    for field in _COMUNA_FIELDS:
        valor = address.get(field)
        if valor and str(valor).strip():
            return str(valor).strip()
    return None


def comuna_matches(payload: Mapping[str, Any], comuna: str | None) -> bool:
    """¿El resultado cae en la comuna esperada?

    Sin comuna esperada, todo vale: es el comportamiento anterior y el que
    corresponde cuando el sistema no sabe dónde debería estar el hecho.

    Un resultado que **no declara** comuna también pasa. Rechazarlo sería
    convertir un hueco de OSM en una pérdida de señal, y la guarda existe para
    descartar lo que está demostradamente en otra parte, no lo que no se sabe.
    """
    if not comuna:
        return True
    encontrada = result_comuna(payload)
    if encontrada is None:
        return True
    return normalise_text(encontrada) == normalise_text(comuna)


def build_queries(
    streets: dict[str, Any], *, region: str = DEFAULT_REGION
) -> list[str]:
    """Consultas candidatas, de la más específica a la más general.

    **Nominatim no resuelve intersecciones, y eso está medido.** Su búsqueda de
    texto libre no tiene ningún concepto de cruce: le pide el nombre a su índice
    y «Av. Argentina y Pedro Montt» no es el nombre de nada. La respuesta no es
    un error ni un punto aproximado, es un array vacío. Comprobado el 2026-09-03
    contra el servicio público, con `countrycodes=cl`:

        Av. Argentina y Pedro Montt, Valparaíso, Región de Valparaíso   → []
        Avenida España y Avenida Argentina, Valparaíso, Región …        → []
        Uno Norte y Libertad, Viña del Mar, Región de Valparaíso        → []
        Av. Argentina, Valparaíso, Región de Valparaíso                 → OK

    Ese último es el punto que este archivo llevaba meses sin pedir. La consulta
    de intersección era la forma **por defecto** en cuanto el extractor
    encontraba una transversal, así que todo aviso bien leído —«colisión en Av.
    Argentina con Pedro Montt», que es la forma canónica de la prensa local—
    terminaba sin coordenadas. Y sin coordenadas no hay incidente:
    `cluster_unassigned_events` filtra por `geom IS NOT NULL`. El evento se
    guardaba entero, con su tipo y su texto, y no llegaba nunca al mapa.

    Es el mismo cero de siempre por una puerta nueva, y con un agravante: el
    aviso mejor escrito era justamente el que se perdía. Cuanto más completa la
    fuente, más probable la transversal, más seguro el silencio.

    Por eso la transversal sale de la consulta **como cruce** — pero no se tira:
    baja a ser una candidata más, por su cuenta. Eso lo obligó el despacho de
    «PRIMERO DE MAYO / 12 DE OCTUBRE» del 2026-09-03: dentro de la caja de
    Valparaíso, `Primero de Mayo` no existe en OSM y `12 de Octubre` sí. La
    calle geocodificable era la SEGUNDA, y quedarse sólo con la primera perdía
    el despacho igual que antes, ahora por otro motivo.

    Cuál de las dos resolvió queda en `GeocodeResult.matched`, y la que no se
    usó en `omitted`: un punto sobre una de las dos calles de un cruce no es el
    cruce, y el mapa dibuja los dos como el mismo pin.

    Lo que se pierde diciéndolo claro: Av. Argentina son ~1,5 km y el punto cae
    donde OSM ancle el tramo, no en la esquina informada. Es peor que un cruce y
    es muchísimo mejor que nada — es exactamente la precisión que hoy tiene el
    accidente de Av. España, el que sí llegó al mapa. Resolver el cruce de
    verdad pide otro servicio (Overpass sabe intersecar dos `way`); queda anotado
    y no se hace acá.

    La segunda candidata quita la ciudad. Nominatim a veces no reconoce la
    comuna tal como la escribe la fuente («Con Con», «Viña»), y la consulta
    entera se cae por el segmento menos importante. Sólo se pide si la primera
    falló: un evento que resuelve a la primera sigue costando una petición.

    Sin vía principal no hay ninguna candidata. Buscar sólo por ciudad daría el
    centroide comunal, que como ubicación de un accidente es peor que no tener
    ubicación: parece un dato y no lo es.
    """
    primary = (streets.get("street_1") or "").strip()
    if not primary:
        return []

    secondary = (streets.get("street_2") or "").strip()
    city = (streets.get("city") or "").strip()
    region = region.strip()

    def formas(calle: str) -> list[str]:
        return [
            ", ".join([calle, *[p for p in (city, region) if p]]),
            ", ".join([calle, *[p for p in (region,) if p]]),
        ]

    # El orden agota la calle principal antes de mirar la transversal: la
    # central escribe primero la vía donde ocurre el hecho.
    candidatas = formas(primary) + (formas(secondary) if secondary else [])

    # Con sector, la primera consulta lo nombra: "calle once, Miraflores Alto,
    # Viña del Mar" es la única forma de pedirle a Nominatim la calle Once de
    # ESE sector y no la primera homónima de la comuna. Cuesta una petición más
    # sólo cuando la fuente nombró un sector, y si no resuelve, las formas de
    # siempre siguen detrás —ahora con la guarda de sector (`sector_matches`).
    # Si lo que el extractor leyó como calle ES el sector ("Miraflores Alto"),
    # nombrarlo dos veces sólo gasta una petición.
    sector = (streets.get("sector") or "").strip()
    if sector and normalise_text(sector) not in normalise_text(primary):
        con_sector = ", ".join([primary, sector, *[p for p in (city, region) if p]])
        candidatas = [con_sector, *candidatas]

    # Sin ciudad, las dos formas de una misma calle son la misma cadena.
    # Deduplicar acá y no en `geocode` evita gastar un segundo del limitador
    # global en repetir una consulta que ya falló.
    unicas: list[str] = []
    for candidata in candidatas:
        if candidata not in unicas:
            unicas.append(candidata)
    return unicas


def matched_key(query: str, streets: dict[str, Any]) -> str | None:
    """¿Cuál de las dos calles resolvió? Se deduce del prefijo de la consulta."""
    for clave in ("street_1", "street_2"):
        calle = str(streets.get(clave) or "").strip()
        if calle and query.startswith(calle):
            return clave
    return None


def omitted_keys(streets: dict[str, Any], *, matched: str | None = None) -> tuple[str, ...]:
    """Qué resolvió el extractor y el punto devuelto NO representa.

    `reference` siempre: un punto de referencia no es una calle y nunca entra en
    la consulta. La calle que no resolvió, también: el punto está sobre una de
    las dos vías del cruce, no en la esquina.
    """
    candidatas = ["street_1", "street_2", "reference"]
    return tuple(
        clave
        for clave in candidatas
        if clave != matched and str(streets.get(clave) or "").strip()
    )


def build_query(streets: dict[str, Any], *, region: str = DEFAULT_REGION) -> str | None:
    """La candidata más específica, o None si no hay vía principal.

    Se conserva porque es la superficie que ya usan los tests y el worker de
    Bomberos. La lógica vive en `build_queries`.
    """
    candidatas = build_queries(streets, region=region)
    return candidatas[0] if candidatas else None


def build_sector_query(
    streets: dict[str, Any], *, region: str = DEFAULT_REGION
) -> str | None:
    """Consulta del sector solo: "Miraflores Alto, Viña del Mar, Región…".

    **Exige comuna.** Sin ella no hay guarda posible, y un nombre de sector se
    repite de una comuna a otra: "Miraflores" existe en más de una. Un sector
    en la comuna equivocada es el mismo punto falso que un «Primero de Mayo»
    en Quillota, sólo que a menos kilómetros.
    """
    sector = (streets.get("sector") or "").strip()
    city = (streets.get("city") or "").strip()
    if not sector or not city:
        return None
    return ", ".join([sector, city, *[p for p in (region.strip(),) if p]])


async def _buscar(
    client: httpx.AsyncClient,
    query: str,
    *,
    limiter: RateLimiter | None,
    extra: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Una petición a Nominatim, con el limitador global. Lista vacía si nada."""
    waited = await (limiter or _LIMITER).acquire()
    if waited > 0:
        logger.debug(
            "espera por el rate limit de Nominatim",
            extra={"waited_s": round(waited, 3), "query": query},
        )

    payload = await request_json(
        client,
        settings.NOMINATIM_URL,
        {
            "q": query,
            "format": "jsonv2",
            "limit": RESULT_LIMIT,
            # Era 0. Sin esto no hay comuna que comparar y la guarda entera
            # no se puede escribir: `display_name` no sirve, ver el bloque
            # de `COMUNA_VIEWBOX`.
            "addressdetails": 1,
            "countrycodes": settings.NOMINATIM_COUNTRY_CODES,
            **extra,
        },
        origin="nominatim",
        # Un reintento y no dos: cada uno cuesta otro segundo de rate limit, y
        # una dirección que no resuelve hoy tampoco resolverá en 1,5 segundos.
        retries=1,
    )
    if not isinstance(payload, list):
        return []
    return [candidato for candidato in payload if isinstance(candidato, dict)]


async def geocode(
    client: httpx.AsyncClient,
    streets: dict[str, Any],
    *,
    limiter: RateLimiter | None = None,
    comuna: str | None = None,
) -> GeocodeResult | None:
    """Resuelve una intersección a lat/lon. None si no hay match o falta calle.

    Devolver None es un resultado legítimo y frecuente: el MTT nombra tramos de
    ruta ("Ruta 68, km 42") que Nominatim no sabe resolver. El worker registra la
    señal igual, sin coordenadas — no entra al Paso A, pero queda consultable y
    es la métrica que dirá si conviene una capa de rutas propia.

    Prueba las candidatas de `build_queries` en orden y **se queda con la
    primera que responde**. Cada una sólo se pide si la anterior falló, así que
    el caso sano sigue costando una petición y un segundo de limitador; el peor
    caso —dos calles, ninguna reconocida— cuesta cuatro y devuelve None igual.

    Tres guardas, las tres con la misma regla —se descarta lo que está
    demostradamente en otra parte, no lo que no se sabe—:

    * `comuna`: se descarta todo resultado que Nominatim ubique en otra comuna.
      Sin ella, «Primero de Mayo» de un despacho de Valparaíso resuelve en
      Quillota, a 40 km. Ver el bloque de `COMUNA_VIEWBOX`.
    * **La ciudad entera no es una calle** (`precision_de`). Si lo que el
      extractor leyó como vía era el nombre de la comuna, Nominatim devuelve el
      nodo de la ciudad, y ese pin en la plaza parece un dato.
    * `sector` (si la fuente nombró uno): una calle que OSM ubica en otro
      sector se descarta. Ver `sector_matches`.

    Si ninguna calle pasa —o no había calle— y la fuente nombró un sector, se
    geocodifica **el sector solo** (`build_sector_query`) y el punto sale con
    `precision="sector"`. Es menos fino que una calle y es lo que la fuente
    dijo; por eso el mapa puede mostrarlo, y por eso queda rotulado.
    """
    esperada = comuna if comuna is not None else streets.get("city")
    sector = str(streets.get("sector") or "").strip() or None
    caja = viewbox_for(esperada)
    extra: dict[str, Any] = {}
    if caja is not None:
        # Sesgo de ranking, sin `bounded`: acotar de verdad con un rectángulo
        # descartaría resultados válidos por un borde mal puesto, y estos bordes
        # no se pueden poner bien. Ver el bloque de `COMUNA_VIEWBOX`.
        extra = {"viewbox": ",".join(str(v) for v in caja)}

    for query in build_queries(streets):
        for candidato in await _buscar(client, query, limiter=limiter, extra=extra):
            if not comuna_matches(candidato, esperada):
                # Está en otra comuna. Se descarta y se sigue mirando: el bueno
                # suele venir detrás —«12 de Octubre» devuelve primero el de
                # Viña y segundo el de Valparaíso— y con `limit=1` era invisible.
                continue
            precision = precision_de(candidato)
            if precision == PRECISION_COMUNA:
                continue
            if not sector_matches(candidato, sector):
                continue

            lat = as_float(candidato.get("lat"))
            lon = as_float(candidato.get("lon"))
            if lat is None or lon is None:
                continue

            acertada = matched_key(query, streets)
            return GeocodeResult(
                lat=lat,
                lon=lon,
                display_name=candidato.get("display_name"),
                osm_type=candidato.get("osm_type"),
                importance=as_float(candidato.get("importance")),
                query=query,
                precision=precision,
                matched=acertada,
                omitted=omitted_keys(streets, matched=acertada),
                comuna=result_comuna(candidato),
                viewbox=caja,
                sector=sector,
                zonas=tuple(result_sectores(candidato)),
            )

    consulta_sector = build_sector_query(streets)
    if consulta_sector is None:
        return None

    for candidato in await _buscar(client, consulta_sector, limiter=limiter, extra=extra):
        if not comuna_matches(candidato, esperada):
            continue
        # Sólo un barrio o localidad cuenta como "el sector". Una calle que se
        # llama como él ("Av. Miraflores") no es el sector, y la ciudad entera
        # tampoco.
        if precision_de(candidato) != PRECISION_SECTOR:
            continue
        nombres = [str(candidato.get("name") or ""), *result_sectores(candidato)]
        if not any(sectores_compatibles(sector, nombre) for nombre in nombres):
            continue

        lat = as_float(candidato.get("lat"))
        lon = as_float(candidato.get("lon"))
        if lat is None or lon is None:
            continue

        return GeocodeResult(
            lat=lat,
            lon=lon,
            display_name=candidato.get("display_name"),
            osm_type=candidato.get("osm_type"),
            importance=as_float(candidato.get("importance")),
            query=consulta_sector,
            precision=PRECISION_SECTOR,
            matched="sector",
            # Todo lo que el extractor leyó como calle quedó fuera del punto.
            omitted=omitted_keys(streets),
            comuna=result_comuna(candidato),
            viewbox=caja,
            sector=sector,
            zonas=tuple(result_sectores(candidato)),
        )

    return None


def build_client(timeout: float | None = None) -> httpx.AsyncClient:
    """Cliente con el User-Agent que Nominatim exige.

    Las peticiones sin User-Agent identificable se rechazan: es parte del
    contrato de uso, no una recomendación.
    """
    return httpx.AsyncClient(
        timeout=timeout or settings.NOMINATIM_TIMEOUT_SECONDS,
        headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
        follow_redirects=True,
    )


__all__ = [
    "COMUNA_VIEWBOX",
    "PRECISION_COMUNA",
    "PRECISION_INTERSECTION",
    "PRECISION_SECTOR",
    "PRECISION_STREET",
    "RESULT_LIMIT",
    "GeocodeResult",
    "RateLimiter",
    "build_client",
    "build_queries",
    "build_query",
    "build_sector_query",
    "comuna_matches",
    "geocode",
    "get_limiter",
    "matched_key",
    "omitted_keys",
    "precision_de",
    "result_comuna",
    "result_sectores",
    "sector_matches",
    "viewbox_for",
]
