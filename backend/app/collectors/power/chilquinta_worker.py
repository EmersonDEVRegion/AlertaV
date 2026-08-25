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

`prime_session()` reproduce el efecto de eso: una petición previa con el
**mismo** `httpx.AsyncClient`, cuyo cookie jar absorbe el `Set-Cookie` y lo
reenvía solo en la petición de datos. No hay que leer la cookie ni copiarla a
mano; hay que hacer las dos peticiones con el mismo cliente y en ese orden. Ver
el gancho `BasePowerOutageCollector.prime_session`, que existe por este caso.

**Pero no se carga la página: se golpea la propia ruta de datos.** El priming
pide `/obtieneImage` a sabiendas de que va a ser rechazado, porque Laravel abre
sesión al atender la petición aunque después no la autorice, y ese rechazo trae
las dos cookies. Es un caballo de Troya y conviene llamarlo por su nombre.

Se llegó ahí rodeando, no entendiendo. Cargar la página HTML —primero `/`, luego
`/mapas?emp=006`— moría con `SSLError: WRONG_VERSION_NUMBER`, un fallo de
handshake TLS. La ruta de datos negocia bien, así que el preámbulo se mudó a
ella y el visor dejó de ser un punto de fallo. Lo que **no** hay es una
explicación cerrada: un drop por huella TLS no puede depender de la ruta, porque
el path viaja cifrado y el servidor decide antes de conocerlo. La hipótesis viva
es una redirección hacia otro esquema o puerto; ver `prime_session`, que
conserva el `follow_redirects=False` y registra el `Location` por si hay que
volver a tirar de ese hilo.

El 401 resultó tener **tres** causas sumadas, y se descubrieron de a una porque
cada arreglo destapaba la siguiente: la petición no pertenecía a ninguna sesión,
el `orderId` se tomaba por una orden ajena, y faltaba el token CSRF. Las cuatro
piezas de este módulo (priming, `Referer`, `ALL_ORDERS`, `X-XSRF-TOKEN`) atacan
una cada una y son independientes: si el 401 vuelve, se pueden descartar por
separado.

Las cabeceras como parámetros
------------------------------
Toda la consulta cabe en seis cabeceras y ninguna es opcional:

    x-api-key         credencial; sin ella, 401
    X-Company-Code    filial a consultar. 006 = Chilquinta
    X-Orden-Buscada   orden de trabajo concreta, o `ALL_ORDERS` para todas
    Referer           la página del visor de la que "sale" el XHR
    Accept            application/json — se pide el JSON, no el HTML del visor
    X-XSRF-TOKEN      el token de la cookie `XSRF-TOKEN`, url-decodificado

La última contradice el comportamiento estándar de Laravel, que sólo verifica
CSRF en métodos que escriben. Se manda igual porque **la evidencia mandó sobre
la doctrina**: el 401 sobrevivió a la sesión y al `Referer`, y lo que hay delante
es un firewall que valida por su cuenta. Si algún día deja de hacer falta, sobra
una cabecera; no mandarla cuando hace falta cuesta la capa eléctrica entera.

Y es `X-XSRF-TOKEN`, con la cookie cifrada, y **no** `X-CSRF-TOKEN` con el token
en claro del `<meta name="csrf-token">`. Se intentó en ese orden y el primero no
sirvió: son dos tokens distintos con dos cabeceras distintas, y cruzarlos falla
igual que no mandar nada. Está explicado en `leer_xsrf`.

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
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

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

#: Nombre de la cabecera del `User-Agent`. Se nombra para poder afirmar en un
#: test que el de AlertaV va en **las dos** peticiones: hubo una versión en la
#: que el priming se disfrazaba de Chrome, y se quitó al dejar de cargar HTML.
USER_AGENT_HEADER = "User-Agent"

#: Cookie de sesión que emite el visor al cargarse. No se lee ni se copia a mano
#: —de eso se encarga el cookie jar de httpx—; se nombra para poder comprobar
#: que llegó y decirlo en el log cuando no llega.
SESSION_COOKIE = "mapa_interrupciones_session"

#: Cabecera con el token CSRF **cifrado**, el que sale de la cookie. Ojo con la
#: X de más: `X-XSRF-TOKEN` y `X-CSRF-TOKEN` son cabeceras distintas para tokens
#: distintos, y confundirlas es el fallo silencioso de todo esto. Ver `leer_xsrf`.
XSRF_HEADER = "X-XSRF-TOKEN"

#: Cookie de la que sale ese token. La emite Laravel junto con la de sesión y,
#: a diferencia de aquella, no basta con que httpx la reenvíe: la aplicación la
#: quiere **también** como cabecera. Que la cookie viaje sola no alcanza.
XSRF_COOKIE = "XSRF-TOKEN"

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


def _es_fallo_de_handshake(exc: BaseException) -> bool:
    """¿El fallo ocurrió negociando TLS, antes de mandar una sola cabecera?

    Sirve para una única cosa, y es la que ahorra la próxima tarde perdida:
    decidir si tiene sentido tocar las cabeceras del priming. **En un handshake
    fallido el servidor no vio ninguna.** Lo que sale al cable primero es el
    ClientHello; si el otro extremo contesta texto plano, la conexión muere ahí y
    el `User-Agent`, el `Accept` y el `Referer` nunca existieron para él. Un
    disfraz de navegador no puede arreglar eso, por convincente que sea.

    Cuando esto devuelve `True`, lo que hay que mirar está por debajo de HTTP:
    el puerto al que se está apuntando, un `HTTPS_PROXY` en el entorno, el SNI,
    o la huella del propio ClientHello. Cuando devuelve `False`, el servidor sí
    leyó la petición y ahí las cabeceras sí pueden cambiar su respuesta.

    Se detecta por el texto porque httpx envuelve los errores de `ssl` en
    `ConnectError` sin conservar el tipo original: no hay una excepción
    específica que capturar.
    """
    texto = f"{type(exc).__name__}: {exc}".lower()
    return "ssl" in texto or "wrong_version_number" in texto or "handshake" in texto


def leer_xsrf(cookies: httpx.Cookies) -> str | None:
    """Token de la cookie `XSRF-TOKEN`, url-decodificado, o `None`.

    Laravel publica el token CSRF en dos sitios y **no son intercambiables**:

    * el `<meta name="csrf-token">` lo lleva **en claro** y corresponde a la
      cabecera `X-CSRF-TOKEN`;
    * la cookie `XSRF-TOKEN` lo lleva **cifrado** por la aplicación —y encima
      url-encodeado— y corresponde a `X-XSRF-TOKEN`, que la aplicación descifra
      antes de comparar.

    Se probaron los dos, en ese orden, y el que este despliegue acepta es el
    segundo. Cruzarlos —mandar el valor del meta como `X-XSRF-TOKEN`, o el de la
    cookie como `X-CSRF-TOKEN`— produce un rechazo idéntico al de no mandar nada
    y sin ninguna pista de que el problema es el emparejamiento; de ahí que la
    cabecera y la fuente se lean juntas, en esta función, y no en dos sitios.

    `unquote` y **nunca `unquote_plus`**. La diferencia parece cosmética y no lo
    es: el token es base64, el alfabeto base64 incluye `+`, y `unquote_plus`
    convierte cada `+` en un espacio. Eso corrompe el token de forma
    intermitente —sólo cuando al cifrado le toca un `+`, o sea a veces— y
    produce un 401 que aparece y desaparece sin patrón. En una cookie el `+` es
    un `+` literal; el `%2B` es lo que se decodifica.

    Devuelve `None` —y no lanza— cuando la cookie no está o viene vacía. Un
    token de longitud cero se trata como ausente por el mismo motivo que
    `?orderId=`: mandar la cabecera vacía afirma tener un token y presenta uno
    inválido, que es peor que no afirmar nada.
    """
    try:
        crudo = cookies.get(XSRF_COOKIE)
    except httpx.CookieConflict:
        # Varias cookies con el mismo nombre para rutas o dominios distintos.
        # Pasa cuando el priming atraviesa una redirección que la re-emite con
        # otro `path`. Se toma la última que entró al jar, que es la que el
        # servidor acaba de emitir: es una heurística, pero la alternativa es
        # dejar que un `CookieConflict` tumbe una corrida por un preámbulo.
        crudo = next(
            (
                galleta.value
                for galleta in reversed(list(cookies.jar))
                if galleta.name == XSRF_COOKIE
            ),
            None,
        )
    if not crudo:
        return None
    return unquote(crudo).strip() or None


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

    @property
    def xsrf_token(self) -> str | None:
        """Token de la cookie leído en el priming de **esta** corrida, o `None`.

        Se inicializa perezosamente y no en `__init__` por la convención del
        proyecto: los tests construyen collectors con `__new__` para probar
        `normalize()` sin tocar sesión ni base de datos, y un atributo de
        instancia normal rompería esos tests. Es el mismo patrón que
        `BaseCollector.warnings`.

        Que sea estado mutable en la instancia es incómodo, y es el precio de que
        la aplicación quiera el mismo dato por dos canales: la cookie la reenvía
        el jar de httpx solo, pero la **cabecera** hay que armarla, y para eso
        hay que acordarse del valor entre una petición y la siguiente. Vive lo
        que vive la corrida — ver el reseteo al principio de `prime_session`.
        """
        return getattr(self, "_xsrf_token", None)

    def visor_url(self) -> str:
        """La página del visor, que ahora sirve sólo para el `Referer`.

        Ya **no** se visita: el priming pasó a golpear la ruta de datos. Sigue
        calculándose porque el `Referer` es una afirmación sobre de dónde vendría
        un navegador —"este XHR sale de la página del visor"— y esa página es la
        misma tanto si nosotros la cargamos como si no. Un navegador que abre
        `/mapas?emp=006` y luego pide `/obtieneImage` manda exactamente esto.

        Que lleve también el `?emp=006` no es un descuido: con la política de
        referrer por defecto, una petición al mismo origen declara la URL de la
        página **entera**, query incluida.
        """
        return pagina_del_visor(
            self.request_url(),
            emp=str(settings.CHILQUINTA_COD_EMP or "").strip(),
        )

    def priming_url(self) -> str:
        """A dónde se golpea para abrir sesión: la **propia ruta de datos**.

        Antes era la página del visor. El cambio tiene una consecuencia que no se
        ve y que decide si esto funciona: **el priming tiene que pasar el API
        Gateway**. El gateway valida los parámetros obligatorios antes de enrutar
        y contesta `400 Missing required request parameters: [orderId]` sin
        molestar a Laravel; una petición "desnuda" a `/obtieneImage` moriría ahí,
        Laravel no llegaría a ejecutarse y **no habría sesión que emitir**. Sería
        un priming que no prima nada.

        Por eso esto devuelve `request_url()` —con su `?orderId=null`— y no la
        ruta pelada: el objetivo es llegar hasta Laravel y que sea *él* quien
        rechace, porque su rechazo es el que trae las cookies.
        """
        return self.request_url()

    def priming_headers(self) -> dict[str, str]:
        """Las mismas cabeceras de la petición de datos, sin el token.

        Es literalmente `request_headers()`, y no una coincidencia: el priming
        **es** la petición de datos hecha una vez sin credencial de sesión, para
        que la aplicación conteste con un rechazo que trae las cookies. Que sea
        el mismo método garantiza que las dos peticiones no puedan divergir.

        No hace falta quitarle nada. `prime_session()` resetea el token antes de
        llamar acá, así que en ese momento `request_headers()` omite sola el
        `X-XSRF-TOKEN` —no lo tiene todavía— y devuelve exactamente la petición
        que buscamos. Si algún día alguien invierte ese orden, el priming
        mandaría el token de la corrida anterior y el efecto sería sutil y feo;
        el reseteo está comentado allá arriba por eso.

        Aquí murió el disfraz de navegador. Mientras el priming cargaba una
        página HTML tenía sentido parecerse a un navegador; ahora consulta la
        misma ruta de API que el resto del collector, donde un `User-Agent` de
        Chrome sería más raro que el nuestro, no menos. Con eso vuelve además la
        política del proyecto —identificarse ante un servidor ajeno— a **todas**
        las peticiones, y desaparece la asimetría de dos `User-Agent` distintos
        dentro de una misma sesión, que era en sí misma una señal.
        """
        return self.request_headers()

    async def prime_session(self, client: httpx.AsyncClient) -> None:
        """Una primera petición a la ruta de datos, sin token, para abrir sesión.

        El patrón es el del caballo de Troya: se golpea `/obtieneImage` sabiendo
        que va a ser rechazado. Lo que interesa **no** es el cuerpo de la
        respuesta sino sus `Set-Cookie`: Laravel abre sesión al atender la
        petición, aunque después decida no autorizarla, así que el rechazo trae
        `mapa_interrupciones_session` y `XSRF-TOKEN`. httpx los guarda en el
        cookie jar de `client` y la petición de datos que viene detrás los
        reenvía sola. Lo único que hay que garantizar es que las dos usen **este**
        cliente, y por eso el gancho recibe el cliente.

        No hace falta capturar ningún `HTTPStatusError`: `client.get()` no lanza
        ante un 4xx —sólo lo haría `raise_for_status()`, que no se llama acá—, de
        modo que un 401 llega como una respuesta normal y sus cookies entran al
        jar por el camino de siempre. Está comprobado en los tests.

        Por qué se dejó de cargar `/mapas?emp=006`
        -------------------------------------------
        Porque el priming contra la página HTML moría con
        `SSLError: WRONG_VERSION_NUMBER` y la ruta de datos no. La ruta de datos
        es, además, la única que este collector necesita que funcione: si ella
        negocia TLS, el preámbulo puede vivir ahí y el visor deja de ser un punto
        de fallo. Es un rodeo, no una explicación.

        La explicación sigue sin cerrar, y conviene decirlo en vez de dejar que
        el próximo la reconstruya mal. **Un drop por huella TLS —JA3— no puede
        depender de la ruta**: la huella está en el ClientHello y el path viaja
        cifrado, o sea después, así que en el momento de decidir el servidor no
        sabe qué ruta se le va a pedir. Sobre el mismo host y puerto, "TLS falla
        en `/mapas` pero no en `/obtieneImage`" no es literalmente posible; httpx
        además reutiliza la misma conexión para las dos, así que un handshake que
        sirve para una sirve para la otra.

        Lo que sí encaja con los hechos observados es que el fallo ocurriera en
        una **segunda** conexión: el visor contesta un 3xx hacia otro esquema o
        puerto y seguirlo abre una conexión nueva contra algo que habla texto
        plano. De ahí el `follow_redirects=False` que se conserva más abajo. Si
        alguna vez hay que volver a `/mapas`, ese es el hilo del que tirar, y el
        log del `Location` lo dirá en una línea.

        La segunda cosa que se trae de acá es el **token CSRF**. Está en la
        cookie `XSRF-TOKEN`, que el jar ya reenvía sola, pero eso no basta: la
        aplicación quiere el mismo valor **también** como cabecera, y esa hay que
        armarla. Por eso este método lee el jar después del GET y guarda el token
        en la instancia, y por eso `request_headers()` depende de que esto haya
        corrido antes.

        Antes se leía del `<meta name="csrf-token">` y se mandaba en
        `X-CSRF-TOKEN`. Ese camino se probó primero y **no fue el que este
        despliegue acepta**; se abandonó al confirmarse. Los dos tokens no son
        intercambiables y las dos cabeceras tampoco — ver `leer_xsrf`, donde está
        la diferencia escrita, porque es exactamente el error que se cometió.

        Que la aplicación exija CSRF en un **GET** contradice el comportamiento
        estándar de Laravel, que sólo lo verifica en métodos que escriben. Se
        manda igual porque la evidencia manda sobre la doctrina: el 401
        sobrevivió a la sesión y al `Referer`, y lo que queda delante es un
        firewall que valida por su cuenta. Si algún día esto deja de hacer falta,
        sobra una cabecera; no mandarla cuando hace falta cuesta la capa entera.

        **Ningún fallo de acá es fatal**, y la asimetría es a propósito. Este es
        un preámbulo: si la petición no llega, la de datos dirá `401` o lo que
        corresponda, y ese error describe el problema mejor que "no pude hacer el
        preámbulo". Lo que no puede pasar es que falle en silencio, así que queda
        en el log —incluidos los dos casos traicioneros: que responda sin emitir
        la cookie de sesión, y que la emita pero no la del token—. Es `logger` y
        no `self.warn` deliberadamente: un priming fallido no degrada una corrida
        que después devuelve los cortes completos, y marcarla `partial` haría
        ruido justo en la señal que sirve para detectar degradaciones reales.

        Que el fallo no sea fatal es lo que hizo *barato* descubrir lo del TLS:
        el `WRONG_VERSION_NUMBER` salió por el log como una línea, no como una
        corrida perdida.

        Cortesía: esto **no** añade peticiones. Antes eran dos por corrida —el
        visor y los datos— y siguen siendo dos; lo que cambia es que las dos van
        ahora a la misma ruta. Vale la pena saberlo por si alguna vez el operador
        pregunta por qué ve el doble de consultas a `/obtieneImage`: es el
        preámbulo, a la cadencia de siempre.
        """
        # Primero de todo, y aunque parezca redundante con el `__init__` que no
        # existe: el token pertenece a **una** sesión. Si esta corrida no
        # consigue primar, lo que queda de la anterior no sirve —su sesión ya no
        # está en el cookie jar, que es nuevo en cada corrida— y mandarlo
        # emparejaría un token viejo con una sesión ausente. Un 401 por no mandar
        # nada se depura; uno por mandar un token que no corresponde, no.
        self._xsrf_token: str | None = None

        pagina = self.priming_url()
        if not pagina:
            logger.debug(
                "sin URL que primar",
                extra={"collector": self.name, "url": self.request_url()},
            )
            return

        try:
            # `follow_redirects=False` **sólo acá**, contra el `True` del cliente
            # de la familia. Se conserva aunque el priming ya no toque la ruta
            # HTML: si un 3xx apunta a un destino con esquema o puerto equivocado
            # —`http://…`, o un `https://host:80`—, seguirlo abre una conexión
            # **nueva** que negocia TLS contra un puerto que habla texto plano, y
            # ahí muere. Sin seguirlo, la respuesta llega entera y sus
            # `Set-Cookie` entran igual al jar, que es lo único que se venía a
            # buscar.
            respuesta = await client.get(
                pagina, headers=self.priming_headers(), follow_redirects=False
            )
        except Exception as exc:  # frontera con una fuente ajena
            logger.warning(
                "no se pudo preparar la sesión de Chilquinta; la petición de "
                "datos va sin cookie ni token y probablemente reciba 401",
                extra={
                    "collector": self.name,
                    "url": pagina,
                    "error": f"{type(exc).__name__}: {exc}",
                    # La pista que decide dónde mirar la próxima vez. Un fallo de
                    # handshake ocurre ANTES de que salga un solo byte de HTTP:
                    # el servidor no vio el `User-Agent`, ni el `Accept`, ni
                    # nada. Si el error es de TLS, cambiar cabeceras no puede
                    # arreglarlo y hay que mirar más abajo —SNI, huella del
                    # ClientHello, un proxy en el entorno, el puerto—.
                    "cabeceras_llegaron_al_servidor": not _es_fallo_de_handshake(exc),
                },
            )
            return

        if respuesta.is_redirect:
            # Se registra el destino porque es exactamente el dato que faltaba:
            # un `Location` con `http://` o con `:80` explica el
            # `WRONG_VERSION_NUMBER` sin necesidad de capturar tráfico.
            logger.warning(
                "el priming de Chilquinta recibió una redirección; no se sigue "
                "a propósito",
                extra={
                    "collector": self.name,
                    "url": pagina,
                    "status": respuesta.status_code,
                    "location": respuesta.headers.get("location", ""),
                },
            )
        elif respuesta.is_success:
            # El preámbulo esperaba un rechazo y le contestaron que sí. No es un
            # problema —las cookies llegan igual y la corrida sigue— pero sí una
            # señal fuerte: si la ruta responde 200 sin token, la teoría del CSRF
            # dejó de ser cierta y sobra media clase. Queda en info, no en
            # warning: nada está degradado, sólo desactualizado.
            logger.info(
                "el priming de Chilquinta fue autorizado sin token; revisar si "
                "la cabecera CSRF sigue haciendo falta",
                extra={
                    "collector": self.name,
                    "url": pagina,
                    "status": respuesta.status_code,
                },
            )

        # Del jar y no del cuerpo: el token que este despliegue acepta es el
        # cifrado que viaja en la cookie, no el que la plantilla imprime en el
        # HTML. Se lee **después** del GET porque es esa respuesta la que lo
        # emite.
        self._xsrf_token = leer_xsrf(client.cookies)
        if self._xsrf_token is None:
            logger.warning(
                "Chilquinta no emitió la cookie del token CSRF; la petición de "
                "datos va sin la cabecera",
                extra={
                    "collector": self.name,
                    "url": pagina,
                    "status": respuesta.status_code,
                    "cookie_esperada": XSRF_COOKIE,
                    "cookies_recibidas": sorted(client.cookies.keys()),
                },
            )

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
        """Las de la familia más las que definen la consulta y la sesión.

        Junto con el `?orderId=` de `request_url()`, estas cabeceras **son** los
        parámetros: no hay cuerpo, así que entre las dos piezas se arma la
        petición entera.

        **Este método depende de que `prime_session()` haya corrido**, y es la
        única dependencia temporal del módulo. `X-XSRF-TOKEN` sale de una cookie
        que sólo existe si el priming la trajo; la garantía es el orden dentro de
        `load_records()`, donde el gancho se llama antes de que estas cabeceras
        se construyan. Llamado suelto —los tests lo hacen— simplemente omite la
        cabecera en vez de reventar: es una degradación honesta, no un error de
        programación.

        Cinco decisiones que no se ven en el resultado:

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
        * `X-XSRF-TOKEN` es la excepción a lo anterior, y no porque la cookie no
          viaje —viaja— sino porque la aplicación quiere el mismo valor **por
          los dos canales**: la cookie y la cabecera. Que el jar reenvíe la
          primera no arma la segunda. Va **sólo si existe**: mandar la cabecera
          vacía sería peor que no mandarla —afirma tener un token y presenta uno
          inválido—, y este proyecto ya sabe lo que cuesta un valor de longitud
          cero: ver `ALL_ORDERS`.

        Nada de esto llega a `run_params()`, y por tanto nada llega a
        `collector_runs`. Una credencial en la traza es una credencial visible
        para cualquiera que consulte el historial de corridas — y el token CSRF
        cuenta como tal: identifica una sesión viva.
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
        if self.xsrf_token:
            headers[XSRF_HEADER] = self.xsrf_token
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
    "JSON_ACCEPT",
    "ORDER_HEADER",
    "ORDER_PARAM",
    "REFERER_HEADER",
    "SESSION_COOKIE",
    "USER_AGENT_HEADER",
    "VISOR_EMP_PARAM",
    "VISOR_PATH",
    "XSRF_COOKIE",
    "XSRF_HEADER",
    "ChilquintaCollector",
    "con_order_id",
    "leer_xsrf",
    "pagina_del_visor",
]
