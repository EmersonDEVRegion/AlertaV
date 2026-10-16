"""DMC — avisos, alertas y alarmas meteorológicas que tocan la Región de Valparaíso.

La Dirección Meteorológica de Chile es la autoridad del pronóstico oficial. Su
Sistema de Alerta Meteorológica publica tres niveles: **Aviso** (severidad
moderada), **Alerta** (fuerte) y **Alarma** (intensa). Este collector lee el
archivo que alimenta ese mapa y guarda lo que toca la región.

Un aviso **no es un hecho**: dice lo que puede ocurrir («probables tormentas
eléctricas el lunes en la tarde»). Por eso emite `weather_warning`, que está
fuera de `CORRELATABLE_EVENT_TYPES` igual que `weather_observation`: no crea
incidentes, no mueve confianzas y no manda push. Tampoco es una alerta de
SENAPRED: la «Alerta» de la DMC es meteorológica y la de SENAPRED es de
protección civil, y la interfaz las nombra siempre por su institución.

Cómo lee
--------
Un GET por corrida a `DMC_AAA_URL`. El archivo es estático, de ~30 kB, con
`Cache-Control: max-age=60`; cada diez minutos es menos de lo que pide una
persona con el mapa abierto. El `User-Agent` dice quién es AlertaV.

Condiciones de uso
------------------
El `robots.txt` de `archivos.meteochile.gob.cl` dice `Disallow: /`. Esto no es
un rastreador —un archivo, cada diez minutos—, pero el proyecto no quiere
apoyarse en esa distinción: la PWA cita a la DMC con enlace en cada aviso, se
le pidió autorización por correo, y ante cualquier reclamo se apaga con
`DMC_AVISOS_ENABLED=false` (el collector sale del registro y el feed responde
404, que la PWA lee como «no disponible»).

Vigencia
--------
La DMC retira un aviso del archivo cuando deja de regir o lo reemplaza por
una actualización (`A552-1/2026`). Igual que en Esval, vigente = **visto en la
última corrida que leyó el archivo** (`_dmc.visto_en`). La hora de término
que se calcula del texto («la noche del martes») sólo sirve de respaldo para
no mostrar un aviso vencido si el collector queda detenido.

Cuándo falla y cuándo sólo avisa
--------------------------------
* El archivo no responde o no trae la lista `AAA` → `failed`.
* Faltan claves en todos los registros, o un registro no se entiende →
  `partial` con el motivo, y entra lo que sí se pudo leer.
* Los contadores (`Avisos`, `Alertas`, `Alarmas`) no calzan con la lista →
  `partial`: el archivo puede estar a medio escribir.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import BaseCollector
from app.collectors.dmc.aaa_parser import (
    DMC_KEY,
    TIPOS,
    AvisoDmc,
    build_external_id,
    build_text,
    claves_ausentes,
    detalle,
    parse_aviso,
)
from app.collectors.geoservices import request_json
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.core.identidad import user_agent_o
from app.models.enums import EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

ORIGEN = "DMC (datos_AAA.json)"

#: El aviso existe y es oficial: eso, y nada más, dice el 1.0. Como
#: `weather_warning` no correlaciona, este número no mueve ningún incidente.
WEATHER_WARNING_CONFIDENCE = 1.0

#: Cuántos caracteres de un registro raro van al log.
_MUESTRA = 1500


def _agente() -> str:
    return user_agent_o(
        settings.DMC_USER_AGENT, "avisos meteorologicos Region de Valparaiso"
    )


def _ahora() -> datetime:
    """Reloj del collector. De módulo para que los tests lo fijen."""
    return datetime.now(UTC)


class DmcAvisosCollector(BaseCollector):
    """Avisos, alertas y alarmas de la DMC que tocan la V Región."""

    name = "dmc_avisos"
    source = EventSource.DMC
    default_interval_seconds = 600

    #: Proxy de salida opcional (`DMC_PROXY_URL`). De clase para que los tests
    #: que arman el collector con `__new__` salgan directo.
    proxy_url: str = ""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.url = settings.DMC_AAA_URL.strip()
        self.proxy_url = settings.DMC_PROXY_URL.strip()
        if not self.url:
            raise CollectorError("DMC_AAA_URL no está configurada.")

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.DMC_POLL_INTERVAL_SECONDS

    def run_params(self) -> dict[str, Any]:
        return {"url": self.url, "via": "proxy" if self.proxy_url else "directo"}

    def http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=settings.DMC_TIMEOUT_SECONDS,
            follow_redirects=True,
            proxy=self.proxy_url or None,
        )

    # -- Lectura ---------------------------------------------------------------

    async def fetch(self) -> Sequence[AvisoDmc]:
        headers = {"User-Agent": _agente(), "Accept": "application/json, */*;q=0.5"}
        async with self.http_client() as client:
            try:
                payload = await request_json(
                    client, self.url, {}, origin=ORIGEN, headers=headers
                )
            except CollectorError as exc:
                if not self.proxy_url:
                    raise
                # El mensaje termina en `/collectors/health`: sin la URL del proxy.
                raise CollectorError(
                    exc.message.replace(self.proxy_url, "proxy"), detail={"url": self.url}
                ) from None
        registros = self._registros(payload)
        return self._interpretar(registros)

    def _registros(self, payload: Any) -> list[Any]:
        if not isinstance(payload, Mapping) or not isinstance(payload.get("AAA"), list):
            claves = (
                sorted(payload)[:10] if isinstance(payload, Mapping) else type(payload).__name__
            )
            raise CollectorError(
                f"{ORIGEN}: la respuesta no trae la lista `AAA` (llegó {claves})",
                detail={"url": self.url},
            )
        registros: list[Any] = payload["AAA"]
        esperados = 0
        contadores_ok = True
        for clave in ("Avisos", "Alertas", "Alarmas"):
            valor = payload.get(clave)
            if isinstance(valor, int):
                esperados += valor
            else:
                contadores_ok = False
        if contadores_ok and esperados != len(registros):
            self.warn(
                f"los contadores dicen {esperados} y la lista trae {len(registros)}: "
                f"el archivo puede estar a medio publicar"
            )
        return registros

    def _interpretar(self, registros: Sequence[Any]) -> list[AvisoDmc]:
        ausentes = claves_ausentes(registros)
        if registros and ausentes:
            self._loguear_muestra(registros[0], motivo=f"faltan {', '.join(ausentes)}")
            self.warn(f"el formato de datos_AAA cambió: faltan {', '.join(ausentes)}")

        avisos: list[AvisoDmc] = []
        ilegibles = 0
        fuera = 0
        for registro in registros:
            try:
                aviso = parse_aviso(registro)
            except (ValueError, TypeError, AttributeError) as exc:
                ilegibles += 1
                logger.warning(
                    "dmc: registro que no se pudo leer",
                    extra={"collector": self.name, "error": f"{type(exc).__name__}: {exc}"},
                )
                continue
            if aviso is None:
                fuera += 1
                continue
            avisos.append(aviso)
        if fuera:
            # El filtro trabajando, no una degradación: `info`, no `warn`.
            logger.info(
                "dmc: avisos de otras regiones",
                extra={"collector": self.name, "descartados": fuera},
            )
        if ilegibles:
            self.warn(f"{ilegibles} registros de datos_AAA ilegibles; se descartaron")
            if ilegibles == len(registros):
                self._loguear_muestra(registros[0], motivo="ningún registro se pudo leer")
        return avisos

    def _loguear_muestra(self, registro: Any, *, motivo: str) -> None:
        logger.warning(
            "dmc: formato inesperado en datos_AAA (%s). Primer registro: %s",
            motivo,
            str(registro)[:_MUESTRA],
            extra={"collector": self.name},
        )

    # -- Normalización ---------------------------------------------------------

    def normalize(self, records: Sequence[AvisoDmc]) -> list[EventCreate]:
        """Avisos → señales `weather_warning`. Función pura."""
        ahora = _ahora()
        eventos: list[EventCreate] = []
        for aviso in records:
            if aviso.tipo not in TIPOS:
                continue
            # La señal existe desde que la DMC la emitió, nunca en el futuro.
            timestamp = min(aviso.emision or ahora, ahora)
            try:
                eventos.append(
                    EventCreate(
                        timestamp=timestamp,
                        source=self.source,
                        type=EventType.WEATHER_WARNING,
                        text=build_text(aviso),
                        external_id=build_external_id(aviso),
                        confidence=WEATHER_WARNING_CONFIDENCE,
                        raw_data={
                            "_collector": self.name,
                            DMC_KEY: detalle(aviso, visto_en=ahora),
                            "_source_record": dict(aviso.raw),
                        },
                    )
                )
            except Exception as exc:
                logger.warning(
                    "dmc: aviso descartado en validación",
                    extra={
                        "collector": self.name,
                        "id": aviso.id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
        return eventos


__all__ = ["ORIGEN", "WEATHER_WARNING_CONFIDENCE", "DmcAvisosCollector"]
