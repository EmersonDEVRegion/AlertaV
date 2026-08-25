"""Plantilla compartida por los collectors de cortes eléctricos.

Chilquinta y CGE hacen exactamente lo mismo: leen los cortes que publica la
empresa, los normalizan y los emiten como señales `power_outage`. Lo que cambia
entre ellas es la URL, el nombre de la empresa, la fuente del enum y también el
transporte: Chilquinta resultó estar tras un POST con API key, y CGE no publica
una API sino un KMZ estático.

Existe esta clase intermedia y no dos ficheros gemelos porque el día que haya
que arreglar el parseo —y con un esquema sin verificar, ese día llega— arreglarlo
una vez es la diferencia entre una corrección y una corrección más un olvido.

Qué se guarda de un corte
--------------------------
Los tres campos que pidió el producto, en `raw_data._outage`:

    company            "chilquinta" | "cge"
    affected_clients   entero, o None si la empresa no lo publica
    restoration_at     ISO-8601 en UTC, o None

Y además, planos en `raw_data`, `affected_clients` y `restoration_at`
duplicados: el mismo criterio que se usó con magnitud y profundidad en la capa
sísmica. El cliente que dibuja el mapa no debería tener que bajar a una clave
con guion bajo, que es estructura interna nuestra y no contrato.

Cómo cada empresa consulta lo suyo
-----------------------------------
No todas las distribuidoras publican igual. Una expone un JSON abierto por GET;
otra esconde el suyo tras un POST con API key; CGE ni siquiera expone una API,
sino un archivo KMZ estático. Esas diferencias se declaran con cuatro ganchos y
no duplicando el `fetch()`:

    http_method        "GET" (por defecto) | "POST"
    request_headers()  cabeceras propias — API keys van acá
    request_payload()  filtros; query string en GET, cuerpo JSON en POST
    prime_session()    preparar la conexión antes de pedir los datos
    load_records()     el transporte completo, para fuentes que no son JSON

Los cuatro primeros bastan mientras la respuesta sea JSON. `load_records()` es la
salida para cuando no lo es: devuelve la lista de registros crudos y hereda todo
lo que viene después. Ver `cge_worker`, que lo sobrescribe para descomprimir un
KMZ en memoria.

`prime_session()` es el más nuevo y el menos evidente: hay fuentes cuyo backend
no responde a una petición aislada, sino sólo dentro de una sesión que la propia
web abre al cargarse. Ver `ChilquintaCollector.prime_session`, que es el caso que
lo obligó.

Todo lo demás —reintentos, detección de HTML, filtro espacial, normalización—
sigue siendo uno solo. Una fuente con otro formato no deja de necesitar el
resto, y dejar que se abriera su propio camino para poder leer un ZIP habría
significado perderlo: el filtro de bounding box existiría dos veces, y el día
que cambie la caja regional se arreglaría una sola.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from app.collectors.base import BaseCollector
from app.collectors.geoservices import request_json
from app.collectors.power.outage_parser import (
    PowerOutage,
    build_external_id,
    describe_shape,
    extract_records,
    parse_outage,
)
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: La distribuidora es la autoridad sobre su propia red: el corte lo registran
#: sus equipos, no es una observación indirecta. Ver la regla en `confidence.py`.
POWER_OUTAGE_CONFIDENCE = 1.0

#: Clave namespaced con el detalle del corte.
OUTAGE_KEY = "_outage"


class BasePowerOutageCollector(BaseCollector):
    """Lector de un feed de cortes. Las subclases sólo declaran quién son."""

    #: Nombre de la empresa tal como se guarda en los metadatos.
    company: str
    #: Variable de entorno que contiene la URL. Se nombra para poder decirlo en
    #: el mensaje de error: "define CGE_API_URL" es accionable, "falta la URL" no.
    url_setting: str
    #: Verbo HTTP con el que se consulta el feed. GET por defecto, que es lo que
    #: hace una fuente abierta; una subclase lo cambia a POST cuando su visor
    #: manda los filtros en el cuerpo. Es atributo de clase y no de instancia
    #: para que se pueda leer sin construir el collector.
    http_method: str = "GET"

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        self.url = str(getattr(settings, self.url_setting, "") or "").strip()
        if not self.url:
            # Falla en la construcción, no en silencio. `run_collector` lo atrapa
            # y escribe una fila `failed` en `collector_runs`: un collector mal
            # configurado tiene que ser visible.
            raise CollectorError(
                f"{self.url_setting} no está configurada; el collector de "
                f"{self.company} no tiene de dónde leer."
            )
        self.bbox = settings.region_bbox

    def run_params(self) -> dict[str, Any]:
        # El método queda en la traza de la corrida: cuando el feed empiece a
        # fallar, saber si se consultó por GET o por POST ahorra media
        # investigación. La API key NO se registra acá; ver `request_headers`.
        #
        # La URL que se registra es la **efectiva** (`request_url()`), no la
        # configurada: si una subclase le añade algo obligatorio, la traza tiene
        # que mostrar lo que de verdad se pidió. Un `collector_runs` que dice una
        # URL distinta de la que salió por el cable es peor que no decir nada.
        return {
            "company": self.company,
            "url": self.request_url(),
            "method": self.http_method,
        }

    def request_url(self) -> str:
        """La URL a la que se consulta de verdad. Por defecto, la configurada.

        Existe como método y no como lectura directa de `self.url` porque hay
        fuentes cuyo endpoint **exige** algo en el query string que no es un
        filtro opcional sino parte de la dirección: sin ello no responden. Ese
        añadido no puede depender de que `request_payload()` sobreviva el camino
        hasta httpx —donde un diccionario falsy se descarta y la query se
        pierde—, así que se aplica sobre la URL, que es lo único que siempre
        viaja tal cual.

        Ver `ChilquintaCollector.request_url`, que es el caso que obligó a
        separarlo.
        """
        return self.url

    def request_headers(self) -> dict[str, str]:
        """Cabeceras de la petición. Las subclases añaden las suyas.

        El `User-Agent` identificable es política del proyecto para todas las
        fuentes: estamos consultando servidores ajenos y quien los opere tiene
        derecho a saber quién golpea y a quién escribirle.

        Lo que una subclase añada acá —una API key, un `Referer` que el visor
        exija— nunca llega a `collector_runs`: `run_params()` no lo mira. Una
        credencial en la traza es una credencial filtrada a cualquiera que
        consulte el historial de corridas.
        """
        return {"User-Agent": settings.NOMINATIM_USER_AGENT}

    def request_payload(self) -> dict[str, Any]:
        """Filtros que la fuente espera recibir. Vacío = pedir el feed entero.

        Van al cuerpo JSON si `http_method` es POST, y al query string si es GET.
        Un mismo diccionario sirve para las dos formas porque son la misma
        pregunta escrita de dos maneras, y así una subclase declara *qué* filtra
        sin tener que saber *cómo* viaja.
        """
        return {}

    def http_client(self) -> httpx.AsyncClient:
        """Cliente HTTP compartido por las distribuidoras.

        Mismo timeout y misma política de redirects para cualquier transporte:
        que CGE descargue un binario y Chilquinta un JSON no cambia cómo nos
        comportamos frente a un servidor que no nos pertenece.
        """
        return httpx.AsyncClient(
            timeout=settings.POWER_TIMEOUT_SECONDS,
            follow_redirects=True,
        )

    async def prime_session(self, client: httpx.AsyncClient) -> None:
        """Prepara la conexión antes de pedir los datos. Por defecto, nada.

        Existe porque no todo backend responde a una petición aislada. Algunos
        —los que están detrás de un framework con sesión de servidor— sólo
        atienden a un cliente que ya cargó la página del visor: la primera
        respuesta trae un `Set-Cookie` y la ruta de datos exige esa cookie de
        vuelta. Una petición suelta, con las cabeceras correctas y la credencial
        correcta, igual recibe un 401.

        El gancho recibe el **cliente**, no la URL, y eso es todo el punto: el
        estado que se está construyendo vive en el cookie jar de esa instancia
        de `httpx.AsyncClient`, así que quien lo implemente tiene que hacer sus
        peticiones con el mismo objeto o no habrá servido de nada. Por eso se
        llama acá dentro, ya abierto el `async with`, y no en `__init__` ni en
        un cliente propio.

        Quien lo implemente decide también si sus fallos son fatales. Lo normal
        es que no lo sean: si el priming no consigue la sesión, la petición de
        datos dirá que no está autorizada, y ese error es más informativo que el
        del preámbulo. Ver `ChilquintaCollector.prime_session`.
        """
        return None

    async def load_records(self) -> Sequence[Any]:
        """Trae los registros crudos de la fuente. Este es el camino JSON.

        Está separado de `fetch()` porque **el transporte es lo único que cambia
        entre distribuidoras**. CGE no publica JSON sino un KMZ —un ZIP con un
        KML dentro—, así que sobrescribe este método y hereda intacto todo lo
        demás: el filtro espacial, el conteo de descartes y la contabilidad de la
        corrida.

        La alternativa era que CGE sobrescribiera `fetch()` entero, y entonces el
        filtro de bounding box y su contabilidad existirían dos veces. Es justo
        el escenario contra el que se escribió esta clase.

        Todo fallo sale como `CollectorError` y de ninguna otra forma.
        `request_json` ya traduce timeouts, 5xx, DNS, TLS y respuestas que no son
        JSON; el `except Exception` final cubre lo que no anticipamos al cruzar
        la frontera con un sistema ajeno. `BaseCollector.run()` lo registra y el
        orquestador no se entera.
        """
        cuerpo = self.request_payload()
        es_post = self.http_method.upper() == "POST"
        destino = self.request_url()
        # En GET los filtros son query string; en POST, cuerpo JSON. Se decide
        # acá y no en `request_json` porque es una característica de la fuente,
        # no del transporte.
        #
        # Ojo con el `or {}`: si `cuerpo` viene vacío hay que pasar `{}` para que
        # `request_response` aplique su guarda y **no** toque la query que ya
        # traiga la URL configurada. Es la trampa de httpx documentada allí, y es
        # justo lo que permite que `request_url()` cargue un parámetro
        # obligatorio sin que un payload vacío lo borre.
        params = {} if es_post or not cuerpo else dict(cuerpo)
        try:
            async with self.http_client() as client:
                # Antes de pedir nada: darle a la fuente la oportunidad de abrir
                # sesión. Va **dentro** del `async with` y con este `client`
                # porque lo que el priming construye —cookies— vive en el cookie
                # jar de esta instancia y muere con ella. Por defecto no hace
                # nada y esta línea no cuesta una petición.
                await self.prime_session(client)
                payload = await request_json(
                    client,
                    destino,
                    params,
                    origin=self.company,
                    method=self.http_method,
                    headers=self.request_headers(),
                    json_body=cuerpo if es_post and cuerpo else None,
                )
        except CollectorError:
            raise
        except Exception as exc:  # frontera con una fuente ajena
            raise CollectorError(
                f"{self.company}: fallo inesperado al leer el feed: "
                f"{type(exc).__name__}: {exc}",
                detail={"url": destino, "method": self.http_method},
            ) from exc

        registros = extract_records(payload)
        if registros is None:
            # El esquema no es el que se esperaba. Se falla con la forma de lo
            # que llegó incluida: es la diferencia entre depurar el primer
            # despliegue en minutos o a ciegas. Ver `describe_shape`.
            raise CollectorError(
                f"{self.company}: no se encontró una lista de cortes en la "
                f"respuesta ({describe_shape(payload)}). Si el endpoint es "
                f"correcto, hay que agregar su clave a `_LIST_KEYS` en "
                f"outage_parser.py.",
                detail={"url": destino},
            )
        return registros

    async def fetch(self) -> Sequence[PowerOutage]:
        """Registros crudos → cortes dentro de la región.

        Idéntico para todas las distribuidoras, sea cual sea el transporte del
        que vengan los registros: lo que `load_records()` entregue tiene que ser
        una secuencia de mappings que `parse_outage` sepa leer.
        """
        registros = await self.load_records()

        # Filtro espacial en el MISMO bucle de extracción, antes de mapear nada
        # al dominio. Las dos distribuidoras publican su zona de concesión
        # completa —CGE llega hasta Aysén— y arrastrar cortes de Chiloé por todo
        # el pipeline para descartarlos al final sería trabajo y memoria por un
        # dato que nunca se va a usar.
        #
        # Es la misma primitiva que usa el collector del CSN: `BoundingBox.contains`
        # sobre `settings.region_bbox`, o sea REGION_NORTH/SOUTH/EAST/WEST. Una
        # sola definición del territorio para todas las capas.
        cortes: list[PowerOutage] = []
        ilegibles = 0
        fuera_de_region = 0

        for item in registros:
            corte = parse_outage(item)
            if corte is None:
                ilegibles += 1
                continue
            if not self.bbox.contains(corte.lat, corte.lon):
                fuera_de_region += 1
                continue
            cortes.append(corte)

        if ilegibles:
            self.warn(f"{ilegibles} registros sin coordenadas utilizables; se descartaron")

        # Fuera de región NO es una degradación: es el filtro funcionando. Se
        # registra en info para poder calibrar —si el 99 % se descarta, el
        # endpoint no está filtrando en el origen— pero no ensucia el estado de
        # la corrida con un `partial` que no corresponde.
        if fuera_de_region:
            logger.info(
                "cortes fuera de la Región de Valparaíso",
                extra={
                    "collector": self.name,
                    "descartados": fuera_de_region,
                    "conservados": len(cortes),
                    "bbox": self.bbox.as_firms_param(),
                },
            )

        # Registros que llegan pero ninguno se entiende: el feed responde y el
        # parseo está ciego. Distinto de un feed vacío, que es una noche sin
        # cortes, y distinto también de que todo caiga fuera de la región, que
        # es el filtro haciendo su trabajo.
        if registros and ilegibles == len(registros):
            self.warn(
                f"llegaron {len(registros)} registros y ninguno tenía coordenadas "
                f"reconocibles: probable cambio de esquema"
            )

        return cortes

    def normalize(self, records: Sequence[PowerOutage]) -> list[EventCreate]:
        """Cortes → señales del dominio. Función pura."""
        now = datetime.now(UTC)
        events: list[EventCreate] = []
        fuera_de_region = 0
        sin_clientes = 0
        sin_reposicion = 0

        for outage in records:
            # Segunda comprobación, y no es redundante: `fetch()` ya filtró, pero
            # `normalize()` es una función pública que los tests y cualquier
            # reproceso futuro llaman con registros armados a mano. El invariante
            # "ninguna señal fuera de la región llega al dominio" tiene que
            # sostenerse por el camino que sea, no sólo por el habitual.
            if not self.bbox.contains(outage.lat, outage.lon):
                fuera_de_region += 1
                continue

            if outage.affected_clients is None:
                sin_clientes += 1
            if outage.restoration_at is None:
                sin_reposicion += 1

            timestamp = outage.started_at or now
            # Un feed con el reloj adelantado haría fallar la validación de
            # `EventCreate` y perdería el lote entero por una fila.
            if timestamp > now:
                timestamp = now

            detalle = {
                "company": self.company,
                "affected_clients": outage.affected_clients,
                "restoration_at": (
                    outage.restoration_at.isoformat() if outage.restoration_at else None
                ),
                "started_at": (
                    outage.started_at.isoformat() if outage.started_at else None
                ),
                "outage_id": outage.outage_id,
                "sector": outage.sector,
            }

            try:
                events.append(
                    EventCreate(
                        timestamp=timestamp,
                        source=self.source,
                        type=EventType.POWER_OUTAGE,
                        lat=outage.lat,
                        lon=outage.lon,
                        text=build_text(self.company, outage),
                        external_id=build_external_id(self.company, outage),
                        confidence=POWER_OUTAGE_CONFIDENCE,
                        raw_data={
                            "_collector": self.name,
                            # La comuna va bajo la clave que `extract_commune` ya
                            # conoce; es el camino que usan CONAF y SENAPRED.
                            "comuna": outage.commune,
                            # Planos para el cliente del mapa, además de dentro
                            # de `_outage`. Ver el docstring del módulo.
                            "affected_clients": outage.affected_clients,
                            "restoration_at": detalle["restoration_at"],
                            "company": self.company,
                            OUTAGE_KEY: detalle,
                            # El registro original, para reprocesar sin volver a
                            # consultar el día que se entienda mejor el esquema.
                            "_source_record": dict(outage.raw),
                        },
                    )
                )
            except Exception as exc:
                logger.debug(
                    "corte descartado en validación",
                    extra={"error": str(exc), "company": self.company},
                )

        if fuera_de_region:
            logger.info(
                "cortes fuera de la Región de Valparaíso",
                extra={"collector": self.name, "descartados": fuera_de_region},
            )
        # Que la empresa no publique estos campos es normal y no un fallo: al
        # comienzo de un corte nadie sabe todavía cuándo se va a reponer. Se
        # cuenta para poder distinguir "la empresa no lo dice" de "lo estamos
        # leyendo mal", que desde los datos se ven igual.
        if sin_clientes or sin_reposicion:
            logger.info(
                "cortes con campos ausentes en la fuente",
                extra={
                    "collector": self.name,
                    "sin_clientes_afectados": sin_clientes,
                    "sin_hora_reposicion": sin_reposicion,
                    "total": len(events),
                },
            )
        return events


def build_text(company: str, outage: PowerOutage) -> str:
    """Descripción legible. El feed no entrega una armada."""
    partes = [f"Corte de suministro eléctrico ({company.upper()})"]

    if outage.affected_clients is not None:
        plural = "clientes afectados" if outage.affected_clients != 1 else "cliente afectado"
        partes.append(f"{outage.affected_clients:,} {plural}".replace(",", "."))

    ubicacion = " ".join(parte for parte in (outage.sector, outage.commune) if parte)
    if ubicacion:
        partes.append(ubicacion)

    if outage.restoration_at is not None:
        local = outage.restoration_at.astimezone(_chile())
        partes.append(f"reposición estimada {local:%d-%m %H:%M} h")

    return " — ".join(partes)


def _chile():
    from app.collectors.power.outage_parser import CHILE_TZ

    return CHILE_TZ


__all__ = [
    "OUTAGE_KEY",
    "POWER_OUTAGE_CONFIDENCE",
    "BasePowerOutageCollector",
    "build_text",
]
