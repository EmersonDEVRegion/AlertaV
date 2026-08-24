"""Chilquinta — cortes de suministro en la Región de Valparaíso.

Chilquinta es la distribuidora principal del Gran Valparaíso: Valparaíso, Viña
del Mar, Quilpué, Villa Alemana, Concón y buena parte del interior. Para este
sistema es la fuente con más cobertura de la capa eléctrica.

Cómo se llega a los datos
-------------------------
`mapainterrupciones.chilquinta.cl` es un visor de mapa: la raíz devuelve el HTML
de la aplicación y los datos los pide después por XHR. Esa ruta interna —la que
consulta esta clase— tiene tres particularidades que conviene dejar escritas,
porque ninguna se deduce leyendo el código:

1. **El nombre miente.** La ruta se llama `obtieneImage` y no devuelve ninguna
   imagen: devuelve el JSON de los cortes. No es una errata al transcribirla.
   Es ofuscación por oscuridad, y alguien que "corrija" el nombre romperá el
   collector.
2. **Es un POST**, aunque sea una lectura. El visor manda los filtros en el
   cuerpo (`codEmp`/`empresa`) en vez de en el query string, que es lo que antes
   se intentaba con `?emp=006`.
3. **Exige una API key estática** en la cabecera `x-api-key`. Estática quiere
   decir que viene incrustada en el bundle del visor: es un identificador de
   cliente, no un secreto de usuario, y no autoriza a nada que el visor público
   no muestre ya. Aun así vive en el `.env` (`CHILQUINTA_API_KEY`) porque puede
   rotar y porque una credencial ajena no se versiona.

Qué sigue sin verificarse
-------------------------
**El esquema de la respuesta.** Se conoce la puerta, no lo que hay detrás: ni
un solo campo de este collector está confirmado contra una respuesta real. Eso
ya estaba asumido en el diseño de `outage_parser` —alias en español e inglés,
GeoJSON o lista plana, toda ausencia produce `None`— y sigue siendo la apuesta.
Si el esquema no encaja, el collector falla nombrando las claves que llegaron.

Cortesía con un servidor ajeno
------------------------------
La cadencia sigue siendo la de la familia (5 min) y el `User-Agent` identifica
al proyecto. Tener la llave no es motivo para consultar más seguido: se lee lo
mismo que vería una persona con el visor abierto, al ritmo al que le sirve.
"""

from __future__ import annotations

from typing import Any

from app.collectors.power.base_worker import BasePowerOutageCollector
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource

#: Cabecera donde el endpoint espera la llave. El nombre lo fija Chilquinta.
API_KEY_HEADER = "x-api-key"


def _require_api_key() -> str:
    """La API key configurada, o un `CollectorError` que nombra la variable.

    Un solo sitio para leerla y una sola forma de fallar: "define
    CHILQUINTA_API_KEY" es accionable; un 401 en el log a las tres de la mañana
    no lo es.
    """
    key = str(settings.CHILQUINTA_API_KEY or "").strip()
    if not key:
        raise CollectorError(
            "CHILQUINTA_API_KEY no está configurada; el endpoint de Chilquinta "
            f"exige la cabecera {API_KEY_HEADER} y sin ella responde 401."
        )
    return key


class ChilquintaCollector(BasePowerOutageCollector):
    """Cortes publicados por Chilquinta."""

    name = "chilquinta_cortes"
    source = EventSource.CHILQUINTA
    company = "chilquinta"
    url_setting = "CHILQUINTA_API_URL"
    default_interval_seconds = 300
    #: El visor consulta por POST con los filtros en el cuerpo.
    http_method = "POST"

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        # Falla al construirse, igual que una URL ausente, y por el mismo
        # motivo: sin llave el endpoint responde 401 y el collector no puede
        # hacer su trabajo. Que reviente acá deja una fila `failed` en
        # `collector_runs` con la variable que falta en el mensaje, en vez de un
        # 401 repetido cada cinco minutos que hay que ir a leer al log.
        _require_api_key()

    def request_headers(self) -> dict[str, str]:
        """Las de la familia más la API key.

        La llave se lee de `settings` en cada petición en vez de guardarse en la
        instancia: si rota, basta reiniciar el proceso con el `.env` nuevo y no
        hay una copia vieja escondida en un atributo.

        Se añade acá y no en `run_params()` a propósito: las cabeceras no entran
        en la traza de la corrida, así que la credencial no queda escrita en
        `collector_runs` para cualquiera que consulte el historial.
        """
        headers = super().request_headers()
        headers[API_KEY_HEADER] = _require_api_key()
        return headers

    def request_payload(self) -> dict[str, Any]:
        """Filtro de empresa, tal como lo manda el visor.

        Los dos campos llevan el mismo valor y eso no es una redundancia
        nuestra: es lo que envía el frontend. Se replica al pie de la letra
        porque no sabemos cuál de los dos lee el backend, y adivinar mal
        devolvería el catálogo de otra filial o ninguno.
        """
        codigo = str(settings.CHILQUINTA_COD_EMP or "").strip()
        return {"codEmp": codigo, "empresa": codigo}

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.POWER_POLL_INTERVAL_SECONDS


__all__ = ["API_KEY_HEADER", "ChilquintaCollector"]
