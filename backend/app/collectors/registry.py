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
from app.collectors.seismic.sismologia_worker import SismologiaCollector
from app.collectors.senapred.collector import SenapredCollector
from app.collectors.traffic.bomberos_10_4_worker import Bomberos104Collector
from app.collectors.traffic.transporteinforma_worker import TransporteInformaCollector
from app.collectors.traffic.waze_worker import WazeCollector
from app.collectors.usgs.collector import UsgsCollector

CollectorFactory = Callable[[AsyncSession], BaseCollector]

COLLECTORS: dict[str, type[BaseCollector]] = {
    # -- Incendios y emergencias ---------------------------------------------
    FirmsCollector.name: FirmsCollector,
    ConafCollector.name: ConafCollector,
    SenapredCollector.name: SenapredCollector,
    # -- Sismos ---------------------------------------------------------------
    # Dos redes con umbrales distintos, no una redundante: el USGS ignora en la
    # práctica casi todo lo chileno bajo M4.5 y el CSN publica desde M2.5.
    # Ninguna entra al motor de correlación. Ver app/collectors/seismic/.
    UsgsCollector.name: UsgsCollector,
    SismologiaCollector.name: SismologiaCollector,
    # -- Accidentes viales ----------------------------------------------------
    # Los tres emiten `type=accident` y quedan aislados de la familia `fire` por
    # la partición del motor. Ninguno arranca sin su URL configurada: si falta,
    # el constructor lanza y el runner deja una corrida `failed` visible en
    # `collector_runs` en vez de fallar en silencio.
    WazeCollector.name: WazeCollector,
    Bomberos104Collector.name: Bomberos104Collector,
    TransporteInformaCollector.name: TransporteInformaCollector,
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
