"""Lectura del KMZ de afectaciones de CGE.

Por qué existe este módulo
---------------------------
CGE **no publica una API**. Lo que publica es un archivo de Google Earth
—`mapa_cge.kmz`— que su plataforma regenera cada pocos minutos y sirve como
estático. Un KMZ es un ZIP que contiene un KML, y un KML es XML: el mismo dato
que otras distribuidoras entregan como JSON, envuelto en dos capas más.

Eso deja el transporte de CGE fuera del camino de `request_json`, pero **no**
fuera del resto del pipeline: la salida de este módulo son diccionarios con las
mismas claves que `outage_parser` ya sabe leer (`lat`, `lon`,
`clientes_afectados`, `hora_reposicion`, `comuna`…). El KMZ se traduce acá, una
sola vez, y de ahí hacia adelante un corte de CGE es indistinguible de uno de
Chilquinta. Sin eso habría dos definiciones de "corte" en el proyecto.

Las tres trampas de este formato
--------------------------------
1. **El orden de las coordenadas.** En KML `<coordinates>` es `lon,lat[,alt]`,
   igual que en GeoJSON y al revés de como se escribe una coordenada al hablar.
   Invertirlo no produce ningún error: deposita todos los cortes de Valparaíso
   en el océano Índico, con coordenadas perfectamente válidas, y el filtro de
   bounding box los descarta en silencio. El síntoma sería "CGE no reporta
   nunca", que es exactamente el fallo más caro de diagnosticar.

2. **`ET.fromstring()` con un `str`.** El KML trae declaración de codificación
   (`<?xml version="1.0" encoding="UTF-8"?>`). Si se le pasa a ElementTree ya
   decodificado a `str`, lanza `ValueError: Unicode strings with encoding
   declaration are not supported`. Por eso `extract_kml` devuelve **bytes** y no
   texto: ElementTree lee la declaración y resuelve la codificación por su
   cuenta, que además es lo correcto si algún día CGE publica en ISO-8859-1.

3. **Los miles chilenos.** «1.250 clientes» son mil doscientos cincuenta, no
   uno coma veinticinco. `as_float("1.250")` devuelve `1.25` y el conteo se
   guardaría como 1. Se normaliza acá, antes de que el número salga del módulo:
   ver `_normalise_count`.

Qué NO está verificado
----------------------
El contenido de `<description>`. Es un bloque HTML armado por la plataforma de
CGE y no hay contrato publicado: puede ser una tabla, una lista o texto suelto,
y las etiquetas de los campos pueden cambiar sin aviso. Por eso el parseo de la
descripción es por expresiones regulares tolerantes con varios alias por campo,
y **toda ausencia produce `None`, nunca una excepción**. Un corte sin hora de
reposición estimada es un dato incompleto y normal —al principio de un corte
nadie sabe cuándo se repone—; un corte sin coordenadas sí se descarta, porque no
se puede pintar ni correlacionar.

Lo que sí es un fallo duro es que el archivo no sea un ZIP, que no traiga ningún
`.kml` dentro o que el XML no se pueda parsear: eso significa que CGE cambió el
formato y necesita a una persona. Se distingue con `KmzFormatError`.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Any
from xml.etree import ElementTree as ET

from app.collectors.geoservices import as_float, normalise_text
from app.collectors.power.outage_parser import CHILE_TZ

logger = logging.getLogger(__name__)

#: Techo del KML ya descomprimido. El KMZ de CGE pesa decenas de KB y descomprime
#: a unos pocos MB, así que 32 MB es holgura amplia; existe porque el destino de
#: producción es una instancia de 512 MB y el archivo lo sirve un tercero. Un ZIP
#: de 40 KB puede declarar gigabytes de contenido, y descubrirlo por OOM killer
#: es peor que descubrirlo por un mensaje.
MAX_KML_BYTES = 32 * 1024 * 1024

#: Cuánto de la descripción original se conserva en el registro para poder
#: reprocesar sin volver a descargar. Suficiente para reconstruir los campos a
#: mano; acotado para no meter una tabla HTML entera en cada fila de la base.
DESCRIPTION_SNIPPET = 2000


class KmzFormatError(Exception):
    """El archivo no es un KMZ legible. El collector lo traduce a `CollectorError`."""


# --- Descompresión en memoria ------------------------------------------------


def extract_kml(raw: bytes) -> bytes:
    """KMZ → los bytes del KML interno. Sin tocar el disco.

    El archivo se abre desde un `io.BytesIO` y no desde una ruta temporal a
    propósito: escribir en disco obligaría a limpiar el archivo aunque el parseo
    falle a mitad, y en un contenedor efímero con el sistema de archivos de sólo
    lectura ni siquiera hay dónde. `zipfile.ZipFile` acepta cualquier objeto con
    `read`/`seek`, así que el buffer en memoria le sirve igual.

    Elección del miembro, en orden: `doc.kml` —el nombre que usa la
    especificación de Google y el que escribe cualquier exportador—, si no está
    el único `.kml` que haya, y si hay varios el primero por orden alfabético,
    que al menos es determinista entre corridas. Las carpetas y los recursos
    (`files/`, iconos PNG) se ignoran.
    """
    try:
        archivo = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise KmzFormatError(
            f"la respuesta no es un ZIP válido ({len(raw)} bytes; "
            f"empieza con {raw[:16]!r})"
        ) from exc

    with archivo:
        miembros = [
            info
            for info in archivo.infolist()
            if not info.is_dir() and info.filename.lower().endswith(".kml")
        ]
        if not miembros:
            raise KmzFormatError(
                f"el KMZ no contiene ningún .kml; miembros: {archivo.namelist()[:10]}"
            )

        elegido = next(
            (i for i in miembros if i.filename.lower().endswith("doc.kml")),
            None,
        ) or min(miembros, key=lambda i: i.filename)

        # `file_size` es lo que el ZIP *declara*, y se comprueba antes de leer:
        # el punto de la guarda es no materializar la bomba, no detectarla
        # después de haberla materializado.
        if elegido.file_size > MAX_KML_BYTES:
            raise KmzFormatError(
                f"el KML interno declara {elegido.file_size} bytes, sobre el "
                f"máximo de {MAX_KML_BYTES}"
            )

        try:
            with archivo.open(elegido) as handle:
                # Se lee un byte de más para distinguir "justo en el límite" de
                # "el ZIP mintió en la cabecera", que es el caso que la guarda
                # anterior no cubre.
                contenido = handle.read(MAX_KML_BYTES + 1)
        except (zipfile.BadZipFile, OSError) as exc:
            raise KmzFormatError(
                f"no se pudo descomprimir '{elegido.filename}': {exc}"
            ) from exc

    if len(contenido) > MAX_KML_BYTES:
        raise KmzFormatError(
            f"el KML interno supera {MAX_KML_BYTES} bytes al descomprimir"
        )
    if not contenido.strip():
        raise KmzFormatError(f"el KML interno '{elegido.filename}' está vacío")

    logger.debug(
        "KML extraído del KMZ",
        extra={
            "miembro": elegido.filename,
            "comprimido_b": elegido.compress_size,
            "descomprimido_b": len(contenido),
            "miembros_kml": len(miembros),
        },
    )
    return contenido


# --- Recorrido del XML -------------------------------------------------------


def _local(tag: Any) -> str:
    """Nombre de la etiqueta sin el namespace.

    KML declara `http://www.opengis.net/kml/2.2`, pero circulan documentos con
    el 2.0, el 2.1 y con extensiones de Google (`gx:`). ElementTree entrega las
    etiquetas expandidas —`{http://www.opengis.net/kml/2.2}Placemark`— así que
    buscar por el nombre local es lo único que funciona con las tres versiones
    sin mantener una lista de namespaces que CGE puede cambiar sin avisar.
    """
    if not isinstance(tag, str):
        return ""
    return tag.rpartition("}")[2]


def _text_of(node: ET.Element) -> str:
    """Texto del nodo, incluido el de sus hijos. Vacío si no hay."""
    return "".join(node.itertext()).strip()


def _coordinates_of(placemark: ET.Element) -> tuple[float, float] | None:
    """Punto representativo del Placemark: el centroide de sus vértices.

    Un `<Placemark>` de CGE puede ser un `<Point>` (un corte puntual), un
    `<Polygon>` (el sector afectado) o un `<MultiGeometry>` con varios. En vez de
    bifurcar por tipo de geometría se recogen **todos** los `<coordinates>` que
    cuelguen del Placemark y se promedia: para un Point el centroide es el punto
    mismo, así que el caso simple no paga nada por soportar los otros.

    El anillo de un polígono repite el primer vértice al final y eso sesga el
    promedio una fracción de metro. Es irrelevante para posicionar un marcador y
    corregirlo costaría distinguir anillos de líneas, que es precisamente la
    bifurcación que este enfoque evita.
    """
    puntos: list[tuple[float, float]] = []

    for nodo in placemark.iter():
        if _local(nodo.tag) != "coordinates" or not nodo.text:
            continue
        # KML separa las tuplas con espacios, tabuladores o saltos de línea, y
        # los exportadores los mezclan. `.split()` sin argumento cubre los tres.
        for tupla in nodo.text.split():
            partes = tupla.split(",")
            if len(partes) < 2:
                continue
            # lon,lat[,alt] — el orden de la especificación KML. Ver el docstring
            # del módulo: invertirlo no falla, sólo manda todo al océano Índico.
            lon = as_float(partes[0])
            lat = as_float(partes[1])
            if lat is None or lon is None:
                continue
            puntos.append((lat, lon))

    if not puntos:
        return None

    total = len(puntos)
    return (
        sum(lat for lat, _ in puntos) / total,
        sum(lon for _, lon in puntos) / total,
    )


def _extended_data_of(placemark: ET.Element) -> dict[str, str]:
    """Pares nombre/valor de `<ExtendedData>`, si CGE los publica.

    Es el lugar *correcto* del formato para datos estructurados, y si están son
    más confiables que sacarlos de la descripción a golpe de expresión regular.
    Se leen las dos formas del esquema: `<Data name=…><value>` y
    `<SchemaData><SimpleData name=…>`.

    No siempre existen —muchos exportadores vuelcan todo en el HTML de
    `<description>`— así que esto complementa y no reemplaza al parseo de texto.
    """
    datos: dict[str, str] = {}

    for nodo in placemark.iter():
        etiqueta = _local(nodo.tag)
        nombre = nodo.get("name")
        if not nombre:
            continue

        if etiqueta == "Data":
            valor = next(
                (_text_of(hijo) for hijo in nodo if _local(hijo.tag) == "value"),
                None,
            )
        elif etiqueta == "SimpleData":
            valor = _text_of(nodo)
        else:
            continue

        if valor:
            datos[str(nombre).strip()] = valor

    return datos


# --- Lectura de la descripción HTML ------------------------------------------

#: Etiquetas que separan un campo del siguiente. Se convierten en salto de línea
#: *antes* de borrar el resto del marcado: sin eso «Horario de reposición:
#: 18:30» y el campo que le sigue quedarían en la misma línea y la captura hasta
#: fin de línea se tragaría los dos.
#:
#: `td` y `th` NO están acá, y es la parte que hay que no romper: en
#: `<td>Comuna</td><td>Quillota</td>` la etiqueta y su valor son dos celdas de la
#: *misma* fila. Cortar por celda las separaría en dos líneas y ningún patrón
#: —todos son «etiqueta … valor» en una línea— volvería a emparejarlas. Las
#: celdas se disuelven en espacios con `_ANY_TAG`; lo que corta es la fila.
_LINE_BREAK_TAGS = re.compile(
    r"<\s*/?\s*(?:br|/tr|p|div|li|h[1-6])[^>]*>", re.IGNORECASE
)
_ANY_TAG = re.compile(r"<[^>]*>")

#: Alias por campo. Se prueban en orden hasta que uno responde; cubrir las
#: variantes cuesta una línea cada una y evita que el collector reporte cero
#: clientes porque CGE escribió «N° de clientes» en vez de «Clientes afectados».
_CLIENTS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"clientes?\s*afectad[oa]s?\s*[:=\-–]*\s*([\d][\d.,]*)", re.IGNORECASE),
    re.compile(
        r"(?:n[°ºo]\s*(?:de\s*)?)?clientes?\s*[:=\-–]*\s*([\d][\d.,]*)", re.IGNORECASE
    ),
    re.compile(r"afectad[oa]s?\s*[:=\-–]*\s*([\d][\d.,]*)", re.IGNORECASE),
    re.compile(r"suministros?\s*afectad[oa]s?\s*[:=\-–]*\s*([\d][\d.,]*)", re.IGNORECASE),
)

_RESTORE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"horario\s*(?:estimad[oa]\s*)?(?:de\s*)?reposici[oó]n\s*[:=\-–]*\s*([^\n]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:hora|fecha)\s*(?:y\s*hora\s*)?(?:estimad[oa]\s*)?(?:de\s*)?"
        r"reposici[oó]n\s*[:=\-–]*\s*([^\n]+)",
        re.IGNORECASE,
    ),
    re.compile(r"reposici[oó]n\s*(?:estimada)?\s*[:=\-–]*\s*([^\n]+)", re.IGNORECASE),
    re.compile(r"(?:hora\s*)?estimada?\s*de\s*normalizaci[oó]n\s*[:=\-–]*\s*([^\n]+)",
               re.IGNORECASE),
)

_START_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:hora|fecha)\s*(?:de\s*)?(?:inicio|interrupci[oó]n)\s*[:=\-–]*\s*([^\n]+)",
        re.IGNORECASE,
    ),
    re.compile(r"inicio\s*(?:del\s*corte)?\s*[:=\-–]*\s*([^\n]+)", re.IGNORECASE),
)

_COMMUNE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"comuna\s*[:=\-–]*\s*([^\n]+)", re.IGNORECASE),
    re.compile(r"localidad\s*[:=\-–]*\s*([^\n]+)", re.IGNORECASE),
)

_SECTOR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sector\s*[:=\-–]*\s*([^\n]+)", re.IGNORECASE),
    re.compile(r"direcci[oó]n\s*[:=\-–]*\s*([^\n]+)", re.IGNORECASE),
)

_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:folio|n[°ºo]\s*de\s*aviso|orden|ticket)\s*[:=\-–]*\s*([\w\-/]+)",
               re.IGNORECASE),
)

#: Lo que estas plataformas escriben cuando todavía no tienen el dato. Se
#: compara normalizado (sin tildes, en minúsculas) porque aparece de las dos
#: formas en el mismo archivo.
_NO_DATA = frozenset(
    {
        "",
        "-",
        "--",
        "n/a",
        "na",
        "sin informacion",
        "sin info",
        "sin dato",
        "sin datos",
        "por definir",
        "por determinar",
        "no definido",
        "no informado",
        "indeterminado",
        "en evaluacion",
        "null",
        "none",
    }
)

#: Unidad colgando al final del valor: «18:30 hrs.», «19:00 horas».
_TRAILING_UNIT = re.compile(r"[\s.]*\b(?:hrs?|hras?|horas?)\b\.?\s*$", re.IGNORECASE)

#: Sólo hora, sin fecha. Ver `_resolve_bare_time`.
_BARE_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")

#: Miles a la chilena: 1.250 / 12.345 / 1,250. Ver el docstring del módulo.
_THOUSANDS = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")
_PLAIN_INT = re.compile(r"^\d+$")


def plain_text(html: str) -> str:
    """HTML de `<description>` → texto plano con un campo por línea.

    Se llama a `unescape` dos veces y no es un descuido. Estas descripciones
    suelen venir doblemente escapadas: el KML guarda `&lt;br&gt;`, ElementTree lo
    entrega ya como `<br>` literal en el texto del nodo, y dentro de ese HTML
    quedan todavía entidades propias (`&nbsp;`, `&aacute;`). Un solo paso deja
    una de las dos capas sin resolver, y «Reposición» aparecería como
    «Reposici&oacute;n» — que ninguna de las expresiones regulares reconoce.
    """
    texto = unescape(html or "")
    texto = _LINE_BREAK_TAGS.sub("\n", texto)
    texto = _ANY_TAG.sub(" ", texto)
    texto = unescape(texto)
    # `\xa0` es el espacio duro de `&nbsp;`: sobrevive a `unescape` y no lo
    # reconoce `\s` en todos los modos, así que se normaliza a espacio.
    texto = texto.replace("\xa0", " ")

    lineas = [" ".join(linea.split()) for linea in texto.splitlines()]
    return "\n".join(linea for linea in lineas if linea)


def _match_first(texto: str, patrones: Iterable[re.Pattern[str]]) -> str | None:
    for patron in patrones:
        encontrado = patron.search(texto)
        if encontrado:
            valor = encontrado.group(1).strip()
            if valor:
                return valor
    return None


def _is_no_data(valor: str) -> bool:
    return normalise_text(valor).strip(" .:-") in _NO_DATA


def _normalise_count(token: str | None) -> str | None:
    """Texto del conteo → entero en texto, o None si no es un conteo.

    Devolver None en vez de arriesgar un número equivocado es deliberado: el
    número de clientes afectados es un dato que se muestra al ciudadano, y
    publicar «1 cliente afectado» cuando son 1.250 es peor que no publicar nada.
    Por eso `1.25` —que no es ni miles ni entero— se rechaza en lugar de
    redondearse.
    """
    if token is None:
        return None
    limpio = token.strip().strip(".,;:")
    if not limpio:
        return None
    if _THOUSANDS.match(limpio):
        return limpio.replace(".", "").replace(",", "")
    if _PLAIN_INT.match(limpio):
        return limpio
    return None


def _resolve_bare_time(valor: str, *, now: datetime | None = None) -> str:
    """«18:30» → «20-08-2026 18:30». Cualquier otra cosa pasa sin tocarse.

    CGE publica varias reposiciones sólo con la hora, dando por supuesto el día
    en que se está mirando el mapa. Guardar esa hora sin fecha no sirve: aguas
    abajo `parse_timestamp` no la reconoce y el campo se pierde entero.

    La fecha se resuelve contra el reloj **chileno**, no el del servidor, que
    corre en UTC: a las 02:00 UTC en Chile todavía es el día anterior, y usar
    UTC adelantaría un día toda reposición leída de noche.

    La holgura de seis horas hacia atrás es el criterio central: una reposición
    anunciada para las 18:30 y leída a las 19:00 es una reposición atrasada de
    hoy —lo más común durante un corte— y no una de mañana. Más allá de esa
    ventana lo probable es que sea del día siguiente. El caso ambiguo se resuelve
    hacia "hoy" a propósito: mostrar una reposición vencida invita a mirar el
    dato; mostrarla 24 horas en el futuro genera alarma sin razón.
    """
    encontrado = _BARE_TIME.match(valor)
    if not encontrado:
        return valor

    hora, minuto = int(encontrado.group(1)), int(encontrado.group(2))
    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        return valor

    ahora = (now or datetime.now(UTC)).astimezone(CHILE_TZ)
    objetivo = ahora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    if objetivo < ahora - timedelta(hours=6):
        objetivo += timedelta(days=1)
    return objetivo.strftime("%d-%m-%Y %H:%M")


def parse_description(html: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Bloque HTML de un Placemark → los campos del corte que se puedan leer.

    Sólo se devuelven las claves que se encontraron. Las ausentes se omiten en
    vez de ir con `None`, para que `outage_parser._first` pueda caer sobre un
    alias de `<ExtendedData>` cuando la descripción no traiga el campo.
    """
    texto = plain_text(html)
    if not texto:
        return {}

    campos: dict[str, Any] = {}

    clientes = _normalise_count(_match_first(texto, _CLIENTS_PATTERNS))
    if clientes is not None:
        campos["clientes_afectados"] = clientes

    for clave, patrones in (
        ("hora_reposicion", _RESTORE_PATTERNS),
        ("hora_inicio", _START_PATTERNS),
    ):
        crudo = _match_first(texto, patrones)
        if crudo is None:
            continue
        valor = _TRAILING_UNIT.sub("", crudo).strip(" .;,|")
        if not valor or _is_no_data(valor):
            continue
        campos[clave] = _resolve_bare_time(valor, now=now)

    for clave, patrones in (
        ("comuna", _COMMUNE_PATTERNS),
        ("sector", _SECTOR_PATTERNS),
        ("folio", _ID_PATTERNS),
    ):
        valor = _match_first(texto, patrones)
        if valor and not _is_no_data(valor):
            campos[clave] = valor.strip(" .;,|")

    return campos


# --- Placemarks → registros --------------------------------------------------


def parse_placemarks(kml: bytes, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """KML → un diccionario por corte, con las claves que `outage_parser` lee.

    La salida deliberadamente **no** es `PowerOutage`: se devuelven mappings para
    que `parse_outage` haga la conversión de tipos, la validación de que la
    coordenada cae en Chile y el anclaje de la hora chilena a UTC. Ese código ya
    existe, ya está probado y es el que usa Chilquinta; duplicarlo acá sería
    tener dos definiciones de "corte" que se irían separando con el tiempo.

    Un Placemark sin coordenadas legibles se omite —sin él no hay nada que
    pintar—, pero no interrumpe la lectura del resto: un archivo operativo trae
    carpetas de leyenda y marcadores decorativos, y perder la corrida entera por
    uno sería cambiar un dato faltante por doscientos.
    """
    try:
        raiz = ET.fromstring(kml)
    except ET.ParseError as exc:
        raise KmzFormatError(f"el KML no es XML válido: {exc}") from exc

    registros: list[dict[str, Any]] = []
    sin_coordenadas = 0

    for nodo in raiz.iter():
        if _local(nodo.tag) != "Placemark":
            continue

        coordenadas = _coordinates_of(nodo)
        if coordenadas is None:
            sin_coordenadas += 1
            continue

        lat, lon = coordenadas
        nombre = ""
        descripcion = ""
        for hijo in nodo:
            etiqueta = _local(hijo.tag)
            if etiqueta == "name" and not nombre:
                nombre = _text_of(hijo)
            elif etiqueta == "description" and not descripcion:
                descripcion = _text_of(hijo)

        # `<ExtendedData>` primero y la descripción encima: cuando la expresión
        # regular acierta un campo, ese valor gana, porque está etiquetado en el
        # texto que ve el usuario. Cuando no acierta, la clave ni siquiera está
        # en `campos` y lo que quedó de `<ExtendedData>` sobrevive para que
        # `_first` lo encuentre por alias.
        registro: dict[str, Any] = dict(_extended_data_of(nodo))
        registro.update(parse_description(descripcion, now=now))

        registro["lat"] = lat
        registro["lon"] = lon

        # El id de CGE, en orden de preferencia. Sin uno estable,
        # `build_external_id` cae al hash de ubicación + inicio, que también
        # sirve; tenerlo es mejor porque sobrevive a que CGE mueva el marcador
        # unos metros al refinar el sector.
        identificador = (
            nodo.get("id") or registro.get("folio") or (nombre if nombre else None)
        )
        if identificador:
            registro["id"] = str(identificador).strip()

        if nombre:
            registro.setdefault("sector", nombre)
            registro["_kml_name"] = nombre
        if descripcion:
            # Recortado: sirve para reprocesar y para depurar el día que CGE
            # cambie las etiquetas, no para archivar su HTML.
            registro["_kml_description"] = plain_text(descripcion)[:DESCRIPTION_SNIPPET]

        registros.append(registro)

    if sin_coordenadas:
        logger.debug(
            "placemarks sin coordenadas legibles",
            extra={"omitidos": sin_coordenadas, "conservados": len(registros)},
        )

    return registros


def parse_kmz(raw: bytes, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Bytes descargados → registros de corte. El camino completo, en memoria."""
    return parse_placemarks(extract_kml(raw), now=now)


def describe_kmz(raw: bytes, *, limit: int = 10) -> str:
    """Qué llegó, para el mensaje de error. El equivalente de `describe_shape`.

    Existe por la misma razón que su gemelo del parser JSON: el primer despliegue
    contra el archivo real es donde esto se va a equivocar, y un error que dice
    «llegó text/html de 4 KB» se depura en minutos mientras que «no se pudo leer
    el KMZ» se depura a ciegas.
    """
    cabecera = raw[:4]
    if cabecera[:2] != b"PK":
        muestra = raw[:120].decode("utf-8", errors="replace")
        return f"{len(raw)} bytes que no son un ZIP; empieza con: {muestra!r}"
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archivo:
            nombres = archivo.namelist()[:limit]
        return f"ZIP de {len(raw)} bytes con miembros: {nombres}"
    except zipfile.BadZipFile:
        return f"ZIP de {len(raw)} bytes ilegible"


__all__ = [
    "DESCRIPTION_SNIPPET",
    "MAX_KML_BYTES",
    "KmzFormatError",
    "describe_kmz",
    "extract_kml",
    "parse_description",
    "parse_kmz",
    "parse_placemarks",
    "plain_text",
]
