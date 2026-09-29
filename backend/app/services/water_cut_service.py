"""Cortes de agua vigentes de Esval: la lectura detrás de `GET /events/water-cuts/geojson`.

Vigente no es «dentro de su horario»: la API de Esval no avisa cuándo termina
un corte, simplemente deja de listarlo, y la hora de término que publica es
«referencial». Así que vigente = **visto en la última corrida que leyó la API**
(`_esval.visto_en`, que el collector reescribe en cada corrida).

La referencia es esa corrida y no el reloj. Si el collector cae, el mapa sigue
mostrando lo último que se supo —con `fuente.estado` avisando— en vez de
vaciarse: un mapa sin cortes diría que no hay cortes, y sería falso.

Sin ninguna lectura exitosa no hay capa: `features` vacío y
`fuente.ultima_lectura = null`, que el mapa usa para no mostrar la fila.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.water.esval_parser import ESVAL_KEY, motivo_legible
from app.collectors.water.esval_worker import EsvalCollector
from app.core.config import settings
from app.models.enums import CollectorStatus
from app.models.event import CollectorRun, RawEvent
from app.repositories.event_repository import EventRepository
from app.schemas.event import GeoJSONFeature
from app.schemas.water_cut import WaterCutCollection, WaterCutSource
from app.services.collector_health import estado_de_collector

logger = logging.getLogger(__name__)

#: Holgura sobre el inicio de la corrida. `visto_en` se escribe después de
#: `started_at`, así que alcanza con poco; el minuto cubre relojes desfasados.
MARGEN_VIGENCIA = timedelta(minutes=1)

#: Un mal día son ~30 cortes. El tope sólo evita una respuesta desbocada.
LIMITE = 200

#: Corridas que leyeron la API. `failed` no la leyó; `partial` sí (es el KML
#: el que falló, o se descartaron registros).
_LEYERON = (CollectorStatus.SUCCESS.value, CollectorStatus.PARTIAL.value)


class WaterCutService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = EventRepository(session)

    async def vigentes(self, *, ahora: datetime | None = None) -> WaterCutCollection:
        momento = ahora or datetime.now(UTC)
        ultima = await self._ultima_corrida()
        lectura = await self._ultima_corrida(solo_lecturas=True)

        features: list[GeoJSONFeature] = []
        if lectura is not None:
            filas = await self.repo.list_water_cuts(
                vistos_desde=_con_zona(lectura.started_at) - MARGEN_VIGENCIA,
                bbox=settings.region_bbox,
                limit=LIMITE,
            )
            features = [f for f in (feature_de_corte(fila) for fila in filas) if f is not None]

        return WaterCutCollection(
            features=features,
            generado_en=momento,
            total=len(features),
            fuente=WaterCutSource(
                collector=EsvalCollector.name,
                estado=estado_de_collector(EsvalCollector.name, ultima, ahora=momento),
                ultima_corrida=(ultima.finished_at or ultima.started_at) if ultima else None,
                ultima_lectura=lectura.started_at if lectura else None,
                detalle=(ultima.error[:300] if ultima and ultima.error else None),
            ),
        )

    async def _ultima_corrida(self, *, solo_lecturas: bool = False) -> CollectorRun | None:
        stmt = select(CollectorRun).where(CollectorRun.collector == EsvalCollector.name)
        if solo_lecturas:
            stmt = stmt.where(CollectorRun.status.in_(_LEYERON))
        stmt = stmt.order_by(desc(CollectorRun.started_at)).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()


def _con_zona(momento: datetime) -> datetime:
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def feature_de_corte(fila: RawEvent) -> GeoJSONFeature | None:
    """Fila `water_cut` → feature con propiedades planas. None si no se entiende.

    Sólo lo que muestra el mapa: ni `_source_record` ni los polígonos (~5 KB
    por corte). Tolerante: una fila escrita por otra versión del collector no
    puede tumbar la capa entera; se omite y queda en el log.
    """
    raw: Mapping[str, Any] = fila.raw_data if isinstance(fila.raw_data, dict) else {}
    esval = raw.get(ESVAL_KEY)
    if not isinstance(esval, Mapping):
        logger.warning(
            "corte de agua sin bloque _esval; se omite de la capa",
            extra={"public_id": str(fila.public_id)},
        )
        return None
    bloque_visor = esval.get("visor")
    visor: Mapping[str, Any] = bloque_visor if isinstance(bloque_visor, Mapping) else {}
    programado = esval.get("programado")

    properties: dict[str, Any] = {
        "public_id": str(fila.public_id),
        "sisda": _texto(esval.get("sisda")),
        "comuna": _texto(esval.get("comuna")) or _texto(raw.get("comuna")) or fila.commune,
        "tipo": _texto(esval.get("tipo")),
        "programado": programado if isinstance(programado, bool) else None,
        "motivo": motivo_legible(_texto(esval.get("motivo"))),
        "calles": _texto(esval.get("calles")) or _texto(visor.get("donde")),
        "sector": _texto(esval.get("sector")),
        "inicio": _texto(esval.get("inicio")),
        "fin": _texto(esval.get("fin")),
        "suministro_alternativo": _si_no(visor.get("suministro_alternativo")),
        "url_mapa": _https(_texto(esval.get("url_mapa"))),
        "visto_en": _texto(esval.get("visto_en")),
    }
    geometry = (
        {"type": "Point", "coordinates": [fila.lon, fila.lat]}
        if fila.lat is not None and fila.lon is not None
        else None
    )
    return GeoJSONFeature(geometry=geometry, properties=properties)


def _texto(valor: Any) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _si_no(valor: Any) -> bool | None:
    """ "Sí" / "No" de la ficha del visor. Cualquier otra cosa: no se sabe."""
    texto = (_texto(valor) or "").lower()
    if texto in {"si", "sí"}:
        return True
    if texto == "no":
        return False
    return None


def _https(url: str | None) -> str | None:
    """`urlMapa` viene en `http://`; el visor responde igual en `https`.

    Sólo para los hosts de Esval: cualquier otra cosa se descarta en vez de
    publicarse como enlace.
    """
    if not url:
        return None
    partes = urlsplit(url)
    host = (partes.hostname or "").lower()
    if partes.scheme not in {"http", "https"} or not (
        host == "esval.cl" or host.endswith(".esval.cl")
    ):
        return None
    return urlunsplit(("https", partes.netloc, partes.path, partes.query, partes.fragment))


__all__ = ["LIMITE", "MARGEN_VIGENCIA", "WaterCutService", "feature_de_corte"]
