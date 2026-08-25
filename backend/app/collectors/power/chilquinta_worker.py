"""Chilquinta — cortes de suministro en la Región de Valparaíso.

Chilquinta es la distribuidora principal del Gran Valparaíso: Valparaíso, Viña
del Mar, Quilpué, Villa Alemana, Concón y buena parte del interior. Para este
sistema es la fuente con más cobertura de la capa eléctrica.

Cómo se llega a los datos
-------------------------
`mapainterrupciones.chilquinta.cl` es un visor de mapa: la raíz devuelve el HTML
de la aplicación y los datos los pide después por XHR. Esa ruta interna —la que
consulta esta clase— tiene cuatro particularidades que conviene dejar escritas,
porque ninguna se deduce leyendo el código:

1. **El nombre miente.** La ruta se llama `obtieneImage` y no devuelve ninguna
   imagen: devuelve el JSON de los cortes. No es una errata al transcribirla.
   Es ofuscación por oscuridad, y alguien que "corrija" el nombre romperá el
   collector.
2. **Es un GET cuyos filtros van en cabeceras, más un `?orderId=` obligatorio.**
   Casi toda la consulta viaja como **cabeceras propias** —segunda capa de la
   misma ofuscación: un GET a una URL casi desnuda no delata qué se pide, y
   quien mire el tráfico por encima no ve un `?emp=006` que copiar—. La
   excepción es `?orderId=`: sin ese parámetro en la URL el servidor responde
   `400 {"error":"Missing required request parameters: [orderId]"}` aunque la
   cabecera `X-Orden-Buscada` vaya presente. Por eso el parámetro se añade a la
   **URL** (`request_url`) y no al payload: ver allí.
   Y no basta con que el parámetro exista: tiene que traer un valor de longitud
   mayor que cero, y además uno que la aplicación no intente resolver a una
   orden concreta. Ver `ALL_ORDERS`, que lleva tres valores distintos y dos
   servidores distintos rechazando cada uno.
3. **Exige una API key estática** en la cabecera `x-api-key`. Estática quiere
   decir que viene incrustada en el bundle del visor: es un identificador de
   cliente, no un secreto de usuario, y no autoriza a nada que el visor público
   no muestre ya. Aun así vive en el `.env` (`CHILQUINTA_API_KEY`) porque puede
   rotar y porque una credencial ajena no se versiona.
4. **Exige sesión.** Y esta es la que no se parece a las otras: no es un
   parámetro que falte, es que el backend no atiende a un cliente que acaba de
   aparecer. Ver la sección siguiente.

Hay dos servidores, no uno
---------------------------
Las respuestas de error vienen de dos sitios distintos y confundirlos cuesta
despliegues:

* delante hay un **API Gateway**, que valida los parámetros obligatorios antes
  de enrutar. Habla en inglés y en JSON plano: `Missing required request
  parameters: [orderId]`, con HTTP 400.
* detrás hay una **aplicación Laravel**, que ya ejecuta la lógica del visor.
  Habla en español: `Esta petición no esta autorizada`, con HTTP 401.

Pasar el primero no dice nada sobre el segundo. Durante dos iteraciones el
`?orderId=0` se dio por bueno porque el 400 desapareció; lo que había detrás era
un 401 que no se había visto todavía.

La sesión: por qué un GET suelto no basta
------------------------------------------
Laravel no autoriza peticiones anónimas a esta ruta. El visor real no manda una
petición: manda la **segunda** petición de una sesión que empezó cuando el
navegador cargó la página. En esa primera carga el servidor emite
`mapa_interrupciones_session` (y el `XSRF-TOKEN` que la acompaña), y el XHR
posterior la devuelve junto con un `Referer` que dice de qué página salió.

`prime_session()` reproduce exactamente eso: un GET silencioso a la página del
visor con el **mismo** `httpx.AsyncClient`, cuyo cookie jar absorbe el
`Set-Cookie` y lo reenvía solo en la petición de datos. No hay que leer la
cookie ni copiarla a mano; hay que hacer las dos peticiones con el mismo cliente
y en ese orden. Ver el gancho `BasePowerOutageCollector.prime_session`, que
existe por este caso.

**Y la página es `/mapas?emp=006`, no la raíz.** Lo intuitivo —y lo que se
intentó primero— es cargar `https://mapainterrupciones.chilquinta.cl/`, pero esa
ruta está mal configurada del lado de Chilquinta: responde tráfico **sin TLS**
por el puerto 443, así que el handshake muere con
`SSLError: WRONG_VERSION_NUMBER` antes de que exista una respuesta HTTP. No es un
problema de certificado ni de nuestra configuración, y no se arregla desde acá:
se rodea. Las rutas internas del mismo host sirven TLS correctamente, y
`/mapas?emp=006` es la que el navegador carga de verdad, devuelve 200 y emite
`mapa_interrupciones_session`. Ver `VISOR_PATH`.

El 401 que se está resolviendo tenía, entonces, dos causas sumadas —sin sesión y
con un `orderId` que la aplicación tomaba por una orden ajena— y las tres piezas
de este módulo (priming, `Referer`, `ALL_ORDERS`) atacan una cada una. Si alguna
vez vuelve el 401, se pueden probar por separado: son independientes.

Las cabeceras como parámetros
------------------------------
Toda la consulta cabe en cinco cabeceras y ninguna es opcional:

    x-api-key         credencial; sin ella, 401
    X-Company-Code    filial a consultar. 006 = Chilquinta
    X-Orden-Buscada   orden de trabajo concreta, o `ALL_ORDERS` para todas
    Referer           la página del visor de la que "sale" el XHR
    Accept            application/json — se pide el JSON, no el HTML del visor

`X-Orden-Buscada` va siempre, nunca se omite: omitir la cabecera y mandarla con
el centinela no son lo mismo para este endpoint. Su valor es el mismo
`ALL_ORDERS` que viaja en la URL, para que la cabecera y el query digan lo mismo
— el backend lee uno de los dos y no sabemos cuál.

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
al proyecto. El priming duplica las peticiones —dos por corrida en vez de una—,
que sigue siendo menos de lo que gasta una persona con el visor abierto: el
navegador pide la página, el bundle, los tiles y después el JSON. Tener la llave
no es motivo para consultar más seguido.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.collectors.power.base_worker import BasePowerOutageCollector
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource

logger = logging.getLogger(__name__)

#: Cabecera donde el endpoint espera la llave. El nombre lo fija Chilquinta.
API_KEY_HEADER = "x-api-key"

#: Cabecera con la filial a consultar. Reemplaza al viejo `?emp=006` **en la
#: ruta de datos**; el `?emp=` no desapareció del visor, sigue en la URL de la
#: página. Ver `VISOR_EMP_PARAM`.
COMPANY_HEADER = "X-Company-Code"

#: Cabecera que acota la consulta a una orden de trabajo. `ALL_ORDERS` = todas.
ORDER_HEADER = "X-Orden-Buscada"

#: Página de la que el navegador "viene" al hacer el XHR. Laravel la mira: una
#: petición sin `Referer` a esta ruta es, para la aplicación, una petición que no
#: salió de su visor.
REFERER_HEADER = "Referer"

#: Formato que se pide. Explícito y no por omisión: la misma aplicación sirve
#: HTML para el visor, y una negociación de contenido ambigua puede devolver la
#: página de error en vez del JSON del error, que es mucho menos diagnosticable.
ACCEPT_HEADER = "Accept"

#: Lo que pide el XHR de datos.
JSON_ACCEPT = "application/json"

#: Lo que pide el navegador al cargar la página del visor. El priming manda esto
#: y no `application/json` a propósito: se está pidiendo la página, y una
#: aplicación Laravel a la que se le pide JSON en una ruta HTML puede responder
#: otra cosa —y con ella, otro `Set-Cookie`, o ninguno—.
HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

#: Cookie de sesión que emite el visor al cargarse. No se lee ni se copia a mano
#: —de eso se encarga el cookie jar de httpx—; se nombra para poder comprobar
#: que llegó y decirlo en el log cuando no llega.
SESSION_COOKIE = "mapa_interrupciones_session"

#: Ruta de la página del visor: la que se carga para abrir sesión y la que se
#: declara como `Referer`.
#:
#: **No es `/`, y el motivo no es preferencia.** La raíz del host está mal
#: configurada del lado de Chilquinta y responde tráfico sin TLS por el puerto
#: 443: el intento de priming contra ella muere en el handshake con
#: `SSLError: WRONG_VERSION_NUMBER`, sin llegar a haber respuesta HTTP. Las rutas
#: internas del mismo host sí sirven TLS, así que se carga la página que el
#: navegador carga de verdad. Devuelve 200 y emite `SESSION_COOKIE`.
#:
#: Ojo con la simetría inversa: `/mapas` es la ruta **correcta para la página** y
#: la equivocada para los datos —devuelve el HTML del visor—, mientras que
#: `/obtieneImage` es lo contrario. Las dos viven en este módulo y se parecen lo
#: suficiente como para que alguien "unifique" la que no debe.
VISOR_PATH = "/mapas"

#: Filial en la URL del visor. Es el mismo código que viaja en `COMPANY_HEADER`
#: y sale del mismo sitio (`CHILQUINTA_COD_EMP`): la página y el XHR tienen que
#: hablar de la misma empresa, y con dos literales acabarían no haciéndolo.
VISOR_EMP_PARAM = "emp"

#: Parámetro de query con el mismo significado que `ORDER_HEADER`. El endpoint
#: exige los dos: sin el de la URL responde
#: `400 {"error":"Missing required request parameters: [orderId]"}`, aunque la
#: cabecera vaya presente. Es redundancia del backend de Chilquinta, no nuestra
#: — se replica al pie de la letra porque adivinar cuál de los dos manda cuesta
#: un 400 por corrida.
ORDER_PARAM = "orderId"

#: Valor de `X-Orden-Buscada` y de `?orderId=` cuando se quiere el mapa
#: completo, que es siempre en este collector.
#:
#: **Ha valido tres cosas distintas y cada cambio lo forzó un servidor distinto.**
#: Vale la pena la historia entera, porque el valor "obvio" es el primero y los
#: otros dos sólo se explican por lo que rechazó cada capa:
#:
#: 1. `""` — lo intuitivo para "sin filtro". El **API Gateway** valida los
#:    parámetros obligatorios antes de enrutar y para él un valor de longitud
#:    cero no existe: `?orderId=` recibe el mismo
#:    `400 {"error":"Missing required request parameters: [orderId]"}` que no
#:    mandar nada.
#: 2. `"0"` — tiene longitud, así que pasa el gateway. Pero llega a **Laravel**,
#:    que lo trata como el identificador de una orden de trabajo y aplica la
#:    Policy de pertenencia: ¿es del solicitante la orden 0? No, no existe, y la
#:    respuesta es `401 {"error":"Esta petición no esta autorizada"}`. El 400
#:    había desaparecido, así que el `"0"` parecía correcto: era sólo un error
#:    más adentro.
#: 3. `"null"` — la cadena literal, no `None` ni `""`. Mantiene la longitud que
#:    el gateway exige y le dice a la aplicación que no hay orden que buscar, en
#:    vez de darle un identificador que su Policy pueda resolver —y denegar—.
#:
#: Es la cadena `"null"`, con comillas, y no un `None` de Python: si alguien
#: "limpia" esto convirtiéndolo al nulo del lenguaje, `urlencode` escribirá
#: `orderId=None` y volveremos al caso 2 con otro nombre.
ALL_ORDERS = "null"


def pagina_del_visor(url: str, *, emp: str = "") -> str:
    """Página del visor que acompaña al endpoint `url`, o `""` si no se deduce.

    Es lo que un navegador carga antes de hacer el XHR, y por tanto de dónde
    salen la cookie de sesión y el valor del `Referer`. En producción da
    `https://mapainterrupciones.chilquinta.cl/mapas?emp=006`.

    **Ruta fija, host derivado**, y cada mitad tiene su motivo:

    * la ruta es `VISOR_PATH` y no la raíz porque la raíz no habla TLS — ver
      allí, es un fallo de configuración de Chilquinta que sólo se puede rodear;
    * el host sale del endpoint configurado en vez de escribirse en una
      constante porque **una cookie está atada a su host**. Con el host escrito a
      mano, apuntar `CHILQUINTA_API_URL` a otro entorno dejaría un cookie jar
      lleno de cookies que httpx —correctamente— no enviaría a la petición de
      datos, y el síntoma sería el mismo 401 sin ninguna pista de por qué.
      Derivándolo, las dos peticiones comparten origen por construcción.

    `emp` se recibe en vez de leerse de `settings` acá dentro para que la función
    sea pura y se pueda probar sin tocar la configuración; quien la llama ya lo
    tiene. Vacío, se omite el parámetro en vez de mandar `?emp=`: un valor de
    longitud cero es exactamente lo que este proyecto ya aprendió a no enviar.

    Devuelve `""` en vez de lanzar cuando la URL no tiene esquema y host: sin
    ellos no hay nada que primar, y quien tiene que quejarse de una URL
    inservible es la petición de datos, con su error real, no el preámbulo.
    """
    partes = urlsplit(url)
    if not partes.scheme or not partes.netloc:
        return ""
    consulta = urlencode({VISOR_EMP_PARAM: emp}) if emp else ""
    return urlunsplit((partes.scheme, partes.netloc, VISOR_PATH, consulta, ""))


def con_order_id(url: str) -> str:
    """`url` con un `orderId` **no vacío** garantizado.

    Tres casos y una regla para cada uno:

    * no hay `orderId` → se añade `ALL_ORDERS`;
    * hay `orderId` pero vacío → se **reemplaza** por `ALL_ORDERS`;
    * hay `orderId` con valor → se respeta, porque es alguien consultando una
      orden concreta a propósito.

    El caso del medio es el que importa y el que no es obvio. Una versión
    anterior devolvía la URL intacta en cuanto veía la clave, sin mirar el valor:
    conservaba el `?orderId=` vacío que hoy sabemos que el API Gateway rechaza,
    así que un `.env` con la URL "ya arreglada" seguiría produciendo el 400 y el
    parche parecería no haber servido. Preservar la clave no basta; lo que el
    validador cuenta es la longitud del valor.

    Se manipula la URL con `urlsplit`/`urlencode` y no concatenando
    `"?orderId=null"` porque la concatenación asume que la URL no tiene query, y
    el día que alguien configure `…/obtieneImage?v=2` produciría
    `…?v=2?orderId=null`, que no es una URL válida y que el servidor rechazaría
    con el mismo 400 que se está intentando evitar.
    """
    partes = urlsplit(url)
    # `keep_blank_values` para *ver* un `orderId=` vacío en vez de que `parse_qsl`
    # lo descarte: hay que distinguirlo de la ausencia para poder corregirlo.
    consulta = parse_qsl(partes.query, keep_blank_values=True)
    corregida = [
        (clave, ALL_ORDERS if clave == ORDER_PARAM and not valor else valor)
        for clave, valor in consulta
    ]
    if not any(clave == ORDER_PARAM for clave, _ in corregida):
        corregida.append((ORDER_PARAM, ALL_ORDERS))
    return urlunsplit(partes._replace(query=urlencode(corregida)))


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
    #: GET sin cuerpo: los filtros van en cabeceras y en el `?orderId=`
    #: obligatorio de la URL. Ver `request_headers`, `request_url` y
    #: `request_payload`.
    http_method = "GET"

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        # Falla al construirse, igual que una URL ausente, y por el mismo
        # motivo: sin llave el endpoint responde 401 y el collector no puede
        # hacer su trabajo. Que reviente acá deja una fila `failed` en
        # `collector_runs` con la variable que falta en el mensaje, en vez de un
        # 401 repetido cada cinco minutos que hay que ir a leer al log.
        _require_api_key()

    # -- Sesión ---------------------------------------------------------------

    def visor_url(self) -> str:
        """La página del visor: la que se prima y la que se declara en `Referer`.

        Un solo método para los dos usos porque **tienen que coincidir**. El
        `Referer` es la afirmación "vengo de esta página" y el priming es lo que
        la hace cierta; si se calcularan por separado, un día dirían cosas
        distintas y la petición pasaría a ser una mentira con la forma correcta.

        Que el `Referer` lleve también el `?emp=006` no es un descuido: es lo que
        manda un navegador. Con la política de referrer por defecto, una petición
        al mismo origen declara la URL de la página **entera**, query incluida.
        """
        return pagina_del_visor(
            self.request_url(),
            emp=str(settings.CHILQUINTA_COD_EMP or "").strip(),
        )

    def priming_headers(self) -> dict[str, str]:
        """Cabeceras del GET al visor: las de un navegador abriendo la página.

        **Sin la API key**, y es deliberado. La llave autoriza la ruta de datos;
        la página del visor es pública y no la pide. Mandarla igualmente sería
        exponerla en una petición que no la necesita —a otra ruta, a otros logs
        intermedios, a un servidor de estáticos— a cambio de nada.

        Tampoco van `X-Company-Code` ni `X-Orden-Buscada`: no significan nada
        para una página HTML, y el objetivo del priming es parecerse a la carga
        del visor, no a un XHR con la URL equivocada.
        """
        headers = super().request_headers()
        headers[ACCEPT_HEADER] = HTML_ACCEPT
        return headers

    async def prime_session(self, client: httpx.AsyncClient) -> None:
        """GET silencioso al visor para que el cookie jar absorba la sesión.

        Es la primera mitad de lo que hace un navegador: cargar
        `/mapas?emp=006`. El servidor responde 200 con
        `Set-Cookie: mapa_interrupciones_session` (más el `XSRF-TOKEN`), httpx
        los guarda en el cookie jar de `client`, y la petición de datos que viene
        después los reenvía sola. No se lee ni se copia ninguna cookie acá: lo
        único que hay que garantizar es que las dos peticiones usen **este**
        cliente, y por eso el gancho recibe el cliente.

        La página es `/mapas` y no `/`. La raíz del host devuelve tráfico sin TLS
        por el 443 y el priming contra ella moría en el handshake con
        `SSLError: WRONG_VERSION_NUMBER`, sin respuesta HTTP que absorber. Ver
        `VISOR_PATH`.

        Sobre el CSRF: el `XSRF-TOKEN` entra al jar por el mismo camino, pero no
        se promueve a cabecera `X-XSRF-TOKEN` porque Laravel sólo verifica CSRF
        en métodos que escriben (POST, PUT, PATCH, DELETE) y esto es un GET. Si
        el 401 sobreviviera a este cambio, ese es el siguiente pomo que girar, y
        el token ya estará en `client.cookies` esperando.

        **Ningún fallo de acá es fatal**, y la asimetría es a propósito. Este es
        un preámbulo: si el visor no responde, la petición de datos dirá `401` o
        lo que corresponda, y ese error describe el problema mejor que "no pude
        cargar la página". Lo que no puede pasar es que falle en silencio, así
        que queda en el log —incluido el caso más traicionero, que responda 200 y
        aun así no emita la cookie—. Es `logger` y no `self.warn`
        deliberadamente: un priming fallido no degrada una corrida que después
        devuelve los cortes completos, y marcarla `partial` haría ruido justo en
        la señal que sirve para detectar degradaciones reales.

        Que el fallo no sea fatal es lo que hizo *barato* descubrir lo del TLS:
        el `WRONG_VERSION_NUMBER` salió por el log como una línea, no como una
        corrida perdida.
        """
        pagina = self.visor_url()
        if not pagina:
            logger.debug(
                "sin página de visor que primar",
                extra={"collector": self.name, "url": self.request_url()},
            )
            return

        try:
            respuesta = await client.get(pagina, headers=self.priming_headers())
        except Exception as exc:  # frontera con una fuente ajena
            logger.warning(
                "no se pudo preparar la sesión de Chilquinta; la petición de "
                "datos va sin cookie y probablemente reciba 401",
                extra={
                    "collector": self.name,
                    "url": pagina,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return

        if SESSION_COOKIE not in client.cookies:
            # 200 sin cookie es el caso que más se parece a "funcionó". Se nombra
            # lo que sí llegó: si el visor renombra su cookie, esta línea del log
            # es el único sitio donde se va a ver antes del 401.
            logger.warning(
                "el visor de Chilquinta respondió sin la cookie de sesión",
                extra={
                    "collector": self.name,
                    "url": pagina,
                    "status": respuesta.status_code,
                    "cookie_esperada": SESSION_COOKIE,
                    "cookies_recibidas": sorted(client.cookies.keys()),
                },
            )

    # -- Petición de datos ----------------------------------------------------

    def request_headers(self) -> dict[str, str]:
        """Las de la familia más las cinco que definen la consulta.

        Junto con el `?orderId=` de `request_url()`, estas cabeceras **son** los
        parámetros: no hay cuerpo, así que entre las dos piezas se arma la
        petición entera.

        Cuatro decisiones que no se ven en el resultado:

        * La llave se lee de `settings` en cada petición en vez de guardarse en
          la instancia. Si rota, basta reiniciar el proceso con el `.env` nuevo y
          no queda una copia vieja escondida en un atributo.
        * `X-Orden-Buscada` lleva `ALL_ORDERS` —el mismo valor que el query
          string— y **se envía siempre**: omitir la cabecera no es equivalente a
          mandarla con el centinela, así que si alguien la borra por parecer
          basura, la consulta cambia de significado.
        * `Referer` no es decoración ni cortesía: Laravel lo mira, y una petición
          sin él es una petición que no salió de su visor. Sale de `visor_url()`
          —la misma página que `prime_session()` acaba de cargar, `?emp=` y
          todo— para que la afirmación sea cierta y no sólo esté bien escrita.
        * La cookie de sesión **no aparece acá**. La pone el cookie jar de httpx
          a partir del priming, y añadirla a mano requeriría leerla, guardarla y
          mantenerla: tres oportunidades de que se quede vieja. Si en la petición
          saliente no hay `Cookie`, el que no corrió es `prime_session()`.

        Nada de esto llega a `run_params()`, y por tanto nada llega a
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
        headers[ACCEPT_HEADER] = JSON_ACCEPT
        referer = self.visor_url()
        if referer:
            headers[REFERER_HEADER] = referer
        return headers

    def request_url(self) -> str:
        """La URL configurada **más** `?orderId=null`, siempre.

        Este parámetro es obligatorio y además tiene que traer valor: sin él —o
        con él vacío— el endpoint responde
        `400 {"error":"Missing required request parameters: [orderId]"}` incluso
        con `X-Orden-Buscada` presente, porque el API Gateway valida antes de
        enrutar y para él un valor de longitud cero es una ausencia. Que el mismo
        dato tenga que ir en la cabecera *y* en la URL es redundancia del backend
        de Chilquinta; no hay forma de saber cuál de las dos lee sin provocar el
        400, así que van las dos.

        El *valor* del centinela es otra historia y está contada en `ALL_ORDERS`:
        pasar el gateway y ser aceptado por la aplicación resultaron ser dos
        requisitos distintos.

        Va acá y no en `request_payload()`, que es donde estuvo primero y donde
        no sobrevivió a producción. El camino del payload tiene dos puntos donde
        una query se evapora en silencio:

        * `load_records()` descarta el payload cuando es falsy, y `{"orderId": …}`
          es fácil de "simplificar" a `{}` en una limpieza bienintencionada;
        * `request_response` sólo pasa `params` a httpx cuando el diccionario
          tiene claves, porque httpx **reemplaza** la query de la URL con lo que
          reciba en `params`.

        La URL, en cambio, viaja tal cual la devuelve este método. Ese es todo el
        motivo del cambio: el parámetro obligatorio deja de depender de que un
        diccionario sobreviva tres capas.

        Se recalcula en cada petición en vez de fijarse en `__init__` para que
        siga valiendo si alguien reasigna `self.url` —los tests lo hacen— y para
        que la garantía no dependa del orden de construcción.
        """
        return con_order_id(super().request_url())

    def request_payload(self) -> dict[str, Any]:
        """Vacío a propósito: la consulta entera va en la URL y las cabeceras.

        **No devolver `{ORDER_PARAM: ALL_ORDERS}` acá.** Un payload con claves
        hace que `load_records()` se lo pase a httpx como `params`, y httpx
        *reemplaza* la query de la URL con lo que reciba — es decir, el
        `?orderId=null` que `request_url()` acaba de garantizar pasaría a
        depender otra vez de este diccionario. Vacío, la guarda de
        `request_response` manda `params=None` y la query de la URL llega
        intacta.

        Lo que sale por el cable es `…/obtieneImage?orderId=null`; quien lo
        garantiza es `request_url()`, no este método.
        """
        return {}

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.POWER_POLL_INTERVAL_SECONDS


__all__ = [
    "ACCEPT_HEADER",
    "ALL_ORDERS",
    "API_KEY_HEADER",
    "COMPANY_HEADER",
    "HTML_ACCEPT",
    "JSON_ACCEPT",
    "ORDER_HEADER",
    "ORDER_PARAM",
    "REFERER_HEADER",
    "SESSION_COOKIE",
    "VISOR_EMP_PARAM",
    "VISOR_PATH",
    "ChilquintaCollector",
    "con_order_id",
    "pagina_del_visor",
]
