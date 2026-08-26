"""Chilquinta — cortes de suministro en la Región de Valparaíso.

Chilquinta es la distribuidora principal del Gran Valparaíso: Valparaíso, Viña
del Mar, Quilpué, Villa Alemana, Concón y buena parte del interior. Para este
sistema es la fuente con más cobertura de la capa eléctrica.

Chilquinta no tiene API
------------------------
El visor de `mapainterrupciones.chilquinta.cl` **no consulta ningún servicio**:
lee un archivo estático que la propia empresa regenera.

    https://mapainterrupciones.chilquinta.cl/dt/results_006.js?v=<epoch_ms>

El `006` del nombre es el código de filial —el mismo que el visor lleva en
`/mapas?emp=006`— y el `?v=` es un rompe-cachés. Sin credencial, sin sesión, sin
parámetros. Es el mismo patrón que CGE con su KMZ, y por el mismo motivo
probablemente: son visores viejos que publican un volcado periódico en vez de
montar un servicio.

Cómo se descubrió, y por qué importa contarlo
----------------------------------------------
Durante seis iteraciones este módulo apuntó a `https://…/obtieneImage`, una ruta
que devolvía `401 {"error":"Esta petición no esta autorizada"}` y que **el visor
nunca llama**. Se le echó encima, por turnos: priming de sesión, `Referer`,
`orderId` con centinela `""`→`"0"`→`"null"`, token CSRF del `<meta>`, token
cifrado de la cookie, cabeceras de navegador y reintentos por greylisting. Cada
arreglo cambiaba el síntoma lo justo para parecer progreso, y eso mantuvo viva la
premisa equivocada.

Lo resolvió media hora de navegador leyendo el tráfico real de la página. La
lección, para la próxima fuente que se resista: **cuando dos hipótesis plausibles
seguidas no bastan, el problema ya no está en la siguiente capa que se pueda
añadir sino en alguna suposición que nadie ha comprobado.** Mirar antes de
deducir sale más barato que la tercera hipótesis.

El formato: JSONP con las coordenadas escondidas
-------------------------------------------------
El cuerpo no es JSON sino **JSONP**: viene envuelto en `eqfeed_callback( … )` y
hay que quitar el envoltorio antes de parsear. Dentro:

    {"headers": {}, "exception": …, "original": {
        "ordenes_totales": 29,
        "ordenes": [{
            "orden": "10209025",            identificador de la orden de trabajo
            "etr": "27-08-2026 17:00:00",   reposición estimada, hora de Chile
            "latitud": "", "longitud": "",  casi siempre VACÍAS
            "cant_clientes": "68",          string, no entero
            "tipo": "dx" | "inter",
            "cant_seg": 64, "cant_trafos": 1,
            "comuna": "VALPARAISO",
            "segmentos": [[{"latitud_min": …, "longitud_min": …}, …]]
        }]}}

La trampa está en las coordenadas. `latitud` y `longitud` a nivel de orden vienen
**vacías en la gran mayoría** de los registros —4 de 29 en la primera captura—:
los puntos reales viven en `segmentos`, que es una lista *de listas* de vértices
con las claves `latitud_min`/`longitud_min`. Un parser que sólo mire el nivel de
orden no falla: devuelve cero cortes en silencio, que es peor. Ver
`punto_de_la_orden`.

Qué sigue sin verificarse
-------------------------
* **Cada cuánto se regenera el archivo.** La cadencia de 5 minutos es la de la
  familia; si Chilquinta lo reescribe cada hora, sobran consultas. Se resuelve
  comparando `Last-Modified` entre dos corridas.
* **Qué significan `tipo: "dx"` e `"inter"`.** Se conservan en `raw_data` y no se
  filtra por ellos: inventarles semántica sería peor que ignorarlos. Si `inter`
  resultara ser "interrupción programada", habría que decidir si un corte
  anunciado con días de antelación debe emitirse como incidente activo.
* **Si hay un `results_XXX.js` por filial.** El mismo collector las cubriría
  cambiando el número en `CHILQUINTA_API_URL`.

Cortesía con un servidor ajeno
------------------------------
Una petición cada cinco minutos a un archivo estático, con el `User-Agent` del
proyecto y su dirección de contacto. Es menos de lo que gasta una persona con el
visor abierto.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.collectors.geoservices import (
    ServiceErrorEnvelope,
    as_float,
    detect_service_error,
    request_response,
)
from app.collectors.power.base_worker import BasePowerOutageCollector
from app.collectors.power.outage_parser import records_or_raise
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource

logger = logging.getLogger(__name__)

#: Función que envuelve el JSON en el archivo. El nombre lo elige Chilquinta y
#: viene de un ejemplo de Google Maps —`eqfeed_callback` es el que usa su tutorial
#: de terremotos—, así que es copiado y podría cambiar. Por eso el envoltorio se
#: quita buscando los paréntesis y no comparando contra esta cadena: se nombra
#: para el mensaje de error, no para el parseo. Ver `quitar_jsonp`.
JSONP_CALLBACK = "eqfeed_callback"

#: Clave del sobre donde vive el cuerpo útil. `extract_records` no la atraviesa
#: sola porque su desenvoltorio automático sólo actúa sobre sobres de una única
#: clave, y este trae tres (`headers`, `original`, `exception`).
ENVELOPE_KEY = "original"

#: Claves de un vértice dentro de `segmentos`. El sufijo `_min` no significa
#: "mínimo" de nada que podamos ver; es el nombre que trae el archivo.
SEGMENT_LAT = "latitud_min"
SEGMENT_LON = "longitud_min"

#: Lista de listas de vértices: el polígono —o los polígonos— que abarca la orden.
SEGMENTS_KEY = "segmentos"

#: Marca que se añade a una orden cuyo punto **calculamos nosotros**.
#:
#: Hace falta porque el registro que viaja a `raw_data._source_record` es el que
#: se le pasa a `parse_outage`, o sea el ya normalizado — no el original. Sin
#: esta marca, un `latitud: -33.045` derivado de 64 vértices sería
#: indistinguible de uno que la empresa publicó, y quien reprocese en el futuro
#: no tendría forma de saber cuál de los dos está mirando. Con guion bajo por la
#: convención del proyecto: lo que empieza por `_` en `raw_data` es nuestro, no
#: de la fuente.
DERIVED_POINT_KEY = "_punto_derivado"

#: Ruta muerta contra la que este collector se estrelló durante seis
#: iteraciones. No es la que usa el visor y devuelve 400 o 401 según qué le
#: falte; ver el encabezado del módulo.
#:
#: Se nombra para poder **rechazarla al construir el collector**. El motivo es
#: un incidente real: el código nuevo se desplegó con la variable de entorno
#: vieja todavía puesta, y el síntoma fue un
#: `400 Missing required request parameters: [companyCode, orderId]` — un error
#: que habla de parámetros que este collector ya no manda y que manda a buscar
#: el problema al sitio equivocado. "Actualiza CHILQUINTA_API_URL" es
#: accionable; ese 400, no.
RUTA_MUERTA = "obtieneImage"

#: Reintentos de la descarga, por encima de los 2 que trae `request_response`.
#:
#: **El greylisting existía; sólo estaba en otro sitio.** Durante el intento del
#: priming se teorizó que un WAF dropeaba la primera conexión de un cliente
#: desconocido. Aquel priming desapareció con el pivote al archivo estático, pero
#: el comportamiento no: con el collector ya funcionando y trayendo 29 órdenes,
#: **el 43 % de las corridas muere** con
#: `ConnectError: [SSL: WRONG_VERSION_NUMBER]` — 3 de 7 corridas seguidas
#: observadas en `collector_runs`, alternando con corridas perfectas contra la
#: misma URL.
#:
#: Los valores por defecto no alcanzan y se ve en la aritmética: el backoff es
#: `backoff * 2**intento`, así que con `retries=2, backoff=1.5` los tres intentos
#: ocurren en t=0, t≈1.5 s y t≈4.5 s. Si la ventana del greylisting dura más que
#: eso —y los datos dicen que sí— los tres caen juntos y la corrida se pierde
#: entera.
#:
#: Con estos valores hay 5 intentos repartidos en ~30 s (2+4+8+16), que cabe de
#: sobra en los 300 s de cadencia. Los fallos de handshake son inmediatos, no
#: timeouts, así que el coste real es la espera y no el tiempo de conexión.
PRIMING_RETRIES = 4
PRIMING_BACKOFF_SECONDS = 2.0

#: Parámetro rompe-cachés que añade el visor. Se replica porque el archivo es
#: estático y servido con las cabeceras de caché de un estático: sin él, una CDN
#: o un proxy corporativo pueden devolver el volcado de hace horas. En un mapa de
#: cortes, un dato viejo no se distingue de uno bueno — y es exactamente el modo
#: de fallo silencioso contra el que está escrito el resto de este módulo.
CACHE_BUSTER = "v"


def quitar_jsonp(cuerpo: str) -> str:
    """Devuelve el JSON de dentro de `callback( … )`, o el texto tal cual.

    Se localiza por los paréntesis y no por el nombre de la función a propósito.
    `eqfeed_callback` está copiado de un tutorial de Google Maps y no hay ninguna
    garantía de que Chilquinta no lo renombre; los paréntesis, en cambio, son lo
    que define el formato. Emparejar por nombre convertiría un cambio cosmético
    en una caída.

    Si el cuerpo ya es JSON —sin envoltorio— se devuelve intacto: si algún día
    dejan de envolverlo, esto sigue funcionando en vez de romperse.

    Se usa el **último** paréntesis de cierre y no el primero porque el JSON de
    dentro trae los suyos; buscar el primero cortaría el cuerpo por la mitad.
    """
    texto = cuerpo.strip()
    if texto.startswith(("{", "[")):
        return texto

    abre = texto.find("(")
    cierra = texto.rfind(")")
    if abre == -1 or cierra <= abre:
        return texto
    return texto[abre + 1 : cierra].strip()


def _vertices(segmentos: Any) -> Iterable[Mapping[str, Any]]:
    """Todos los vértices de `segmentos`, venga como venga.

    El archivo publica una lista *de listas* de puntos, pero se acepta también
    una lista plana. No es defensa preventiva: es que el formato no está
    documentado, se conoce por una captura, y la diferencia entre las dos formas
    es exactamente el tipo de cosa que cambia sin avisar. Distinguirlas cuesta
    dos líneas; equivocarse cuesta una capa entera devolviendo cero cortes.
    """
    if not isinstance(segmentos, Sequence) or isinstance(segmentos, str | bytes):
        return
    for elemento in segmentos:
        if isinstance(elemento, Mapping):
            yield elemento
        elif isinstance(elemento, Sequence) and not isinstance(elemento, str | bytes):
            for punto in elemento:
                if isinstance(punto, Mapping):
                    yield punto


def punto_de_la_orden(orden: Mapping[str, Any]) -> tuple[float, float] | None:
    """Un punto por orden: el suyo si lo trae, si no el centro de sus segmentos.

    **Este es el método sin el cual el collector devuelve cero cortes**, así que
    conviene entender por qué existe. La orden trae `latitud`/`longitud`, pero
    vienen vacías en la gran mayoría de los registros —4 de 29 en la captura con
    la que se escribió esto—. Las coordenadas de verdad están un nivel más abajo,
    en los vértices de `segmentos`.

    Orden de preferencia, y cada escalón tiene su motivo:

    1. **el punto de la orden**, si es utilizable. Cuando la empresa lo publica
       está diciendo dónde considera *ella* que está el corte, y eso vale más que
       cualquier cosa que calculemos;
    2. **el centroide de los vértices**. Una orden cubre un polígono —hasta 64
       segmentos— y el mapa necesita un punto. El centroide cae dentro de la zona
       afectada en el caso normal, que es el de segmentos contiguos.
    3. `None`, y el registro se descarta contándolo como ilegible.

    Se emite **una señal por orden y no una por segmento**, y esto no es un
    detalle de presentación: `cant_clientes` está publicado a nivel de orden, así
    que un evento por segmento multiplicaría los clientes afectados por el número
    de segmentos —64 en el peor caso observado— y llenaría el mapa de marcadores
    para un solo corte. La orden es la unidad con la que trabaja la empresa y es
    también la unidad del `external_id`.

    El centroide tiene un límite conocido: si una orden agrupara segmentos
    lejanos entre sí, su punto medio podría caer en un lugar donde no hay corte.
    Se acepta porque los segmentos de una orden son sectores contiguos de una
    misma red, y porque la alternativa —descartar esas órdenes— pierde datos
    reales por un caso hipotético. Si aparece, se verá como cortes en mitad del
    mar o de un cerro.
    """
    propio = punto_propio(orden)
    if propio is not None:
        return propio
    return centroide_de_segmentos(orden.get(SEGMENTS_KEY))


def punto_propio(orden: Mapping[str, Any]) -> tuple[float, float] | None:
    """El punto que publica la propia orden, si es utilizable.

    Devuelve `None` cuando `latitud`/`longitud` vienen vacías —el caso normal, 25
    de 29 en la captura— y también cuando traen algo que no es un número.
    """
    lat = as_float(orden.get("latitud"))
    lon = as_float(orden.get("longitud"))
    if lat is None or lon is None:
        return None
    return lat, lon


def centroide_de_segmentos(segmentos: Any) -> tuple[float, float] | None:
    """Promedio de todos los vértices, o `None` si no hay ninguno utilizable."""
    lats: list[float] = []
    lons: list[float] = []
    for punto in _vertices(segmentos):
        vertice_lat = as_float(punto.get(SEGMENT_LAT))
        vertice_lon = as_float(punto.get(SEGMENT_LON))
        if vertice_lat is not None and vertice_lon is not None:
            lats.append(vertice_lat)
            lons.append(vertice_lon)

    if not lats:
        return None
    return sum(lats) / len(lats), sum(lons) / len(lons)


def con_rompe_caches(url: str) -> str:
    """`url` con un `?v=<epoch_ms>` fresco, como el que añade el visor.

    Se recalcula en cada llamada —de eso se trata— y se respeta un `v` que ya
    venga en la URL configurada, por si alguien fija uno a mano para reproducir
    una descarga concreta mientras depura.

    Se arma con `urlsplit`/`urlencode` y no concatenando: la URL configurada
    puede traer ya un query string, y pegar `"?v=…"` a ciegas produciría un
    `?a=1?v=…` que no es una URL válida.
    """
    partes = urlsplit(url)
    consulta = parse_qsl(partes.query, keep_blank_values=True)
    if any(clave == CACHE_BUSTER for clave, _ in consulta):
        return url
    consulta.append((CACHE_BUSTER, str(int(time.time() * 1000))))
    return urlunsplit(partes._replace(query=urlencode(consulta)))


class ChilquintaCollector(BasePowerOutageCollector):
    """Cortes publicados por Chilquinta en su volcado estático."""

    name = "chilquinta_cortes"
    source = EventSource.CHILQUINTA
    company = "chilquinta"
    url_setting = "CHILQUINTA_API_URL"
    default_interval_seconds = 300
    #: Un GET a un archivo. Sin cabeceras propias, sin filtros, sin cuerpo: todo
    #: lo que este collector necesita está en la URL.
    http_method = "GET"

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        # Falla al construirse, igual que una URL ausente, y por el mismo
        # motivo: deja una fila `failed` en `collector_runs` con la variable que
        # hay que corregir en el mensaje, en vez de un 400 cada cinco minutos
        # que hay que ir a interpretar al log. Ver `RUTA_MUERTA`.
        if RUTA_MUERTA in self.url:
            raise CollectorError(
                f"{self.url_setting} apunta a `{RUTA_MUERTA}`, que no es la "
                f"fuente de Chilquinta: es una ruta que devuelve 400/401 y que "
                f"su visor nunca consulta. Los datos están en el archivo "
                f"estático `dt/results_006.js`. Actualiza la variable de entorno "
                f"del despliegue —no basta con el `.env` del repositorio— al "
                f"valor por defecto de `config.py`."
            )

    def request_url(self) -> str:
        """La URL configurada más el rompe-cachés. Ver `con_rompe_caches`."""
        return con_rompe_caches(super().request_url())

    async def load_records(self) -> Sequence[Any]:
        """Descarga el archivo, le quita el envoltorio JSONP y devuelve órdenes.

        Se sobrescribe —en vez de heredar el camino JSON de la familia— porque el
        cuerpo **no es JSON**: `eqfeed_callback({…})` hace fallar a `json.loads`
        con un error de sintaxis en la posición 0, que es un diagnóstico que manda
        a buscar el problema donde no está. Es el mismo motivo por el que CGE
        sobrescribe este método para su KMZ.

        Lo que sí se conserva es `request_response`, el transporte del proyecto:
        reintentos ante 5xx y errores de red, ninguno ante 4xx, y toda excepción
        de httpx ya convertida en `CollectorError`. Abrir un `client.get()` a
        pelo para poder leer texto habría significado perder las tres cosas.

        Cada registro sale con su punto ya resuelto en `latitud`/`longitud`, para
        que `parse_outage` —que es genérico y no sabe nada de `segmentos`— lo
        encuentre donde lo busca. El resto del registro viaja intacto, así que
        `segmentos` sigue estando en `raw_data` y una orden se puede reprocesar
        el día que se quiera dibujar el polígono en vez del punto.
        """
        destino = self.request_url()
        try:
            async with self.http_client() as client:
                respuesta = await request_response(
                    client,
                    destino,
                    # `{}` y no `None`: activa la guarda de `request_response`
                    # para que httpx no reemplace la query de la URL, donde vive
                    # el rompe-cachés.
                    {},
                    origin=self.company,
                    headers=self.request_headers(),
                    # Más insistencia que la de la familia, y sólo acá. Ver
                    # `PRIMING_RETRIES`: el host dropea conexiones de forma
                    # intermitente y los 4,5 s que cubren los valores por
                    # defecto se quedan cortos.
                    retries=PRIMING_RETRIES,
                    backoff=PRIMING_BACKOFF_SECONDS,
                )
                cuerpo = respuesta.text
        except CollectorError:
            raise
        except Exception as exc:  # frontera con una fuente ajena
            raise CollectorError(
                f"{self.company}: fallo inesperado al descargar el volcado: "
                f"{type(exc).__name__}: {exc}",
                detail={"url": destino},
            ) from exc

        if not cuerpo.strip():
            raise CollectorError(
                f"{self.company}: el volcado llegó vacío.",
                detail={"url": destino},
            )

        try:
            payload = json.loads(quitar_jsonp(cuerpo))
        except json.JSONDecodeError as exc:
            # El primer trozo del cuerpo en el mensaje: si lo que llegó es el
            # HTML de un portal caído o una página de error, se reconoce de un
            # vistazo en vez de discutir con un "Expecting value: line 1".
            raise CollectorError(
                f"{self.company}: el volcado no es JSON ni JSONP válido tras "
                f"quitar el envoltorio ({JSONP_CALLBACK}): {exc}. "
                f"Empieza por: {cuerpo.strip()[:120]!r}",
                detail={"url": destino},
            ) from exc

        # El sobre EXTERIOR se mira primero. Cuando la pasarela responde un
        # error, lo hace en la raíz —no dentro de `original`, que ni siquiera
        # existe— así que desenvolver antes de comprobar dejaría el diagnóstico
        # apuntando al interior de algo que nunca llegó.
        error_exterior = detect_service_error(payload)
        if error_exterior is not None:
            raise self._error_del_servidor(error_exterior, destino)

        interior = payload
        if isinstance(payload, Mapping) and ENVELOPE_KEY in payload:
            interior = payload[ENVELOPE_KEY]

        # `records_or_raise` vuelve a comprobar sobre el interior —por si el
        # error viniera anidado— y, si no lo es, aplica el diagnóstico de forma
        # con su consejo de `_LIST_KEYS`, que ahí sí es el correcto.
        registros = records_or_raise(interior, company=self.company, url=destino)

        return [self.con_punto(orden) for orden in registros]

    def _error_del_servidor(
        self, error: ServiceErrorEnvelope, destino: str
    ) -> CollectorError:
        """Traduce un sobre de error a la excepción del proyecto.

        Vive acá y no en el parser porque el mensaje menciona el archivo estático
        y su rompe-cachés, que son de esta fuente. La distinción entre
        transitorio y permanente sí es genérica y viene ya resuelta en `error`.
        """
        if error.transient:
            pista = (
                "El código sugiere algo pasajero —límite de tasa o el servidor "
                "ocupado—. La próxima corrida, en cinco minutos, probablemente "
                "traiga datos; si tres seguidas fallan igual, deja de serlo."
            )
        else:
            pista = (
                f"No es un cambio de formato: el parser no llegó a ver datos. "
                f"Comprueba que {self.url} siga existiendo — este collector lee "
                f"un archivo estático, y si la empresa lo renombra o mueve, su "
                f"CDN puede responder un error con HTTP 200 en vez de un 404."
            )
        return CollectorError(
            f"{self.company}: el servidor respondió con un error dentro de una "
            f"respuesta HTTP 2xx — {error.describe()}. {pista}",
            detail={
                "url": destino,
                "server_code": error.code,
                "server_message": error.message,
                "transient": error.transient,
                "keys": list(error.keys),
            },
        )

    def con_punto(self, orden: Any) -> Any:
        """La orden con un punto utilizable, y **marcada** si lo pusimos nosotros.

        Sólo se toca el registro cuando hay algo que añadir:

        * si la orden ya trae un punto utilizable, se devuelve **intacta** —
          `parse_outage` sabe leer `latitud`/`longitud` aunque vengan como texto,
          así que reescribirlas sería sustituir el dato de la fuente por una
          conversión nuestra sin ganar nada;
        * si el punto se deriva de `segmentos`, se devuelve una copia con las
          coordenadas y con `DERIVED_POINT_KEY`;
        * si no hay punto, se devuelve intacta y la descarta `parse_outage`.

        La marca no es adorno. El registro que acaba en `raw_data._source_record`
        es **este**, no el que llegó por la red —`parse_outage` guarda lo que se
        le pasa—, así que sin ella un centroide de 64 vértices sería
        indistinguible de una coordenada publicada por la empresa. Los
        `segmentos` viajan intactos en la misma copia, de modo que quien
        reprocese tiene a la vez el dato original y el aviso de qué es derivado.

        Una orden que no es un mapping se deja pasar tal cual para que sea
        `parse_outage` quien la descarte y la cuente como ilegible: hay un solo
        sitio donde se decide qué registro es inservible, y no es este.
        """
        if not isinstance(orden, Mapping):
            return orden

        if punto_propio(orden) is not None:
            return orden

        derivado = centroide_de_segmentos(orden.get(SEGMENTS_KEY))
        if derivado is None:
            return orden

        lat, lon = derivado
        return {**orden, "latitud": lat, "longitud": lon, DERIVED_POINT_KEY: True}

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.POWER_POLL_INTERVAL_SECONDS


__all__ = [
    "CACHE_BUSTER",
    "DERIVED_POINT_KEY",
    "ENVELOPE_KEY",
    "JSONP_CALLBACK",
    "PRIMING_BACKOFF_SECONDS",
    "PRIMING_RETRIES",
    "RUTA_MUERTA",
    "SEGMENTS_KEY",
    "SEGMENT_LAT",
    "SEGMENT_LON",
    "ChilquintaCollector",
    "centroide_de_segmentos",
    "con_rompe_caches",
    "punto_de_la_orden",
    "punto_propio",
    "quitar_jsonp",
]
