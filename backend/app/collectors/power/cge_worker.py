"""CGE — cortes de suministro en las zonas de la V Región que atiende.

CGE cubre la periferia del área de Chilquinta: parte del litoral, el valle del
Aconcagua y sectores rurales. No se solapan mucho, así que las dos juntas son
la cobertura eléctrica de la región y no una redundancia.

Estado: **sin URL asignada**
-----------------------------
`CGE_API_URL` está vacía a propósito, tal como se pidió. Mientras lo esté, este
collector **falla al construirse** y deja una corrida `failed` visible en
`collector_runs` con el motivo ("define CGE_API_URL").

Que falle en vez de omitirse en silencio es deliberado y es la convención del
proyecto: un collector registrado y sin configurar es trabajo pendiente, y
trabajo pendiente que no se ve nunca se hace. La alternativa —no registrarlo
hasta tener la URL— lo dejaría fuera del inventario y de la traza, y nadie
recordaría que falta.

Si esa fila `failed` cada cinco minutos molesta antes de tener la URL, la salida
limpia es quitarlo del `COLLECTORS` del registry, no silenciar el error.
"""

from __future__ import annotations

from app.collectors.power.base_worker import BasePowerOutageCollector
from app.core.config import settings
from app.models.enums import EventSource


class CgeCollector(BasePowerOutageCollector):
    """Cortes publicados por CGE."""

    name = "cge_cortes"
    source = EventSource.CGE
    company = "cge"
    url_setting = "CGE_API_URL"
    default_interval_seconds = 300

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.POWER_POLL_INTERVAL_SECONDS


__all__ = ["CgeCollector"]
