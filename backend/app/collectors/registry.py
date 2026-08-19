"""Registro de collectors disponibles.

Añadir una fuente = implementar `BaseCollector` y registrarla acá. Nada más
cambia: el runner, la traza y el endpoint de disparo manual la recogen solos.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import BaseCollector
from app.collectors.conaf.collector import ConafCollector
from app.collectors.firms.collector import FirmsCollector
from app.collectors.senapred.collector import SenapredCollector
from app.collectors.usgs.collector import UsgsCollector

CollectorFactory = Callable[[AsyncSession], BaseCollector]

COLLECTORS: dict[str, type[BaseCollector]] = {
    FirmsCollector.name: FirmsCollector,
    ConafCollector.name: ConafCollector,
    SenapredCollector.name: SenapredCollector,
    UsgsCollector.name: UsgsCollector,
    # Próximos hitos:
    #   BroadcastifyCollector.name: BroadcastifyCollector,  # STT → evento
}


def collector_class(name: str) -> type[BaseCollector]:
    """Clase de un collector sin instanciarla.

    El runner la necesita para conocer la cadencia y para poder registrar una
    corrida fallida cuando la propia construcción del collector revienta (por
    ejemplo, por configuración ausente).
    """
    try:
        return COLLECTORS[name]
    except KeyError as exc:
        raise KeyError(
            f"collector desconocido '{name}'. Disponibles: {sorted(COLLECTORS)}"
        ) from exc


def get_collector(name: str, session: AsyncSession) -> BaseCollector:
    try:
        return COLLECTORS[name](session)
    except KeyError as exc:
        raise KeyError(
            f"collector desconocido '{name}'. Disponibles: {sorted(COLLECTORS)}"
        ) from exc


def available_collectors() -> list[str]:
    return sorted(COLLECTORS)
