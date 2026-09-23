"""Capa de cortes de agua potable.

Hoy una sola fuente: Esval, la sanitaria de la V Región. Emite `water_cut`, que
es contexto y no siniestro: fuera de `CORRELATABLE_EVENT_TYPES`, sin incidentes
y sin push. Ver `esval_worker`.
"""

from app.collectors.water.esval_worker import EsvalCollector

__all__ = ["EsvalCollector"]
