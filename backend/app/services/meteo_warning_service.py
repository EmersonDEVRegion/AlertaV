"""Avisos meteorológicos vigentes de la DMC: la lectura detrás de `GET /feed/avisos-meteo`.

Vigente = **visto en la última corrida que leyó el archivo** (`_dmc.visto_en`),
igual que los cortes de Esval: la DMC retira el aviso cuando deja de regir.
La referencia es esa corrida y no el reloj, así que si el collector cae se
sigue mostrando lo último que se supo, con `fuente.estado` avisando.

Respaldo: un aviso cuyo `hasta` calculado ya pasó no se muestra aunque la
última lectura lo trajera. Sólo ocurre con el collector detenido, y ahí es
mejor callar que anunciar un temporal de anteayer.

El orden es el de la urgencia: primero lo más severo, después lo que ya está
en curso, después lo que empieza antes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.dmc.aaa_parser import DMC_KEY, TIPOS, nombre_zona
from app.models.enums import CollectorStatus
from app.models.event import CollectorRun, RawEvent
from app.repositories.event_repository import EventRepository
from app.schemas.meteo_warning import (
    MeteoWarningItem,
    MeteoWarningResponse,
    MeteoWarningTable,
    MeteoWarningZone,
    MeteoWarningZoneCollection,
    MeteoWarningZoneFeature,
    MeteoWarningZoneProperties,
)
from app.schemas.vehicle_feed import VehicleFeedSource
from app.services.collector_health import estado_de_collector

logger = logging.getLogger(__name__)

#: Literal para no importar httpx y el collector desde la API.
COLLECTOR = "dmc_avisos"

#: El mapa oficial: cada aviso enlaza ahí para citar a la DMC.
URL_OFICIAL = "https://archivos.meteochile.gob.cl/portaldmc/AAA/aaa_mapa.php"

#: Holgura sobre el inicio de la corrida, como en los cortes de agua.
MARGEN_VIGENCIA = timedelta(minutes=1)

#: Un día de temporal son 3–5 avisos para la región; el tope sólo frena excesos.
LIMITE = 50

_LEYERON = (CollectorStatus.SUCCESS.value, CollectorStatus.PARTIAL.value)

#: Las zonas de pronóstico de la región, recortadas por `scripts/dmc_zonas.py`.
ZONAS_PATH = Path(__file__).resolve().parents[2] / "static" / "geo" / "dmc_zonas_valpo.json"


@lru_cache(maxsize=1)
def poligonos_de_zonas() -> dict[str, dict[str, Any]]:
    """Geometría de cada zona por su clave (`Litoral` → Polygon). Vacío si falta el archivo.

    Sin el archivo la capa queda vacía y lo dice el log: la franja de «Ahora»
    sigue funcionando, que es lo que importa.
    """
    try:
        datos = json.loads(ZONAS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("no se pudieron leer las zonas de la DMC", extra={"ruta": str(ZONAS_PATH)})
        return {}
    zonas: dict[str, dict[str, Any]] = {}
    for feature in datos.get("features", []):
        zona = (feature.get("properties") or {}).get("zona")
        geometria = feature.get("geometry")
        if isinstance(zona, str) and isinstance(geometria, dict):
            zonas[zona] = geometria
    return zonas


def zonas_con_avisos(
    items: list[MeteoWarningItem], poligonos: Mapping[str, dict[str, Any]]
) -> list[MeteoWarningZoneFeature]:
    """Una feature por zona tocada por algún aviso, con la severidad mayor.

    Un aviso de alcance `region` toca las cuatro: juntas son la región entera.
    `items` llega ordenado por urgencia, así que los fenómenos quedan en ese orden.
    """
    por_zona: dict[str, list[MeteoWarningItem]] = {}
    for item in items:
        claves = list(poligonos) if item.alcance == "region" else [z.clave for z in item.zonas]
        for clave in claves:
            if clave in poligonos:
                por_zona.setdefault(clave, []).append(item)

    features: list[MeteoWarningZoneFeature] = []
    for clave, avisos in por_zona.items():
        peor = max(avisos, key=lambda a: a.severidad)
        fenomenos: list[str] = []
        for aviso in avisos:
            nombre = aviso.fenomeno or aviso.titulo or "Fenómeno meteorológico"
            if nombre not in fenomenos:
                fenomenos.append(nombre)
        features.append(
            MeteoWarningZoneFeature(
                geometry=poligonos[clave],
                properties=MeteoWarningZoneProperties(
                    zona=clave,
                    nombre=nombre_zona(clave),
                    severidad=peor.severidad,
                    tipo=peor.tipo,
                    avisos=len(avisos),
                    fenomenos=" · ".join(fenomenos),
                    ids=",".join(a.id for a in avisos),
                ),
            )
        )
    # Lo más severo arriba: MapLibre dibuja en el orden de las features.
    features.sort(key=lambda f: f.properties.severidad)
    return features


class MeteoWarningService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = EventRepository(session)

    async def vigentes(self, *, ahora: datetime | None = None) -> MeteoWarningResponse:
        momento = ahora or datetime.now(UTC)
        ultima = await self._ultima_corrida()
        lectura = await self._ultima_corrida(solo_lecturas=True)

        items: list[MeteoWarningItem] = []
        if lectura is not None:
            filas = await self.repo.list_weather_warnings(
                vistos_desde=_con_zona(lectura.started_at) - MARGEN_VIGENCIA, limit=LIMITE
            )
            for fila in filas:
                item = item_de_aviso(fila, ahora=momento)
                if item is not None and (item.hasta is None or item.hasta > momento):
                    items.append(item)
        items.sort(key=_urgencia)

        return MeteoWarningResponse(
            generado_en=momento,
            total=len(items),
            items=items,
            fuente=VehicleFeedSource(
                collector=COLLECTOR,
                estado=estado_de_collector(COLLECTOR, ultima, ahora=momento),
                ultima_corrida=(ultima.finished_at or ultima.started_at) if ultima else None,
                detalle=(ultima.error[:300] if ultima and ultima.error else None),
            ),
        )

    async def zonas(self, *, ahora: datetime | None = None) -> MeteoWarningZoneCollection:
        """Las zonas con avisos vigentes, para la capa del mapa."""
        momento = ahora or datetime.now(UTC)
        vigentes = await self.vigentes(ahora=momento)
        return MeteoWarningZoneCollection(
            generado_en=momento,
            features=zonas_con_avisos(vigentes.items, poligonos_de_zonas()),
        )

    async def _ultima_corrida(self, *, solo_lecturas: bool = False) -> CollectorRun | None:
        stmt = select(CollectorRun).where(CollectorRun.collector == COLLECTOR)
        if solo_lecturas:
            stmt = stmt.where(CollectorRun.status.in_(_LEYERON))
        stmt = stmt.order_by(desc(CollectorRun.started_at)).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()


def _con_zona(momento: datetime) -> datetime:
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def _urgencia(item: MeteoWarningItem) -> tuple[int, int, float]:
    inicio = item.desde.timestamp() if item.desde else 0.0
    return (-item.severidad, 0 if item.en_curso else 1, inicio)


def _fecha(valor: Any) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        return _con_zona(datetime.fromisoformat(valor))
    except ValueError:
        return None


def _texto(valor: Any) -> str | None:
    if not isinstance(valor, str):
        return None
    limpio = valor.strip()
    return limpio or None


def item_de_aviso(fila: RawEvent, *, ahora: datetime) -> MeteoWarningItem | None:
    """Fila `weather_warning` → item del feed. `None` si no se entiende.

    Tolerante: una fila escrita por otra versión del collector no puede tumbar
    el feed entero; se omite y queda en el log.
    """
    raw: Mapping[str, Any] = fila.raw_data if isinstance(fila.raw_data, dict) else {}
    dmc = raw.get(DMC_KEY)
    if not isinstance(dmc, Mapping) or dmc.get("tipo") not in TIPOS:
        logger.warning(
            "aviso de la DMC sin bloque _dmc legible; se omite del feed",
            extra={"public_id": str(fila.public_id)},
        )
        return None
    desde = _fecha(dmc.get("desde"))
    zonas_raw = dmc.get("zonas")
    zonas = [
        MeteoWarningZone(clave=z, nombre=nombre_zona(z))
        for z in (zonas_raw if isinstance(zonas_raw, list) else [])
        if isinstance(z, str) and z
    ]
    tabla_raw = dmc.get("tabla")
    try:
        tabla = (
            MeteoWarningTable.model_validate(tabla_raw)
            if isinstance(tabla_raw, Mapping)
            else None
        )
        return MeteoWarningItem(
            id=str(dmc.get("id") or fila.external_id or fila.public_id),
            tipo=dmc["tipo"],
            severidad=int(dmc.get("severidad") or TIPOS.index(dmc["tipo"]) + 1),
            codigo=_texto(dmc.get("codigo")),
            fenomeno=_texto(dmc.get("fenomeno")),
            titulo=_texto(dmc.get("titulo")) or fila.text,
            emision=_fecha(dmc.get("emision")),
            desde=desde,
            desde_texto=_texto(dmc.get("desde_texto")),
            hasta=_fecha(dmc.get("hasta")),
            hasta_texto=_texto(dmc.get("hasta_texto")),
            en_curso=desde is None or desde <= ahora,
            observacion=_texto(dmc.get("observacion")),
            alcance="zonas" if dmc.get("alcance") == "zonas" and zonas else "region",
            zonas=zonas,
            tabla=tabla,
            url=URL_OFICIAL,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        logger.warning(
            "aviso de la DMC que no calza con el contrato; se omite del feed",
            extra={"public_id": str(fila.public_id), "error": f"{type(exc).__name__}: {exc}"},
        )
        return None


__all__ = [
    "COLLECTOR",
    "LIMITE",
    "MARGEN_VIGENCIA",
    "URL_OFICIAL",
    "ZONAS_PATH",
    "MeteoWarningService",
    "item_de_aviso",
    "poligonos_de_zonas",
    "zonas_con_avisos",
]
