"""GBV SpA — vehículos robados, recuperados y abandonados.

Qué es y qué NO es
------------------
GBV (Grupo Búsqueda de Vehículos) es una ONG que publica denuncias de vehículos
para que la gente ayude a encontrarlos. **No es una fuente de emergencias** y
este collector está construido para que nunca se comporte como una:

* Emite `vehicle_report`, fuera de `CORRELATABLE_EVENT_TYPES`: no crea
  incidentes, no se agrupa con nada y no aparece en el mapa.
* Confianza 0.0 en `SOURCE_BASE_CONFIDENCE` y en `RULES`.
* **Sin coordenadas, sin LLM y sin Nominatim.** GBV publica el lugar como texto
  libre, y un punto fabricado a partir de "los araucanos 290" sería peor que no
  tener punto. La comuna se deduce del texto (`gbv_parser.ubicar`).
* Tipo propio y no `other` porque `services/backfill.py` geocodifica con
  Nominatim las señales sin punto de los tipos correlacionables.

Lo consume sólo `GET /feed/vehiculos`: lo que AlertaV vio por primera vez en las
últimas 48 horas.

Delta: lo conocido no se pide
-----------------------------
Cada corrida lee los tres listados —tres peticiones— y calcula la clave de cada
tarjeta **sin abrirla** (la patente y el id vienen en el enlace). Las claves que
ya están en `raw_events` se descartan ahí mismo: no se pide su detalle, no se
normalizan y no se escriben. Sólo lo nuevo cuesta peticiones, y con un tope por
corrida (`GBV_MAX_DETALLES`). Lo que excede el tope no se ingresa a medias: se
difiere entero a la corrida siguiente, donde sigue siendo nuevo.

Corrida semilla
---------------
La primera vez que se lee una sección, TODO lo que tiene es "nuevo": ~200
denuncias, ~160 recuperados, ~90 abandonados. Mostrarlos en un feed de "últimas
48 horas" sería mentir. Así que cuando una sección no tiene ninguna fila en la
base, lo que se lee se guarda marcado `semilla` —sin pedir detalles, porque no
se van a mostrar— y el feed lo excluye. La semilla es **por sección**: si en la
primera corrida falla una, la siguiente la siembra a ella sola en vez de
inundar el feed con su listado completo.

Qué falla y cómo
----------------
* **Un listado responde pero sin tarjetas** → `CollectorError` de esa sección.
  Las tres secciones tienen siempre cientos de avisos: un cero es un cambio de
  maqueta o una página de error servida con 200, no "no hay vehículos".
* **Cae una o dos secciones** → `warn()`; la corrida queda `partial` con el
  nombre de la sección. **Caen las tres** → `failed`.
* **Un detalle da 404** → el aviso entra sólo con los datos de la tarjeta y se
  avisa. Reintentarlo para siempre no lo va a traer de vuelta.
* **Un detalle falla de otra forma** (timeout, 5xx, sin tabla) → se difiere a
  la próxima corrida. Tras tres fallos seguidos se dejan de pedir detalles en
  esta corrida: un sitio caído no tiene por qué comerse el ciclo entero.
* **Había avisos nuevos y no se pudo leer ningún detalle** → `blind()`: la
  corrida no ve el presente aunque haya leído los listados.

Nada de esto se escapa de `run()`: `BaseCollector` captura cualquier excepción y
la deja en `collector_runs`. El runner no se entera.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx
from pydantic import ValidationError as PydanticValidationError

from app.collectors.base import BaseCollector
from app.collectors.vehicles.gbv_parser import (
    CHILE_TZ,
    SECCIONES,
    FichaListado,
    Seccion,
    Vehiculo,
    build_text,
    consolidar,
    hay_pagina_siguiente,
    inicio_del_dia,
    parse_detalle,
    parse_listado,
    ubicar,
    url_de_listado,
)
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import (
    SOURCE_BASE_CONFIDENCE,
    EventSource,
    EventType,
    VehicleStatus,
)
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: Confianza con la que entra cada aviso. La del catálogo, explícita.
GBV_CONFIDENCE = SOURCE_BASE_CONFIDENCE[EventSource.GBV]

#: Fallos seguidos de detalle tras los que se dejan de pedir en la corrida.
MAX_FALLOS_SEGUIDOS = 3

#: Si más de esta fracción de las tarjetas de un listado no se puede leer, no es
#: una tarjeta rara: es la maqueta cambiando. Por debajo, basta con el log.
UMBRAL_DESCARTE = 0.5


@dataclass(frozen=True, slots=True)
class AvisoGbv:
    """Lo que `fetch()` le entrega a `normalize()`."""

    ficha: FichaListado
    #: Tabla del detalle, o None si no se pidió (semilla) o dio 404.
    detalle: dict[str, str] | None
    semilla: bool
    visto_en: datetime


class _DetalleAusenteError(Exception):
    """El detalle ya no existe (404/410). Se ingresa sin él."""


class GbvCollector(BaseCollector):
    """Avisos de vehículos de GBV SpA, para el feed paralelo."""

    name = "gbv_vehiculos"
    source = EventSource.GBV
    default_interval_seconds = 1800

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.GBV_POLL_INTERVAL_SECONDS

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        base = settings.GBV_BASE_URL.strip().rstrip("/")
        if not base:
            raise CollectorError("GBV_BASE_URL no está configurada.")
        self.base_url = base

    def run_params(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "max_detalles": settings.GBV_MAX_DETALLES,
            "max_paginas": settings.GBV_MAX_PAGINAS,
        }

    # -- Transporte -----------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=settings.GBV_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={
                "User-Agent": settings.GBV_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-CL,es;q=0.9",
            },
        )

    async def _get(self, client: httpx.AsyncClient, url: str) -> str:
        """GET con pausa de cortesía entre peticiones consecutivas."""
        if getattr(self, "_peticiones", 0) and settings.GBV_PAUSA_SEGUNDOS > 0:
            await asyncio.sleep(settings.GBV_PAUSA_SEGUNDOS)
        self._peticiones = getattr(self, "_peticiones", 0) + 1

        respuesta = await client.get(url)
        if respuesta.status_code in (404, 410):
            raise _DetalleAusenteError(f"{url} respondió HTTP {respuesta.status_code}")
        if respuesta.status_code != 200:
            raise CollectorError(f"{url} respondió HTTP {respuesta.status_code}")
        tipo = respuesta.headers.get("content-type", "")
        if tipo and "html" not in tipo.lower():
            raise CollectorError(f"{url} respondió {tipo!r} en vez de HTML")
        return respuesta.text

    # -- Base -----------------------------------------------------------------
    #
    # Dos consultas, aisladas en métodos para que los tests las sustituyan sin
    # levantar una base: la convención del proyecto es probar `fetch()` con
    # `respx` sobre instancias creadas con `__new__`.

    async def _seccion_sin_filas(self, seccion: Seccion) -> bool:
        """¿Esta sección todavía no tiene ninguna fila? → corrida semilla."""
        total = await self.service.repo.count_containing(
            EventSource.GBV, {"gbv": {"seccion": seccion.clave}}
        )
        return total == 0

    async def _claves_conocidas(self, claves: Sequence[str]) -> set[str]:
        conocidas = await self.service.repo.ids_by_external_id(EventSource.GBV, claves)
        return set(conocidas)

    # -- Lectura --------------------------------------------------------------

    async def _fichas_nuevas(
        self, client: httpx.AsyncClient, seccion: Seccion, *, semilla: bool
    ) -> list[FichaListado]:
        """Tarjetas de la sección cuya clave todavía no está en la base.

        Pagina sólo si la página entera era nueva —hubo una caída larga— y
        nunca en la semilla, que no necesita historia: sólo marcar el punto de
        partida.
        """
        paginas = settings.GBV_MAX_PAGINAS if seccion.paginada and not semilla else 1
        nuevas: list[FichaListado] = []
        vistas: set[str] = set()

        for numero in range(1, paginas + 1):
            url = url_de_listado(self.base_url, seccion, numero)
            html = await self._get(client, url)
            lectura = parse_listado(html, seccion=seccion, url_pagina=url)

            if lectura.descartadas:
                logger.warning(
                    "tarjetas de GBV que no se pudieron leer",
                    extra={
                        "collector": self.name,
                        "seccion": seccion.clave,
                        "descartadas": len(lectura.descartadas),
                        "motivos": lectura.descartadas[:5],
                    },
                )
                if len(lectura.descartadas) > UMBRAL_DESCARTE * lectura.tarjetas:
                    self.warn(
                        f"{seccion.clave}: {len(lectura.descartadas)} de "
                        f"{lectura.tarjetas} tarjetas ilegibles (¿cambió la maqueta?)"
                    )

            # GBV publica denuncias duplicadas de la misma patente. Gana la
            # primera, que es la más nueva: el listado va de nueva a vieja.
            unicas: list[FichaListado] = []
            for ficha in lectura.fichas:
                if ficha.external_id not in vistas:
                    vistas.add(ficha.external_id)
                    unicas.append(ficha)

            conocidas = await self._claves_conocidas([f.external_id for f in unicas])
            frescas = [f for f in unicas if f.external_id not in conocidas]
            nuevas.extend(frescas)

            pagina_entera_nueva = bool(unicas) and len(frescas) == len(unicas)
            if not pagina_entera_nueva or not hay_pagina_siguiente(html, numero):
                break

        return nuevas

    async def _leer_detalle(
        self, client: httpx.AsyncClient, ficha: FichaListado
    ) -> dict[str, str] | None:
        """Tabla del detalle; None si ya no existe. Otros fallos se propagan."""
        try:
            html = await self._get(client, ficha.url)
        except _DetalleAusenteError as exc:
            self.warn(f"detalle {ficha.seccion} {ficha.id_gbv} ya no existe ({exc}); entra sin él")
            return None
        return parse_detalle(html)

    async def fetch(self) -> list[AvisoGbv]:
        visto_en = datetime.now(UTC)
        avisos: list[AvisoGbv] = []
        secciones_caidas: list[str] = []
        presupuesto = settings.GBV_MAX_DETALLES
        diferidos = 0
        fallos_seguidos = 0
        detalles_ok = 0
        detalles_pedidos = 0

        async with self._client() as client:
            for seccion in SECCIONES:
                try:
                    semilla = await self._seccion_sin_filas(seccion)
                    nuevas = await self._fichas_nuevas(client, seccion, semilla=semilla)
                except (CollectorError, httpx.HTTPError, _DetalleAusenteError) as exc:
                    secciones_caidas.append(f"{seccion.clave}: {_mensaje(exc)}")
                    continue

                if semilla and nuevas:
                    logger.info(
                        "sección de GBV sembrada",
                        extra={
                            "collector": self.name,
                            "seccion": seccion.clave,
                            "avisos": len(nuevas),
                        },
                    )

                for ficha in nuevas:
                    if semilla:
                        avisos.append(AvisoGbv(ficha, None, semilla=True, visto_en=visto_en))
                        continue
                    if presupuesto <= 0 or fallos_seguidos >= MAX_FALLOS_SEGUIDOS:
                        diferidos += 1
                        continue

                    presupuesto -= 1
                    detalles_pedidos += 1
                    try:
                        detalle = await self._leer_detalle(client, ficha)
                    except (CollectorError, httpx.HTTPError) as exc:
                        fallos_seguidos += 1
                        diferidos += 1
                        self.warn(
                            f"detalle {ficha.seccion} {ficha.id_gbv}: {_mensaje(exc)}; "
                            f"se reintenta en la próxima corrida"
                        )
                        continue

                    fallos_seguidos = 0
                    detalles_ok += 1
                    avisos.append(AvisoGbv(ficha, detalle, semilla=False, visto_en=visto_en))

        if len(secciones_caidas) == len(SECCIONES):
            raise CollectorError(
                "no se pudo leer ninguna sección de GBV: " + "; ".join(secciones_caidas)
            )
        for caida in secciones_caidas:
            self.warn(f"sección {caida}")

        if detalles_pedidos and not detalles_ok and diferidos:
            self.blind(
                f"había {diferidos} avisos nuevos y no se pudo leer ninguna ficha de "
                f"detalle: el feed no está recibiendo lo que GBV publica"
            )
        elif diferidos:
            logger.info(
                "avisos de GBV diferidos a la próxima corrida",
                extra={"collector": self.name, "diferidos": diferidos},
            )

        return avisos

    # -- Normalización --------------------------------------------------------

    def normalize(self, records: Sequence[AvisoGbv]) -> list[EventCreate]:
        eventos: list[EventCreate] = []
        for aviso in records:
            hoy = aviso.visto_en.astimezone(CHILE_TZ).date()
            vehiculo = consolidar(aviso.ficha, aviso.detalle, hoy=hoy)

            if not aviso.semilla:
                # Sólo se avisa de lo nuevo: el listado se relee en cada corrida
                # y una patente mal escrita avisaría para siempre. Una vez
                # ingresado por su id de GBV, es conocido y no vuelve a pasar.
                if vehiculo.patente_publicada and not vehiculo.patente:
                    self.warn(
                        f"{vehiculo.seccion} {vehiculo.id_gbv}: "
                        f"'{vehiculo.patente_publicada}' no es una patente chilena válida; "
                        f"se identifica por id de GBV"
                    )
                if vehiculo.fecha_futura_descartada:
                    self.warn(
                        f"{vehiculo.seccion} {vehiculo.id_gbv}: fecha de delito "
                        f"posterior a hoy; se usa la hora de detección"
                    )

            try:
                eventos.append(
                    build_event(
                        vehiculo,
                        external_id=aviso.ficha.external_id,
                        semilla=aviso.semilla,
                        visto_en=aviso.visto_en,
                        ficha_raw=aviso.ficha.as_raw(),
                        detalle_raw=aviso.detalle,
                    )
                )
            except PydanticValidationError as exc:
                self.warn(
                    f"{vehiculo.seccion} {vehiculo.id_gbv} descartado: "
                    f"{exc.errors()[0].get('msg', str(exc))}"
                )
        return eventos


def build_event(
    vehiculo: Vehiculo,
    *,
    external_id: str,
    semilla: bool,
    visto_en: datetime,
    ficha_raw: dict[str, Any],
    detalle_raw: dict[str, str] | None,
) -> EventCreate:
    """`Vehiculo` → `EventCreate`. Función pura.

    El `timestamp` es el momento del hecho informado: el día del delito para un
    robo; para un recuperado o un abandonado, que no traen fecha, el momento en
    que AlertaV lo vio. La ventana del feed NO se mide con esto sino con
    `ingested_at` —GBV publica con atraso—; el `timestamp` sirve para ordenar y
    para `/events`.

    Sin `lat`/`lon`, a propósito. `EventCreate` lo admite siempre que haya
    `text`, y el filtro regional de la ingesta sólo mira eventos con punto.
    """
    ubicacion, comuna = ubicar(
        (vehiculo.recuperado_en, vehiculo.lugar)
        if vehiculo.estado is VehicleStatus.RECUPERADO
        else (vehiculo.lugar,)
    )

    if vehiculo.estado is VehicleStatus.ROBADO and vehiculo.fecha_delito is not None:
        timestamp = inicio_del_dia(vehiculo.fecha_delito)
        precision = "dia"
    else:
        timestamp = visto_en
        precision = "deteccion"

    return EventCreate(
        timestamp=timestamp,
        source=EventSource.GBV,
        type=EventType.VEHICLE_REPORT,
        text=build_text(vehiculo),
        external_id=external_id,
        confidence=GBV_CONFIDENCE,
        raw_data={
            # Todo lo del dominio bajo una sola clave: el feed filtra con
            # `raw_data @> {"gbv": {...}}`, que usa el índice GIN. Y NUNCA una
            # clave `_extraction`: el vínculo por sector del motor la busca.
            "gbv": {
                "seccion": vehiculo.seccion,
                "estado": vehiculo.estado.value,
                "id_gbv": vehiculo.id_gbv,
                "url": vehiculo.url,
                "patente": vehiculo.patente,
                "tipo_vehiculo": vehiculo.tipo_vehiculo,
                "marca": vehiculo.marca,
                "modelo": vehiculo.modelo,
                "color": vehiculo.color,
                "anio": vehiculo.anio,
                "delito": vehiculo.delito,
                "lugar": vehiculo.lugar,
                "recuperado_en": vehiculo.recuperado_en,
                "autoridad": vehiculo.autoridad,
                "tiempo_abandono": vehiculo.tiempo_abandono,
                "fecha_delito": _iso(vehiculo.fecha_delito),
                "fecha_precision": precision,
                "comuna": comuna,
                "region": ubicacion.value,
                "con_detalle": vehiculo.con_detalle,
                "semilla": semilla,
            },
            # Lo publicado, tal cual, para poder reprocesar sin volver a pedirlo.
            "fuente": {"tarjeta": ficha_raw, "detalle": detalle_raw},
        },
    )


def _iso(valor: date | None) -> str | None:
    return valor.isoformat() if valor else None


def _mensaje(exc: BaseException) -> str:
    if isinstance(exc, CollectorError):
        return exc.message
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
