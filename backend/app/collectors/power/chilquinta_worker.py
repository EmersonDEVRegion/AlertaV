"""Chilquinta — cortes de suministro en la Región de Valparaíso.

Chilquinta es la distribuidora principal del Gran Valparaíso: Valparaíso, Viña
del Mar, Quilpué, Villa Alemana, Concón y buena parte del interior. Para este
sistema es la fuente con más cobertura de la capa eléctrica.

Advertencia sobre la URL
------------------------
`mapainterrupciones.chilquinta.cl` es un **visor de mapa**, no una API: la raíz
devuelve el HTML de una aplicación que después pide los datos a una ruta
interna. Apuntar `CHILQUINTA_API_URL` a la raíz hará que el collector reciba
HTML y falle — con un mensaje claro, porque `request_json` detecta el HTML y lo
dice, pero falle.

La ruta real hay que obtenerla inspeccionando las peticiones que hace el visor
en el navegador (pestaña Red, filtro XHR). Cuando se tenga, se pega en la
variable de entorno y este módulo no necesita cambiar: el parser es tolerante a
los nombres de campo y a la forma del sobre. Ver `outage_parser`.

Qué NO se pudo verificar
-------------------------
El esquema. Ningún campo de este collector está confirmado contra una respuesta
real, y eso está asumido en el diseño: se prueban alias en español e inglés, se
acepta GeoJSON o lista plana, y todo campo ausente produce `None` en vez de una
excepción. Si aun así el esquema no encaja, el collector falla diciendo qué
claves llegaron.
"""

from __future__ import annotations

from app.collectors.power.base_worker import BasePowerOutageCollector
from app.core.config import settings
from app.models.enums import EventSource


class ChilquintaCollector(BasePowerOutageCollector):
    """Cortes publicados por Chilquinta."""

    name = "chilquinta_cortes"
    source = EventSource.CHILQUINTA
    company = "chilquinta"
    url_setting = "CHILQUINTA_API_URL"
    default_interval_seconds = 300

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.POWER_POLL_INTERVAL_SECONDS


__all__ = ["ChilquintaCollector"]
