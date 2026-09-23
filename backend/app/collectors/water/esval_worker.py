"""Esval — cortes de agua potable en la Región de Valparaíso.

Esval es la sanitaria de la V Región. Sobre su red es la autoridad, igual que
Chilquinta sobre la eléctrica: el corte lo registra su propio sistema. Pero un
corte de agua **no es un siniestro** —casi todos son programados, y los de
emergencia son una matriz o una válvula— así que este collector emite
`water_cut`, una capa de contexto fuera del motor de correlación, como
`road_closure`. No crea incidentes ni manda push.

Cómo lee
--------
Dos GET por corrida, y el segundo sólo si hay algo que ubicar:

1. **API de la Oficina Virtual** (`ESVAL_CORTES_URL`): qué cortes hay, con
   fechas, calles, motivo y el folio `sisda`. Sin coordenadas.
2. **KML del visor oficial** (`ESVAL_ZONAS_KML_URL`): dónde está cada corte
   —punto y polígonos—, pedido con la caja de la región entera.

Se unen por `sisda`. El detalle de ambas fuentes y sus trampas está en
`esval_parser`.

Qué entra y qué no
------------------
* Sólo `empresaId == ESVAL_EMPRESA_ID`. La API mezcla a Aguas del Valle (IV
  Región) aunque se mande `XCodempresa: 1`.
* Sólo los cortes que **ya empezaron**. "CortesActivos" publica los programados
  días antes; entran en la primera corrida después de su hora de inicio, con
  `timestamp = inicio`. Hasta entonces sólo se cuentan en el log.
* Sólo lo que cae en la región: la del KML si la hay, la caja regional si hay
  punto, y si no hay ninguna de las dos, que Esval diga que es suyo.

Cuándo falla y cuándo sólo avisa
--------------------------------
* La API no responde, o responde algo sin una lista reconocible → `failed`.
  Sin la API no hay corrida.
* La API responde una lista pero el esquema cambió → **no revienta**: escribe el
  primer registro en el log (para verlo en Render) y deja la corrida `partial`
  con el motivo en `collector_runs`, que es donde se ve sin leer logs.
* El KML falla o cambió → `partial`, y los cortes entran igual, sin
  coordenadas. Un corte sin punto sigue siendo un dato; perderlo porque el
  visor está caído sería peor.

Cortesía con un servidor ajeno
------------------------------
Dos peticiones cada diez minutos, con un `User-Agent` que dice quién es AlertaV
y dónde encontrarlo. Es menos de lo que gasta una persona con el visor abierto.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import BaseCollector
from app.collectors.geoservices import request_json, request_text
from app.collectors.power.outage_parser import records_or_raise
from app.collectors.water.esval_parser import (
    COMPANY,
    ESVAL_KEY,
    CorteAgua,
    CorteApi,
    KmlZonasError,
    ZonaCorte,
    build_external_id,
    build_text,
    claves_ausentes,
    comuna_del_corte,
    detalle_del_corte,
    muestra_registro,
    parse_corte,
    parse_zonas_kml,
    sin_duplicados,
    unir,
)
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: La sanitaria es la autoridad sobre su propia red. Ver `SOURCE_BASE_CONFIDENCE`
#: y el matiz de siempre: confirma el corte, no una emergencia.
WATER_CUT_CONFIDENCE = 1.0

#: `Referer` de cada host: el de la Oficina Virtual para la API y el del visor
#: para el KML. Son los que manda un navegador que usa esas páginas.
API_REFERER = "https://ov.esval.cl/"
KML_REFERER = "https://tupuntodeagua.esval.cl/"


def _ahora() -> datetime:
    """Reloj del collector. Función de módulo para que los tests puedan fijarlo:
    qué corte "ya empezó" depende de la hora, y las capturas reales tienen fecha."""
    return datetime.now(UTC)


class EsvalCollector(BaseCollector):
    """Cortes de agua de Esval: API + KML del visor, unidos por `sisda`."""

    name = "esval_cortes_agua"
    source = EventSource.ESVAL
    default_interval_seconds = 600

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.api_url = settings.ESVAL_CORTES_URL.strip()
        self.kml_url = settings.ESVAL_ZONAS_KML_URL.strip()
        self.bbox = settings.region_bbox
        if not self.api_url:
            # Falla al construirse, no en silencio: el runner deja una corrida
            # `failed` en `collector_runs` con este mensaje.
            raise CollectorError(
                "ESVAL_CORTES_URL no está configurada; el collector de Esval no "
                "tiene de dónde leer."
            )

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.ESVAL_POLL_INTERVAL_SECONDS

    # -- Petición --------------------------------------------------------------

    def run_params(self) -> dict[str, Any]:
        # Las cabeceras NO van acá: `collector_runs.params` es consultable por
        # cualquiera con acceso al historial de corridas.
        return {
            "company": COMPANY,
            "api_url": self.api_url,
            "kml_url": self.kml_url or None,
            "kml_params": self.kml_params(),
        }

    def api_headers(self) -> dict[str, str]:
        """Las cabeceras que manda el frontend de la Oficina Virtual.

        `XCodempresa` y `Referer` se mandan aunque, desde un navegador, la API
        responde igual sin ellas: no cuestan nada y es lo que el WAF de Esval ve
        normalmente. Lo que NO hacen es filtrar por empresa; ese filtro está en
        `_seleccionar`.
        """
        return {
            "User-Agent": settings.ESVAL_USER_AGENT,
            "XCodempresa": str(settings.ESVAL_EMPRESA_ID),
            "Referer": API_REFERER,
            "Accept": "application/json, text/plain, */*",
        }

    def kml_headers(self) -> dict[str, str]:
        return {
            "User-Agent": settings.ESVAL_USER_AGENT,
            "Referer": KML_REFERER,
            "Accept": (
                "application/vnd.google-earth.kml+xml, application/xml;q=0.9, "
                "text/xml;q=0.9, */*;q=0.1"
            ),
        }

    def kml_params(self) -> dict[str, str]:
        """La región entera en una sola petición. El visor pide su vista actual;
        pedirle la caja regional devuelve todos los cortes de una vez."""
        caja = self.bbox
        return {
            "region": str(settings.ESVAL_REGION_KML),
            "bbox": f"{caja.west},{caja.south},{caja.east},{caja.north}",
        }

    def http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=settings.ESVAL_TIMEOUT_SECONDS,
            follow_redirects=True,
        )

    # -- Lectura ---------------------------------------------------------------

    async def fetch(self) -> Sequence[CorteAgua]:
        ahora = _ahora()
        async with self.http_client() as client:
            registros = await self._leer_api(client)
            cortes = self._interpretar(registros)
            candidatos = self._seleccionar(cortes, ahora)
            # Sin cortes que ubicar, el KML sería una petición gastada.
            zonas: dict[str, ZonaCorte] = {}
            kml_ok = False
            if candidatos:
                zonas, kml_ok = await self._leer_zonas(client)

        unidos = unir(candidatos, zonas)
        self._revisar_union(unidos, kml_ok=kml_ok)

        en_region = [corte for corte in unidos if self._de_la_region(corte)]
        fuera = len(unidos) - len(en_region)
        if fuera:
            # El filtro trabajando, no una degradación: `info`, no `warn`.
            logger.info(
                "esval: cortes fuera de la región",
                extra={"collector": self.name, "descartados": fuera},
            )
        return en_region

    async def _leer_api(self, client: httpx.AsyncClient) -> list[Any]:
        """La lista de cortes de la API, o `CollectorError`.

        `records_or_raise` distingue un error servido con HTTP 200 de una forma
        de sobre desconocida, y en el segundo caso dice qué claves llegaron. Es
        el mismo camino que usan las eléctricas.
        """
        try:
            payload = await request_json(
                client,
                self.api_url,
                {},
                origin=COMPANY,
                headers=self.api_headers(),
            )
        except CollectorError:
            raise
        except Exception as exc:  # frontera con una fuente ajena
            raise CollectorError(
                f"{COMPANY}: fallo inesperado al leer CortesActivos: "
                f"{type(exc).__name__}: {exc}",
                detail={"url": self.api_url},
            ) from exc
        return records_or_raise(payload, company=COMPANY, url=self.api_url)

    def _interpretar(self, registros: Sequence[Any]) -> list[CorteApi]:
        """Registros crudos → `CorteApi`. Nunca revienta por un esquema distinto.

        Si faltan en **todos** los registros claves que el esquema verificado
        trae siempre, o si ningún registro se deja leer, se escribe el primero en
        el log —para inspeccionarlo en Render— y la corrida queda `partial` con
        el motivo. Lo que sí se pudo leer, entra.
        """
        ausentes = claves_ausentes(registros)
        muestra_logueada = False
        if registros and ausentes:
            self._loguear_muestra(registros[0], motivo=f"faltan {', '.join(ausentes)}")
            muestra_logueada = True
            self.warn(
                f"el esquema de CortesActivos cambió: faltan {', '.join(ausentes)} "
                f"(primer registro en el log)"
            )

        cortes: list[CorteApi] = []
        ilegibles = 0
        for registro in registros:
            try:
                corte = parse_corte(registro)
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                logger.warning(
                    "esval: registro de CortesActivos que no se pudo leer",
                    extra={"collector": self.name, "error": f"{type(exc).__name__}: {exc}"},
                )
                corte = None
            if corte is None:
                ilegibles += 1
                continue
            cortes.append(corte)

        if ilegibles:
            self.warn(f"{ilegibles} registros de CortesActivos ilegibles; se descartaron")
            if ilegibles == len(registros) and not muestra_logueada:
                self._loguear_muestra(registros[0], motivo="ningún registro se pudo leer")

        sin_inicio = sum(1 for corte in cortes if corte.inicio is None)
        if sin_inicio:
            # Entran igual, con la hora de la corrida: perder el corte por no
            # entender su fecha sería peor que fecharlo cuando se vio.
            self.warn(f"{sin_inicio} cortes sin fecha de inicio legible; se fechan al verlos")
        return cortes

    def _loguear_muestra(self, registro: Any, *, motivo: str) -> None:
        # El registro va en el MENSAJE y no sólo en `extra`: los logs de Render
        # muestran el mensaje, y el formateador puede no imprimir los extras.
        logger.warning(
            "esval: esquema inesperado en CortesActivos (%s). Primer registro: %s",
            motivo,
            muestra_registro(registro),
            extra={"collector": self.name},
        )

    def _seleccionar(self, cortes: Sequence[CorteApi], ahora: datetime) -> list[CorteApi]:
        """Los cortes de Esval que ya empezaron, uno por `external_id`."""
        propios: list[CorteApi] = []
        otra_empresa = 0
        futuros = 0
        for corte in cortes:
            if corte.empresa_id is not None and corte.empresa_id != settings.ESVAL_EMPRESA_ID:
                otra_empresa += 1
                continue
            if corte.inicio is not None and corte.inicio > ahora:
                futuros += 1
                continue
            propios.append(corte)

        unicos, repetidos = sin_duplicados(propios)
        if otra_empresa or futuros or repetidos:
            logger.info(
                "esval: cortes que no entran en esta corrida",
                extra={
                    "collector": self.name,
                    "otra_empresa": otra_empresa,
                    "aun_no_empiezan": futuros,
                    "sisda_repetido": repetidos,
                    "conservados": len(unicos),
                },
            )
        return unicos

    async def _leer_zonas(self, client: httpx.AsyncClient) -> tuple[dict[str, ZonaCorte], bool]:
        """Zonas del visor por `sisda`, y si se pudieron leer.

        Cualquier fallo acá es una degradación, no una caída: la corrida sigue
        con los cortes sin coordenadas y queda `partial` con el motivo.
        """
        if not self.kml_url:
            self.warn("ESVAL_ZONAS_KML_URL vacía: los cortes entran sin coordenadas")
            return {}, False
        try:
            texto = await request_text(
                client,
                self.kml_url,
                self.kml_params(),
                origin=f"{COMPANY} (KML de zonas)",
                headers=self.kml_headers(),
            )
            return parse_zonas_kml(texto), True
        except CollectorError as exc:
            motivo = exc.message
        except KmlZonasError as exc:
            motivo = str(exc)
        except Exception as exc:  # frontera con una fuente ajena
            motivo = f"{type(exc).__name__}: {exc}"
        self.warn(f"no se pudo leer el KML de zonas ({motivo}); los cortes entran sin coordenadas")
        return {}, False

    def _revisar_union(self, unidos: Sequence[CorteAgua], *, kml_ok: bool) -> None:
        """Avisa si el KML se leyó bien pero no ubicó ningún corte.

        Uno suelto sin zona es normal —un corte que Esval aún no dibuja—. Todos
        sin zona, con el KML respondiendo, es que la unión por `sisda` dejó de
        funcionar: la etiqueta cambió de nombre o el visor filtra otra cosa.
        """
        if not kml_ok or not unidos:
            return
        esperables = [c for c in unidos if c.corte.georreferenciado is not False]
        sin_zona = [c for c in esperables if c.zona is None]
        if esperables and len(sin_zona) == len(esperables):
            self.warn(
                f"ninguno de los {len(esperables)} cortes aparece en el KML de zonas: "
                f"probable cambio en el visor; entran sin coordenadas"
            )
        elif sin_zona:
            logger.info(
                "esval: cortes sin zona en el KML",
                extra={
                    "collector": self.name,
                    "sin_zona": [c.corte.sisda for c in sin_zona],
                },
            )

    def _de_la_region(self, corte: CorteAgua) -> bool:
        """¿Es un corte de la V Región?

        La región del KML manda si la hay; después, la caja regional si hay
        punto; y sin ninguna de las dos, que el corte sea de Esval o que su
        localidad sea una comuna de la región.
        """
        region = corte.zona.region if corte.zona is not None else None
        if region and region.strip() != str(settings.ESVAL_REGION_KML):
            return False
        punto = corte.punto
        if punto is not None:
            return self.bbox.contains(*punto)
        if corte.corte.empresa_id == settings.ESVAL_EMPRESA_ID:
            return True
        return comuna_del_corte(corte) is not None

    # -- Normalización ---------------------------------------------------------

    def normalize(self, records: Sequence[CorteAgua]) -> list[EventCreate]:
        """Cortes → señales `water_cut`. Función pura.

        Repite los filtros de `fetch()` —región, empresa, que ya empezó— porque
        `normalize()` es pública: los tests y cualquier reproceso la llaman con
        registros armados a mano, y el invariante tiene que sostenerse por el
        camino que sea.
        """
        ahora = _ahora()
        eventos: list[EventCreate] = []
        for corte in records:
            api = corte.corte
            if api.empresa_id is not None and api.empresa_id != settings.ESVAL_EMPRESA_ID:
                continue
            if api.inicio is not None and api.inicio > ahora:
                continue
            if not self._de_la_region(corte):
                continue

            comuna = comuna_del_corte(corte)
            punto = corte.punto
            # El corte existe desde su inicio, no desde que lo vimos. Sin fecha
            # legible, la de la corrida. Nunca en el futuro: `EventCreate` lo
            # rechazaría y se perdería la fila.
            timestamp = min(api.inicio or ahora, ahora)
            try:
                eventos.append(
                    EventCreate(
                        timestamp=timestamp,
                        source=self.source,
                        type=EventType.WATER_CUT,
                        lat=punto[0] if punto else None,
                        lon=punto[1] if punto else None,
                        text=build_text(corte, comuna),
                        external_id=build_external_id(api),
                        confidence=WATER_CUT_CONFIDENCE,
                        raw_data={
                            "_collector": self.name,
                            # La clave que `extract_commune` ya conoce.
                            "comuna": comuna,
                            "company": COMPANY,
                            ESVAL_KEY: detalle_del_corte(corte, comuna=comuna, visto_en=ahora),
                            # El registro original de la API, para reprocesar sin
                            # volver a consultar.
                            "_source_record": dict(api.raw),
                        },
                    )
                )
            except Exception as exc:
                # No debería pasar: si pasa, es un bug nuestro y tiene que verse.
                logger.warning(
                    "esval: corte descartado en validación",
                    extra={
                        "collector": self.name,
                        "sisda": api.sisda,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
        return eventos


__all__ = ["WATER_CUT_CONFIDENCE", "EsvalCollector"]
