"""Contrato común de los collectors.

Agregar CONAF, SENAPRED o Broadcastify consiste en implementar `fetch()` y
`normalize()`. Todo lo demás —idempotencia, trazabilidad, manejo de errores,
commit— ya está resuelto en `run()`.
"""

from __future__ import annotations

import abc
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import CollectorError
from app.models.enums import CollectorStatus, EventSource
from app.schemas.event import EventCreate
from app.services.ingest_service import IngestService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CollectorResult:
    collector: str
    source: EventSource
    fetched: int = 0
    inserted: int = 0
    duplicated: int = 0
    rejected: int = 0
    status: CollectorStatus = CollectorStatus.SUCCESS
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class BaseCollector(abc.ABC):
    """Plantilla fetch → normalize → upsert, con traza de ejecución."""

    #: Nombre estable del collector (aparece en `collector_runs.collector`).
    name: str
    #: Fuente que alimenta.
    source: EventSource
    #: Cadencia por defecto en modo `--loop`. Cada fuente tiene su ritmo: CONAF
    #: refresca cada pocos minutos, FIRMS depende de pasadas satelitales.
    default_interval_seconds: int = 900

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.service = IngestService(session)

    @classmethod
    def poll_interval_seconds(cls) -> int:
        """Intervalo entre corridas. Se sobrescribe para leerlo de `settings`."""
        return cls.default_interval_seconds

    # -- Degradaciones -------------------------------------------------------

    @property
    def warnings(self) -> list[str]:
        """Problemas no fatales detectados durante la corrida.

        Se inicializa perezosamente a propósito: por convención del proyecto
        `normalize()` se testea sobre instancias creadas con `__new__`, sin pasar
        por `__init__`. Un atributo de instancia normal rompería esos tests.
        """
        existing = getattr(self, "_warnings", None)
        if existing is None:
            existing = []
            self._warnings = existing
        return existing

    def warn(self, message: str) -> None:
        """Registra una degradación: la corrida sigue, pero no queda en silencio.

        Cambió un nombre de campo, se usó una fuente de respaldo, llegaron filas
        sin fecha… Nada de eso justifica perder la corrida completa, pero tampoco
        puede desaparecer: termina en `collector_runs` con estado `partial`.

        **No confundir con `blind`.** Esto dice «entregué menos de lo ideal»;
        `blind` dice «lo que entregué no describe el presente».
        """
        if message not in self.warnings:
            self.warnings.append(message)

    @property
    def blindness(self) -> list[str]:
        """Motivos por los que esta corrida no describe el presente."""
        existing = getattr(self, "_blindness", None)
        if existing is None:
            existing = []
            self._blindness = existing
        return existing

    def blind(self, message: str) -> None:
        """Declara que la corrida está CIEGA: corrió, pero no ve el presente.

        Es la distinción que `partial` nunca pudo hacer. Un rechazo por filtro
        regional y una fuente congelada hace dos horas terminaban con el mismo
        estado, y como lo primero ocurre en cada corrida de fuentes sanas, lo
        segundo se volvía invisible por costumbre.

        Cuándo usarlo, y es un umbral alto: cuando el collector puede afirmar
        que **un hecho ocurrido ahora no llegaría a aparecer**. No cuando trae
        pocos datos, no cuando una fuente de respaldo reemplazó a la principal
        —eso es `warn`—, sino cuando la ventana al mundo está tapada.

        El caso canónico es un pull sobre caché ajena: el collector de Instagram
        lee el dataset de la última corrida del Actor, así que si el Actor deja
        de correr, sigue devolviendo datos válidos y viejos indefinidamente. No
        falla nunca, y esa es exactamente la razón por la que hace falta decirlo
        en voz alta.

        Gana sobre `warn`: una corrida ciega que además rechazó filas es ciega.
        """
        if message not in self.blindness:
            self.blindness.append(message)

    # -- A implementar por cada fuente ---------------------------------------

    @abc.abstractmethod
    async def fetch(self) -> Sequence[Any]:
        """Trae los registros crudos de la fuente externa."""

    @abc.abstractmethod
    def normalize(self, records: Sequence[Any]) -> list[EventCreate]:
        """Convierte los registros crudos en eventos del dominio.

        Debe ser una función pura: sin I/O y sin acceso a base de datos. Así se
        puede testear el mapeo con fixtures reales sin levantar nada.
        """

    async def after_ingest(self, events: Sequence[EventCreate]) -> None:
        """Persistencia complementaria de la fuente. No-op por defecto.

        Existe para las fuentes que tienen campos propios que no caben en el
        esquema común de `raw_events` y merecen una tabla satélite: hoy sólo los
        sismos del USGS, con su magnitud y su profundidad en `seismic_details`.

        Va después de `ingest_batch` —no dentro— porque una tabla satélite
        referencia `raw_events.id`, que la fila no tiene hasta estar escrita. El
        commit del lote ya ocurrió, así que la implementación puede recuperar los
        ids por `external_id` y colgar de ahí lo suyo.

        Se llama dentro del `try` de `run()` a propósito: si esto falla, la
        corrida queda `failed` y visible en `collector_runs`. Guardar la señal y
        perder su detalle en silencio sería el peor de los dos resultados.
        """
        return None

    def run_params(self) -> dict[str, Any]:
        """Parámetros de la corrida, guardados en `collector_runs.params`."""
        return {}

    # -- Orquestación --------------------------------------------------------

    async def run(self) -> CollectorResult:
        run = await self.service.start_run(
            source=self.source, collector=self.name, params=self.run_params()
        )
        result = CollectorResult(collector=self.name, source=self.source)

        try:
            records = await self.fetch()
            result.fetched = len(records)

            events = self.normalize(records)
            result.rejected = max(0, result.fetched - len(events))

            if events:
                ingest = await self.service.ingest_batch(events)
                result.inserted = ingest.inserted
                result.duplicated = ingest.duplicated
                await self.after_ingest(events)

            # Se descartaron filas: la corrida se completó pero no íntegramente.
            # Queda registrado para poder distinguir después, al analizar la
            # ventana de recolección, un hueco real de un problema de mapeo.
            if result.rejected:
                result.status = CollectorStatus.PARTIAL

            # Degradaciones no fatales (respaldo usado, campos renombrados,
            # filas sin fecha). La corrida sirvió, pero el operador tiene que
            # poder verlo sin leer logs.
            if self.warnings:
                result.status = CollectorStatus.PARTIAL
                result.details["warnings"] = list(self.warnings)
                result.error = "; ".join(self.warnings)[:4000]

            # La ceguera se evalúa AL FINAL y pisa a `partial`, no al revés.
            #
            # Una corrida ciega casi siempre rechazó algo también —el filtro
            # regional sigue corriendo sobre los datos viejos— así que evaluarla
            # antes la dejaría en `partial` y el aviso se perdería justo en el
            # caso que motivó todo esto. El estado que llega a la base es el del
            # problema más grave, y quedarse ciego lo es.
            if self.blindness:
                result.status = CollectorStatus.DEGRADED
                result.details["blindness"] = list(self.blindness)
                # El motivo de la ceguera va PRIMERO en el mensaje: es lo que se
                # lee en la ficha del mapa y en la fila de `collector_runs`, y
                # tiene que decir por qué esta capa no ve, no que el filtro
                # regional descartó filas.
                partes = [*self.blindness, *self.warnings]
                result.error = "; ".join(partes)[:4000]

        except CollectorError as exc:
            result.status = CollectorStatus.FAILED
            result.error = exc.message
            logger.exception("collector falló", extra={"collector": self.name})
        except Exception as exc:
            result.status = CollectorStatus.FAILED
            result.error = f"{type(exc).__name__}: {exc}"
            logger.exception("collector falló", extra={"collector": self.name})

        await self.service.finish_run(
            run,
            status=result.status,
            fetched=result.fetched,
            inserted=result.inserted,
            duplicate=result.duplicated,
            error=result.error,
        )

        logger.info(
            "collector finalizado",
            extra={
                "collector": self.name,
                "source": self.source.value,
                "status": result.status.value,
                "fetched": result.fetched,
                "inserted": result.inserted,
                "duplicated": result.duplicated,
                "rejected": result.rejected,
            },
        )
        return result
