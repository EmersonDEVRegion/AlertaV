"""De `raw_data` al enlace público de una señal.

Para qué existe
---------------
Cada collector guarda en `raw_data` la traza de dónde salió lo que ingirió, pero
**cada uno con su propia llave**: la prensa escribe `url`, Instagram
`permalink`, Bomberos deja el identificador del tuit dentro de `_bomberos.guid`.
Eso está bien —`raw_data` es el crudo de cada fuente y forzarlo a un esquema
común borraría información— pero significa que el panel no puede pedir "el
enlace" sin saber de qué fuente viene.

Este módulo es esa traducción, y vive acá y no en el schema por dos motivos: es
lógica de presentación que no le corresponde a ningún collector, y es pura, así
que se testea sin sesión, sin base y sin configuración.

Por qué el esquema se valida
----------------------------
Estas URL vienen de **HTML raspado**. `local_news_worker` las arma con
`urljoin(portal.base_url, href)` sobre un `href` que escribió un tercero, y el
resultado va a parar a un `href` del navegador de un usuario. Un `javascript:`
—o un `data:` con HTML dentro— colado por ahí se ejecuta en el origen de la
aplicación.

`urljoin` no protege de eso: resuelve rutas relativas, no juzga esquemas, y
`urljoin("https://portal.cl/", "javascript:alert(1)")` devuelve el
`javascript:` intacto. Por eso la lista blanca de `http`/`https` es explícita y
va acá, en el borde de salida, y no en cada collector: un collector nuevo que
olvide validar no abre el agujero, porque el enlace igual pasa por esta función
antes de llegar al cliente.

El caso que NO es un enlace
---------------------------
Los despachos de Bomberos guardan en `_bomberos.guid` lo que el Actor de Apify
haya puesto como identificador del tuit, y `_ID_KEYS` prefiere `id` —un número
suelto— sobre `url`. O sea: a veces es una URL y a veces es `1962…`. No se
construye la URL de X a mano a partir del número, porque eso obligaría a
adivinar el nombre de la cuenta y produciría enlaces rotos con aire de válidos.
La validación de esquema resuelve el caso sola: lo que no es `http(s)` no es un
enlace y la señal se muestra sin él.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from app.models.enums import EventSource

#: Esquemas que pueden llegar a un `href`. Lista blanca, no lista negra: los
#: esquemas peligrosos no se pueden enumerar (`javascript:`, `data:`, `vbscript:`,
#: `blob:`, y los que inventen mañana), pero los útiles sí.
_ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Tope defensivo. Una URL legítima de estos portales no pasa de unos cientos de
#: caracteres; algo de 8 KB es basura del raspado y no vale la pena servirla.
_MAX_URL_LENGTH = 2048

#: Dónde busca cada fuente su enlace, en orden de preferencia. Las claves con
#: punto descienden por diccionarios anidados.
#:
#: `url` y `link` cierran la lista de casi todas porque son las llaves genéricas
#: que usan los collectors que copian el crudo del portal entero
#: (`**dict(notice.raw)` en Transporte Informa, por ejemplo).
_URL_KEYS: dict[EventSource, tuple[str, ...]] = {
    EventSource.MEDIA: ("url", "link"),
    EventSource.SOCIAL_MEDIA: ("permalink", "url"),
    EventSource.BOMBEROS: ("_bomberos.guid", "url"),
    EventSource.TRANSPORTE_INFORMA: ("url", "link", "detalle_url"),
}

#: Fuentes sin entrada propia. No es lo mismo que "no tiene enlace": un
#: collector puede empezar a guardar `url` mañana y esto lo recoge solo.
_URL_KEYS_DEFAULT: tuple[str, ...] = ("url", "link", "permalink")

#: De dónde sale el nombre humano de la señal: el medio que la publicó, la
#: cuenta que la posteó. Es lo que el panel muestra en vez de repetir la banda.
_LABEL_KEYS: dict[EventSource, tuple[str, ...]] = {
    EventSource.MEDIA: ("_prensa.medio", "_prensa.portal"),
    EventSource.SOCIAL_MEDIA: ("cuenta",),
}

_MAX_LABEL_LENGTH = 80


def _descend(data: Mapping[str, Any], path: str) -> Any:
    """Valor en `data` siguiendo una ruta con puntos. None si se corta."""
    actual: Any = data
    for segmento in path.split("."):
        if not isinstance(actual, Mapping):
            return None
        actual = actual.get(segmento)
        if actual is None:
            return None
    return actual


def is_safe_url(value: Any) -> bool:
    """¿Es una URL que se puede poner en un `href` sin pensarlo dos veces?

    Exige esquema explícito y host. Una URL relativa (`/nota/123`) devuelve
    False a propósito: sin saber el portal no se puede resolver, y resolverla
    contra el origen de AlertaV produciría un enlace a una página nuestra que no
    existe.
    """
    if not isinstance(value, str):
        return False

    texto = value.strip()
    if not texto or len(texto) > _MAX_URL_LENGTH:
        return False

    # Los caracteres de control se cuelan en el raspado y algunos navegadores
    # los ignoran al resolver el esquema: "java\nscript:" llegaría a ejecutarse
    # si sólo se comparara el prefijo. `urlsplit` ya los rechaza en el esquema,
    # pero un enlace con un salto de línea dentro no es un enlace de todos modos.
    if any(caracter in texto for caracter in "\r\n\t"):
        return False

    try:
        partes = urlsplit(texto)
    except ValueError:
        return False

    return partes.scheme.lower() in _ALLOWED_SCHEMES and bool(partes.netloc)


def source_url_for(source: EventSource, raw_data: Any) -> str | None:
    """Enlace público de la señal, o None si no hay uno utilizable.

    None cubre tres casos que al cliente le dan igual y por eso no se
    distinguen: la fuente no publica enlaces (Chilquinta, CGE, SENAPRED), el
    collector todavía no lo guarda, o lo que hay no es una URL (el `guid`
    numérico de un tuit).
    """
    if not isinstance(raw_data, Mapping):
        return None

    for path in _URL_KEYS.get(source, _URL_KEYS_DEFAULT):
        valor = _descend(raw_data, path)
        if is_safe_url(valor):
            return str(valor).strip()

    return None


def source_label_for(source: EventSource, raw_data: Any) -> str | None:
    """Nombre humano de quien publicó la señal: «Pura Noticia», «@cuenta».

    None cuando la fuente no tiene un nombre más específico que su propia banda.
    En ese caso el panel muestra la banda y no inventa nada — que es distinto de
    mostrar una cadena vacía.
    """
    if not isinstance(raw_data, Mapping):
        return None

    for path in _LABEL_KEYS.get(source, ()):
        valor = _descend(raw_data, path)
        if isinstance(valor, str) and valor.strip():
            texto = valor.strip()[:_MAX_LABEL_LENGTH]
            # El arroba se agrega acá y no se guarda en `raw_data`: ahí vive el
            # identificador de la cuenta, que es lo que se usa para volver a
            # consultarla. La decoración es del panel.
            if source is EventSource.SOCIAL_MEDIA and not texto.startswith("@"):
                texto = f"@{texto}"
            return texto

    return None
