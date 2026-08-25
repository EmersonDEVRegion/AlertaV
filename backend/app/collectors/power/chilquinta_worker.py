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
2. **Es un GET sin query string.** La petición no lleva parámetros en la URL ni
   cuerpo: los filtros viajan como **cabeceras propias**. Es la segunda capa de
   la misma ofuscación — un GET a una URL desnuda no delata qué se está pidiendo,
   y quien mire el tráfico por encima no ve un `?emp=006` que copiar.
3. **Exige una API key estática** en la cabecera `x-api-key`. Estática quiere
   decir que viene incrustada en el bundle del visor: es un identificador de
   cliente, no un secreto de usuario, y no autoriza a nada que el visor público
   no muestre ya. Aun así vive en el `.env` (`CHILQUINTA_API_KEY`) porque puede
   rotar y porque una credencial ajena no se versiona.

Las cabeceras como parámetros
------------------------------
Toda la consulta cabe en tres cabeceras y ninguna es opcional:

    x-api-key         credencial; sin ella, 401
    X-Company-Code    filial a consultar. 006 = Chilquinta
    X-Orden-Buscada   orden de trabajo concreta, o "" para pedirlas todas

`X-Orden-Buscada` vacía **no es un descuido**: es el valor que manda el visor al
cargar el mapa completo, y es el que nos interesa. Omitir la cabecera y mandarla
vacía no son lo mismo para este endpoint, así que se envía siempre — ver
`request_headers`, donde está la nota para quien sienta la tentación de
"limpiarla".

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

#: Cabecera con la filial a consultar. Reemplaza al viejo `?emp=006`.
COMPANY_HEADER = "X-Company-Code"

#: Cabecera que acota la consulta a una orden de trabajo. Vacía = todas.
ORDER_HEADER = "X-Orden-Buscada"

#: Valor de `X-Orden-Buscada` cuando se quiere el mapa completo, que es siempre
#: en este collector. Se nombra en vez de escribir `""` suelto porque una cadena
#: vacía en medio de un diccionario parece un olvido y no una decisión.
ALL_ORDERS = ""


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
    #: GET a una URL desnuda: los filtros van en cabeceras, no en el query
    #: string ni en el cuerpo. Ver `request_headers` y `request_payload`.
    http_method = "GET"

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        # Falla al construirse, igual que una URL ausente, y por el mismo
        # motivo: sin llave el endpoint responde 401 y el collector no puede
        # hacer su trabajo. Que reviente acá deja una fila `failed` en
        # `collector_runs` con la variable que falta en el mensaje, en vez de un
        # 401 repetido cada cinco minutos que hay que ir a leer al log.
        _require_api_key()

    def request_headers(self) -> dict[str, str]:
        """Las de la familia más las tres que definen la consulta.

        En este endpoint las cabeceras **son** los parámetros: no hay query
        string ni cuerpo, así que lo que se arma acá es la petición entera.

        Tres decisiones que no se ven en el resultado:

        * La llave se lee de `settings` en cada petición en vez de guardarse en
          la instancia. Si rota, basta reiniciar el proceso con el `.env` nuevo y
          no queda una copia vieja escondida en un atributo.
        * `X-Orden-Buscada` va vacía y **eso es deliberado**. Es lo que manda el
          visor para pedir el mapa completo; omitir la cabecera no es equivalente
          a mandarla vacía. Si alguien la borra por parecer basura, la consulta
          cambia de significado.
        * Nada de esto llega a `run_params()`, y por tanto nada llega a
          `collector_runs`. Una credencial en la traza es una credencial visible
          para cualquiera que consulte el historial de corridas.
        """
        headers = super().request_headers()
        headers[API_KEY_HEADER] = _require_api_key()
        # Desde `settings` y no literal: es el mismo "006" que pide el visor,
        # pero deja el código de filial donde ya estaba configurado en vez de
        # duplicarlo en el código fuente.
        headers[COMPANY_HEADER] = str(settings.CHILQUINTA_COD_EMP or "").strip()
        headers[ORDER_HEADER] = ALL_ORDERS
        return headers

    def request_payload(self) -> dict[str, Any]:
        """Vacío: este endpoint no recibe nada por URL ni por cuerpo.

        No es que no haya filtros — es que viajan en cabeceras. Devolver `{}`
        hace que `load_records()` no añada query string (y que respete el que
        traiga la URL configurada, por la guarda de `request_response`) y que no
        arme cuerpo JSON.
        """
        return {}

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.POWER_POLL_INTERVAL_SECONDS


__all__ = [
    "ALL_ORDERS",
    "API_KEY_HEADER",
    "COMPANY_HEADER",
    "ORDER_HEADER",
    "ChilquintaCollector",
]
