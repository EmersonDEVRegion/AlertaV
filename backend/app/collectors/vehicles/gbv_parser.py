"""Lectura pura del sitio de GBV SpA: HTML → fichas de vehículos.

Nada de este módulo hace I/O. Recibe HTML ya descargado y devuelve estructuras;
el collector (`gbv_worker`) decide qué pedir, qué es nuevo y qué se ingesta.

Cómo publica GBV (verificado el 2026-09-23)
-------------------------------------------
Tres secciones con la MISMA maqueta de tarjetas (`div.grid_A > div`):

* **Denuncias** — `/denuncias/?pag=N`, 200 tarjetas por página, de la más nueva
  a la más vieja. La tarjeta sólo trae el lugar; el resto viaja en el enlace:
  `/denuncias/{tipo}-{delito}-{patente}-{id}`. El detalle agrega marca, modelo,
  color, año y fecha del delito (AAAA-MM-DD, sin hora). **La patente no está en
  la tabla del detalle**: sólo en el enlace y en el título de la página.
* **Recuperados** — `/vehiculos-recuperados`, una sola página. La tarjeta dice
  "Auto Changan  Uní T" —tipo, marca y, tras DOS espacios, modelo— y enlaza a
  la denuncia original, que desaparece del listado de denuncias. Su detalle
  agrega "recuperado en" y "autoridad presente". Sin fecha de recuperación.
* **Abandonados** — `/vehiculos-abandonados/`, una sola página. La tarjeta trae
  lugar, tiempo de abandono ("3 semanas") y tipo; el enlace NO trae patente, y
  el detalle la trae sólo a veces ("Bwph17", "Sin patente" o vacío).

Lo que esto obliga a resolver acá
---------------------------------
* **La patente sale del enlace, no del texto.** Aplicar una regex de patente al
  texto libre encuentra patentes donde no las hay; aplicarla al hueco del
  enlace es exacto. La regex valida ese hueco, no lo busca.
* **Hay patentes mal escritas en origen** (`cshz796`, `pdsl30k`). No se
  corrigen: la ficha entra identificada por su id de GBV y el collector avisa.
* **No hay comuna.** El lugar es texto libre —"los araucanos 290", "Reñaca",
  "Mall maipu"— y el sitio es nacional. `ubicar()` decide sin geocodificar.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from bs4.element import Tag

from app.collectors.geoservices import normalise_text
from app.collectors.lugares import COMUNAS_NORMALIZADAS
from app.core.exceptions import CollectorError
from app.models.enums import VehicleLocation, VehicleStatus

logger = logging.getLogger(__name__)

#: Las fechas de GBV son días calendario chilenos.
CHILE_TZ = ZoneInfo("America/Santiago")

# -- Patentes -----------------------------------------------------------------

#: Las 18 consonantes de la serie 2007+: sin vocales, sin M, N, Ñ ni Q.
CONSONANTES_PATENTE = "BCDFGHJKLPRSTVWXYZ"

#: Patente chilena ya normalizada (mayúsculas, sin separadores).
#:
#: Los dos primeros formatos son los de autos que circulan hoy; los de moto y
#: los anunciados por el MTT van incluidos para que el día que empiecen a
#: aparecer no caigan en silencio al respaldo por id.
#:
#: Los rangos numéricos son los de la especificación: en autos, el formato
#: 2007+ numera de 10 a 99 y el antiguo de 1000 a 9999, así que un cero inicial
#: no es patente de auto. **En motos sí**: la placa se rotula con un cero de
#: relleno (`BBB·010`, `AB·0123`) y así la publica GBV — `RGT012`, `PDC068`,
#: `SLG017` en el listado del 2026-09-23. Ese cero es lo que distingue una moto
#: antigua (`AB0123`) de un auto antiguo (`AB1234`).
PATENTE_CL = re.compile(
    rf"""(?<![A-Z0-9])(?:
        [{CONSONANTES_PATENTE}]{{4}}[1-9][0-9]      # auto 2007+        BBBB·10
      | [A-Z]{{2}}[1-9][0-9]{{3}}                   # auto 1985-2007    AA·1000
      | [{CONSONANTES_PATENTE}]{{3}}0?[1-9][0-9]    # moto 2014+        BBB·010
      | [A-Z]{{2}}0?[1-9][0-9]{{2}}                 # moto antigua      AA·0100
      | [{CONSONANTES_PATENTE}]{{5}}[0-9]          # auto anunciada    BBBBB·0
      | [{CONSONANTES_PATENTE}]{{4}}[0-9]          # moto anunciada    BBBB·0
    )(?![A-Z0-9])""",
    re.VERBOSE,
)

#: Motos escritas sin el cero de relleno. Se les agrega para que `RGT12` y
#: `RGT012` —la misma placa escrita por dos personas— sean una sola clave.
_MOTO_SIN_RELLENO = (
    re.compile(rf"([{CONSONANTES_PATENTE}]{{3}})([1-9][0-9])"),
    re.compile(r"([A-Z]{2})([1-9][0-9]{2})"),
)

#: Sólo autos, formato antiguo (2 letras) y nuevo (4 letras). Es la forma
#: mínima de la regla; `PATENTE_CL` la contiene.
PATENTE_AUTO_CL = re.compile(
    rf"(?<![A-Z0-9])(?:[{CONSONANTES_PATENTE}]{{4}}[1-9][0-9]|[A-Z]{{2}}[1-9][0-9]{{3}})(?![A-Z0-9])"
)

_SEPARADORES_PATENTE = re.compile(r"[\s·.\-]")


def normalizar_patente(valor: str | None) -> str:
    """Mayúsculas y sin separadores: `bb·bb-10` → `BBBB10`."""
    return _SEPARADORES_PATENTE.sub("", (valor or "").strip().upper())


def patente_valida(valor: str | None) -> str | None:
    """La patente normalizada si tiene forma de patente chilena; si no, None.

    `fullmatch`, no `search`: esto valida un hueco que ya se sabe que es la
    patente, no busca una patente dentro de un texto.
    """
    normalizada = normalizar_patente(valor)
    if not normalizada or not PATENTE_CL.fullmatch(normalizada):
        return None
    for patron in _MOTO_SIN_RELLENO:
        coincidencia = patron.fullmatch(normalizada)
        if coincidencia:
            return f"{coincidencia.group(1)}0{coincidencia.group(2)}"
    return normalizada


#: Cuántos tramos del enlace puede ocupar la patente. Quien la escribe a veces
#: la separa con guiones y el slug los conserva: `aj-2072` (AJ2072),
#: `jc-fg-70` (JCFG70), ambos en los recuperados del 2026-09-23.
_MAX_TRAMOS_PATENTE = 3


def patente_del_enlace(tokens: list[str]) -> tuple[str | None, str | None, int]:
    """Patente dentro de los tramos que preceden al id del enlace.

    Prueba el último tramo, después los dos últimos unidos, después los tres, y
    se queda con el PRIMERO que valida: el tramo simple es el caso normal y
    unir más sólo se intenta si ese falla. Unir con el delito nunca produce una
    patente válida —sus tramos finales son palabras largas: "publica",
    "hurto", "indebida"—, y la regex es estricta, así que el intento no
    fabrica falsos positivos.

    Devuelve (patente válida o None, lo publicado, tramos que ocupó).
    """
    for largo in range(1, min(_MAX_TRAMOS_PATENTE, len(tokens)) + 1):
        tramo = tokens[-largo:]
        valida = patente_valida("".join(tramo))
        if valida:
            return (valida, "-".join(tramo), largo)
    return (None, tokens[-1] if tokens else None, 1)


# -- Secciones ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Seccion:
    clave: str
    ruta: str
    estado: VehicleStatus
    #: Segmento que tiene que aparecer en el enlace de cada tarjeta. Una
    #: tarjeta que apunta a otra parte (un banner, un servicio) no es un aviso.
    segmento_enlace: str
    paginada: bool = False


DENUNCIAS = Seccion("denuncias", "/denuncias/", VehicleStatus.ROBADO, "/denuncias/", True)
RECUPERADOS = Seccion(
    "recuperados", "/vehiculos-recuperados", VehicleStatus.RECUPERADO, "/denuncias/"
)
ABANDONADOS = Seccion(
    "abandonados",
    "/vehiculos-abandonados/",
    VehicleStatus.ABANDONADO,
    "/vehiculos-abandonados/",
)
SECCIONES: tuple[Seccion, ...] = (DENUNCIAS, RECUPERADOS, ABANDONADOS)


def url_de_listado(base_url: str, seccion: Seccion, pagina: int = 1) -> str:
    """URL de una página del listado. La página 1 va sin parámetro, como la
    enlaza el propio sitio."""
    url = f"{base_url.rstrip('/')}{seccion.ruta}"
    if seccion.paginada and pagina > 1:
        return f"{url}?pag={pagina}"
    return url


# -- Fichas del listado -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FichaListado:
    """Una tarjeta del listado: lo que se sabe sin abrir el detalle."""

    seccion: str
    estado: VehicleStatus
    id_gbv: int
    url: str
    #: Patente normalizada y válida, o None.
    patente: str | None = None
    #: Lo que venía en el hueco de la patente, tal cual. Distingue "no traía"
    #: de "traía algo que no es una patente".
    patente_publicada: str | None = None
    tipo_vehiculo: str | None = None
    #: Delito según el enlace ("robo desde via publica"). El detalle lo trae
    #: bien escrito; este es el respaldo.
    delito_enlace: str | None = None
    lugar: str | None = None
    marca: str | None = None
    modelo: str | None = None
    tiempo_abandono: str | None = None
    #: Texto de la tarjeta tal cual. Va a `raw_data` para poder reprocesar.
    titulo: str | None = None

    @property
    def external_id(self) -> str:
        """Clave de idempotencia y del delta.

        `gbv:{estado}:{PATENTE}` cuando hay patente válida: dos denuncias de la
        misma patente son el mismo auto buscado (GBV publica duplicados), y el
        estado va en la clave para que la RECUPERACIÓN sea un evento nuevo y no
        una actualización invisible de la fila del robo — el upsert no toca
        `ingested_at`, que es lo que mide la ventana del feed.

        Sin patente válida —abandonados, patentes mal escritas— la identidad es
        el id de GBV, con `id:` delante para que nunca choque con una patente.
        """
        if self.patente and self.estado is not VehicleStatus.ABANDONADO:
            return f"gbv:{self.estado.value}:{self.patente}"
        return f"gbv:{self.estado.value}:id:{self.id_gbv}"

    def as_raw(self) -> dict[str, Any]:
        return {
            "id_gbv": self.id_gbv,
            "url": self.url,
            "titulo": self.titulo,
            "patente_publicada": self.patente_publicada,
            "tipo_vehiculo": self.tipo_vehiculo,
            "delito_enlace": self.delito_enlace,
            "tiempo_abandono": self.tiempo_abandono,
        }


@dataclass(slots=True)
class LecturaListado:
    fichas: list[FichaListado] = field(default_factory=list)
    #: Tarjetas que no se pudieron leer, con el motivo. No se pueden recordar
    #: —no tienen identidad—, así que el collector decide cuánto ruido merecen.
    descartadas: list[str] = field(default_factory=list)
    #: Tarjetas de la grilla, leídas o no.
    tarjetas: int = 0


_ID_FINAL = re.compile(r"-(\d+)/?$")


def _texto(nodo: Tag | None) -> str | None:
    """Texto de un nodo, sin bordes. Conserva los espacios internos: en los
    recuperados, dos espacios separan la marca del modelo."""
    if nodo is None:
        return None
    valor = nodo.get_text().strip()
    return valor or None


def _limpio(valor: str | None) -> str | None:
    """Texto sin espacios repetidos, o None si no queda nada."""
    if valor is None:
        return None
    valor = " ".join(valor.split()).strip(" .,;")
    return valor or None


def _tabla(nodo: Tag | BeautifulSoup) -> dict[str, str]:
    """`table.table_caracteristicas` → {etiqueta normalizada: valor}.

    Las etiquetas vienen como `ano`, `lugar de delito`, `tiempo_abandono`: se
    normalizan (minúsculas, sin tildes, `_` → espacio) para que un cambio
    cosmético en el sitio no rompa la lectura. Los valores quedan tal cual.
    """
    campos: dict[str, str] = {}
    tabla = nodo.select_one("table.table_caracteristicas")
    if tabla is None:
        return campos
    for fila in tabla.find_all("tr"):
        celdas = fila.find_all("td")
        if len(celdas) < 2:
            continue
        etiqueta = normalise_text(celdas[0].get_text().replace("_", " "))
        valor = celdas[1].get_text().strip()
        if etiqueta:
            campos[etiqueta] = valor
    return campos


def _partir_titulo_recuperado(titulo: str | None) -> tuple[str | None, str | None, str | None]:
    """ "Auto Changan  Uní T" → ("Auto", "Changan", "Uní T").

    El sitio arma el título como `{tipo} {marca}  {modelo}`, con DOS espacios
    antes del modelo. Si algún día los colapsa, se pierde la separación entre
    marca y modelo pero no el tipo.
    """
    if not titulo:
        return (None, None, None)
    partes = [parte for parte in re.split(r"\s{2,}", titulo.strip()) if parte.strip()]
    if not partes:
        return (None, None, None)
    cabeza = partes[0].split(None, 1)
    tipo = cabeza[0] if cabeza else None
    marca = _limpio(cabeza[1]) if len(cabeza) > 1 else None
    modelo = _limpio(" ".join(partes[1:])) if len(partes) > 1 else None
    return (tipo, marca, modelo)


def _slug(url: str) -> str:
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]


def _ficha_de_denuncia(seccion: Seccion, url: str, id_gbv: int, titulo: str | None) -> FichaListado:
    """Denuncias y recuperados: `{tipo}-{delito}-{patente}-{id}`."""
    tokens = _slug(url).split("-")
    tipo = delito = publicada = patente = None
    if len(tokens) >= 4:
        tipo = tokens[0].capitalize() or None
        # Entre el tipo y el id: el delito y, al final, la patente.
        medio = tokens[1:-1]
        patente, publicada, largo = patente_del_enlace(medio[1:])
        delito = " ".join(medio[: len(medio) - largo]) or None

    lugar = marca = modelo = None
    if seccion.estado is VehicleStatus.RECUPERADO:
        tipo_titulo, marca, modelo = _partir_titulo_recuperado(titulo)
        tipo = tipo_titulo or tipo
    else:
        lugar = _limpio(titulo)

    return FichaListado(
        seccion=seccion.clave,
        estado=seccion.estado,
        id_gbv=id_gbv,
        url=url,
        patente=patente,
        patente_publicada=publicada,
        tipo_vehiculo=tipo,
        delito_enlace=delito,
        lugar=lugar,
        marca=marca,
        modelo=modelo,
        titulo=_limpio(titulo),
    )


def _ficha_de_abandonado(
    seccion: Seccion, url: str, id_gbv: int, tarjeta: Tag, titulo: str | None
) -> FichaListado:
    campos = _tabla(tarjeta)
    return FichaListado(
        seccion=seccion.clave,
        estado=seccion.estado,
        id_gbv=id_gbv,
        url=url,
        tipo_vehiculo=_limpio(campos.get("vehiculo")),
        lugar=_limpio(titulo),
        tiempo_abandono=_limpio(campos.get("tiempo abandono")),
        titulo=_limpio(titulo),
    )


def parse_listado(html: str, *, seccion: Seccion, url_pagina: str) -> LecturaListado:
    """Tarjetas de una página de listado.

    Levanta `CollectorError` cuando la página no tiene la grilla o la grilla
    viene vacía. Las tres secciones tienen siempre avisos —cientos—, así que un
    cero es un cambio de maqueta o una página de error servida con HTTP 200, y
    reportarlo como "no hay vehículos" sería el fallo silencioso que este
    proyecto persigue.
    """
    soup = BeautifulSoup(html, "lxml")
    grilla = soup.select_one("div.grid_A")
    if grilla is None:
        titulo = _limpio(soup.title.get_text()) if soup.title else None
        raise CollectorError(
            f"GBV {seccion.clave}: la página no tiene la grilla de avisos (div.grid_A); "
            f"probable cambio de maqueta. Título recibido: {titulo!r}"
        )

    lectura = LecturaListado()
    for tarjeta in grilla.find_all("div", recursive=False):
        lectura.tarjetas += 1
        enlace = tarjeta.find("a", href=True)
        if enlace is None:
            lectura.descartadas.append("tarjeta sin enlace")
            continue
        url = urljoin(url_pagina, str(enlace["href"]))
        if seccion.segmento_enlace not in urlsplit(url).path:
            lectura.descartadas.append(f"enlace ajeno a la sección: {url}")
            continue
        coincidencia = _ID_FINAL.search(urlsplit(url).path)
        if coincidencia is None:
            lectura.descartadas.append(f"enlace sin id: {url}")
            continue

        titulo = _texto(tarjeta.select_one(".titulo_MINI"))
        id_gbv = int(coincidencia.group(1))
        if seccion.estado is VehicleStatus.ABANDONADO:
            ficha = _ficha_de_abandonado(seccion, url, id_gbv, tarjeta, titulo)
        else:
            ficha = _ficha_de_denuncia(seccion, url, id_gbv, titulo)
        lectura.fichas.append(ficha)

    if lectura.tarjetas == 0:
        raise CollectorError(
            f"GBV {seccion.clave}: la grilla de avisos vino vacía. La sección nunca "
            f"está vacía en la práctica: probable cambio de maqueta."
        )
    return lectura


def hay_pagina_siguiente(html: str, pagina: int) -> bool:
    """¿El paginador enlaza a `?pag={pagina + 1}`?

    Con borde numérico: el paginador de la página 1 enlaza a `pag=33`, y una
    búsqueda de subcadena leería ahí un `pag=3` que no está.
    """
    return re.search(rf"[?&]pag={pagina + 1}(?!\d)", html) is not None


# -- Detalle ------------------------------------------------------------------


def parse_detalle(html: str) -> dict[str, str]:
    """Tabla de características de la ficha de detalle.

    Levanta `CollectorError` si la página no la tiene: una ficha sin tabla es
    una página de error o un cambio de maqueta, no un vehículo sin datos.
    """
    soup = BeautifulSoup(html, "lxml")
    campos = _tabla(soup)
    if not campos:
        titulo = _limpio(soup.title.get_text()) if soup.title else None
        raise CollectorError(
            f"la ficha no trae la tabla de características; título recibido: {titulo!r}"
        )
    return campos


# -- Fechas y años ------------------------------------------------------------

_FECHA_DMA = re.compile(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$")


def parse_fecha(valor: str | None) -> date | None:
    """`2026-09-22` (lo que publica GBV) o `22-09-2026`. None si no se entiende."""
    texto = (valor or "").strip()
    if not texto:
        return None
    try:
        return date.fromisoformat(texto[:10])
    except ValueError:
        pass
    coincidencia = _FECHA_DMA.match(texto)
    if coincidencia:
        dia, mes, anio = (int(parte) for parte in coincidencia.groups())
        try:
            return date(anio, mes, dia)
        except ValueError:
            return None
    return None


def parse_anio(valor: str | None, *, hoy: date) -> int | None:
    """Año del modelo si es plausible: entre 1950 y el año que viene."""
    texto = (valor or "").strip()
    if not re.fullmatch(r"\d{4}", texto):
        return None
    anio = int(texto)
    if 1950 <= anio <= hoy.year + 1:
        return anio
    return None


def inicio_del_dia(fecha: date) -> datetime:
    """Medianoche chilena de ese día. Es un día calendario, no un instante: se
    toma su comienzo para no fabricar una hora que nadie informó."""
    return datetime(fecha.year, fecha.month, fecha.day, tzinfo=CHILE_TZ)


# -- Dónde: comuna sin geocodificar -------------------------------------------

#: Sectores de la V Región que aparecen como lugar sin comuna, y la comuna a la
#: que pertenecen sin ambigüedad. Claves normalizadas (minúsculas, sin tildes).
#:
#: Conservadora a propósito: un sector ambiguo que queda fuera cae en
#: `sin_ubicar`, que el feed igual muestra. Un sector mal asignado, en cambio,
#: le pondría una comuna falsa a un aviso. Por eso NO están "Placilla" suelta
#: (también es comuna de O'Higgins), ni "Forestal", "Miraflores" o "Recreo".
#: La lista salió de los lugares que GBV tenía publicados el 2026-09-23.
SECTORES_V_REGION: tuple[tuple[str, str], ...] = (
    ("renaca", "Viña del Mar"),
    ("gomez carreno", "Viña del Mar"),
    ("achupallas", "Viña del Mar"),
    ("santa julia", "Viña del Mar"),
    ("miraflores alto", "Viña del Mar"),
    ("nueva aurora", "Viña del Mar"),
    ("forestal alto", "Viña del Mar"),
    ("vina", "Viña del Mar"),
    ("con con", "Concón"),
    ("curauma", "Valparaíso"),
    ("playa ancha", "Valparaíso"),
    ("placilla de penuelas", "Valparaíso"),
    ("placilla penuelas", "Valparaíso"),
    ("laguna verde", "Valparaíso"),
    ("placeres", "Valparaíso"),
    ("belloto", "Quilpué"),
    ("penablanca", "Villa Alemana"),
    ("llay llay", "Llaillay"),
)

#: Las calles numeradas de Viña del Mar ("15 norte con 3 oriente", "8 Norte",
#: "Calle 7 con 24 norte"): la grilla de Norte/Oriente/Poniente es de Viña y de
#: ningún otro lugar que publique GBV. Se excluye "Ruta 5 Norte", que es la
#: Panamericana y cruza todo el país.
_GRILLA_VINA = re.compile(r"(?<!ruta )\b\d{1,2} (norte|oriente|poniente)\b")

#: Menciones de la región que no son una comuna: "Belgrano 1143, Quilpué, V
#: Region de Valparaíso". Se quitan antes de buscar comunas; si no, la comuna
#: Valparaíso le ganaría a Quilpué.
_MENCION_V_REGION = re.compile(
    r"\b(region de valparaiso|v region|quinta region|5ta region|5a region)\b"
)

#: Otra región dicha con todas sus letras: "…, Santo Domingo, las compañías,
#: IV región" es La Serena, no la comuna de Santo Domingo.
_MENCION_OTRA_REGION = re.compile(
    r"\b(?:(?:i|ii|iii|iv|vi|vii|viii|ix|x|xi|xii|xiii|xiv|xv|xvi) region"
    r"|region metropolitana)\b"
)

#: Lugares inequívocamente FUERA de la V Región: comunas de la RM y ciudades de
#: otras regiones que aparecen en GBV. Sólo cuentan cuando CIERRAN un tramo del
#: lugar ("Mall maipu", "..., SANTIAGO"), que es donde una dirección chilena
#: pone la comuna; en medio del texto suelen ser nombres de calle ("Calle
#: Santiago Díaz 338, Rocuant, Valparaíso").
#:
#: Quedan fuera las comunas que también son calles habituales en la V Región
#: —Independencia, Recoleta, Providencia—: marcarlas `otra` escondería avisos
#: locales, y el costo de dejarlas fuera es sólo un `sin_ubicar` más.
LUGARES_OTRAS_REGIONES: frozenset[str] = frozenset(
    {
        # Región Metropolitana
        "santiago",
        "santiago centro",
        "maipu",
        "puente alto",
        "la florida",
        "las condes",
        "nunoa",
        "penalolen",
        "la reina",
        "vitacura",
        "lo barnechea",
        "quilicura",
        "pudahuel",
        "cerrillos",
        "estacion central",
        "san bernardo",
        "la pintana",
        "el bosque",
        "la cisterna",
        "san miguel",
        "san joaquin",
        "macul",
        "renca",
        "conchali",
        "huechuraba",
        "lo prado",
        "cerro navia",
        "quinta normal",
        "lo espejo",
        "pedro aguirre cerda",
        "la granja",
        "san ramon",
        "colina",
        "lampa",
        "buin",
        "talagante",
        "melipilla",
        "penaflor",
        "padre hurtado",
        "calera de tango",
        "pirque",
        "san jose de maipo",
        "tiltil",
        # Otras regiones
        "arica",
        "iquique",
        "alto hospicio",
        "antofagasta",
        "calama",
        "san pedro de atacama",
        "copiapo",
        "vallenar",
        "la serena",
        "coquimbo",
        "ovalle",
        "rancagua",
        "machali",
        "peralillo",
        "san fernando",
        "talca",
        "curico",
        "linares",
        "chillan",
        "concepcion",
        "talcahuano",
        "los angeles",
        "temuco",
        "valdivia",
        "osorno",
        "puerto montt",
        "castro",
        "chiloe",
        "coyhaique",
        "punta arenas",
        # Fuera del país
        "bolivia",
        "argentina",
        "peru",
    }
)

_MAX_PALABRAS_LUGAR = max(len(lugar.split()) for lugar in LUGARES_OTRAS_REGIONES)
_TRAMOS = re.compile(r"[,;/()]|\s-\s")

#: Las 36 comunas, como palabra completa y no como subcadena.
_COMUNAS_V: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"\b{re.escape(normalizada)}\b"), nombre)
    for normalizada, nombre in COMUNAS_NORMALIZADAS
)


#: Palabras que, justo antes del nombre de una comuna, lo vuelven nombre de
#: calle: "Los nogales 2908", "Calle Quillota 250", "Av. Valparaíso".
_INTRODUCEN_CALLE = frozenset(
    {"calle", "av", "avenida", "avda", "pasaje", "psje", "pje", "los", "las"}
)
_PALABRA_PREVIA = re.compile(r"([a-z]+)[^a-z]*$")


def _comuna_v(normalizado: str) -> str | None:
    """Comuna de la V Región nombrada en el texto.

    No cuenta una comuna precedida de "calle", "av.", "pasaje" o de un artículo
    que no es parte de su nombre ("Los nogales 2908" es una calle; "Los Andes"
    sí es la comuna, porque el artículo está en el patrón).

    Si nombra varias, la ÚLTIMA que no sea Valparaíso: una dirección chilena
    termina en "…, comuna, región", y "Valparaíso" al final suele ser la
    región ("Túnel del Cristo Redentor, Los Andes, Valparaíso"; "Acceso al
    puerto de San Antonio, Valparaíso"). Valparaíso gana sólo si está sola.
    """
    menciones: list[tuple[int, str]] = []
    for patron, nombre in _COMUNAS_V:
        for coincidencia in patron.finditer(normalizado):
            previa = _PALABRA_PREVIA.search(normalizado[: coincidencia.start()])
            if previa and previa.group(1) in _INTRODUCEN_CALLE:
                continue
            menciones.append((coincidencia.start(), nombre))
    if not menciones:
        return None
    menciones.sort()
    otras = [nombre for _, nombre in menciones if nombre != "Valparaíso"]
    return otras[-1] if otras else menciones[-1][1]


def _sector_en_texto(normalizado: str) -> str | None:
    for sector, comuna in SECTORES_V_REGION:
        if re.search(rf"\b{re.escape(sector)}\b", normalizado):
            return comuna
    if _GRILLA_VINA.search(normalizado):
        return "Viña del Mar"
    return None


def _otra_region(normalizado: str) -> bool:
    for tramo in _TRAMOS.split(normalizado):
        palabras = re.findall(r"[a-z0-9]+", tramo)
        for largo in range(1, min(_MAX_PALABRAS_LUGAR, len(palabras)) + 1):
            if " ".join(palabras[-largo:]) in LUGARES_OTRAS_REGIONES:
                return True
    return False


def ubicar(textos: Iterable[str | None]) -> tuple[VehicleLocation, str | None]:
    """¿Es de la V Región, de otra, o no se sabe? Sin Nominatim ni LLM.

    `textos` va en orden de prioridad: para un recuperado, primero dónde se
    recuperó y después dónde se lo robaron.

    Tres pasadas, en este orden:

    1. **Otra región dicha con todas sus letras** ("IV región", "Región
       Metropolitana") → `otra`. Es la única señal que le gana a una comuna de
       acá, porque desmiente explícitamente la coincidencia de nombre.
    2. **Comuna o sector de la V Región** → `v_region`. Gana sobre lo que
       sigue: si un texto nombra Viña del Mar y otro tramo dice Santiago, el
       aviso es local. Equivocarse en esa dirección muestra un aviso de más; en
       la contraria escondería uno de acá.
    3. **Lugar inequívoco de otra región cerrando un tramo** → `otra`.

    Probado contra los ~290 lugares que GBV tenía publicados el 2026-09-23.
    """
    candidatos = [normalise_text(texto) for texto in textos if texto and texto.strip()]

    for normalizado in candidatos:
        otra = _MENCION_OTRA_REGION.search(normalizado)
        if otra and not _MENCION_V_REGION.search(normalizado):
            return (VehicleLocation.OTRA, None)

    for normalizado in candidatos:
        region_v = _MENCION_V_REGION.search(normalizado) is not None
        sin_region = _MENCION_V_REGION.sub(" ", normalizado)
        comuna = _comuna_v(sin_region) or _sector_en_texto(sin_region)
        if comuna:
            return (VehicleLocation.V_REGION, comuna)
        if region_v:
            return (VehicleLocation.V_REGION, None)

    for normalizado in candidatos:
        if _otra_region(normalizado):
            return (VehicleLocation.OTRA, None)
    return (VehicleLocation.SIN_UBICAR, None)


# -- Consolidación ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Vehiculo:
    """Lo que se sabe de un aviso, reuniendo tarjeta y detalle."""

    estado: VehicleStatus
    seccion: str
    id_gbv: int
    url: str
    patente: str | None
    patente_publicada: str | None
    tipo_vehiculo: str | None
    marca: str | None
    modelo: str | None
    color: str | None
    anio: int | None
    delito: str | None
    lugar: str | None
    recuperado_en: str | None
    autoridad: str | None
    tiempo_abandono: str | None
    fecha_delito: date | None
    #: Se leyó (o se quiso leer) la ficha de detalle y no sólo la tarjeta.
    con_detalle: bool
    #: La fecha publicada era posterior a hoy y se descartó.
    fecha_futura_descartada: bool = False


def consolidar(ficha: FichaListado, detalle: Mapping[str, str] | None, *, hoy: date) -> Vehiculo:
    """Tarjeta + detalle → vehículo. El detalle manda cuando trae el dato."""
    campos = dict(detalle or {})

    def campo(*claves: str) -> str | None:
        for clave in claves:
            valor = _limpio(campos.get(clave))
            if valor and normalise_text(valor) not in {"sin patente", "sin informacion", "-"}:
                return valor
        return None

    patente = ficha.patente
    publicada = ficha.patente_publicada
    if ficha.estado is VehicleStatus.ABANDONADO:
        # En abandonados la patente sólo está en el detalle, y a veces ni ahí.
        publicada = campo("patente")
        patente = patente_valida(publicada)

    fecha = parse_fecha(campo("fecha de delito"))
    futura = fecha is not None and fecha > hoy
    if futura:
        fecha = None

    return Vehiculo(
        estado=ficha.estado,
        seccion=ficha.seccion,
        id_gbv=ficha.id_gbv,
        url=ficha.url,
        patente=patente,
        patente_publicada=publicada,
        tipo_vehiculo=campo("vehiculo") or ficha.tipo_vehiculo,
        marca=campo("marca") or ficha.marca,
        modelo=campo("modelo") or ficha.modelo,
        color=campo("color"),
        anio=parse_anio(campos.get("ano"), hoy=hoy),
        delito=campo("delito")
        or (ficha.delito_enlace.capitalize() if ficha.delito_enlace else None),
        lugar=campo("lugar de delito", "ubicacion") or ficha.lugar,
        recuperado_en=campo("recuperado en"),
        autoridad=campo("autoridad presente"),
        tiempo_abandono=campo("tiempo abandono") or ficha.tiempo_abandono,
        fecha_delito=fecha,
        con_detalle=detalle is not None,
        fecha_futura_descartada=futura,
    )


_TITULAR: dict[VehicleStatus, str] = {
    VehicleStatus.ROBADO: "Vehículo robado",
    VehicleStatus.RECUPERADO: "Vehículo recuperado",
    VehicleStatus.ABANDONADO: "Vehículo abandonado",
}


def build_text(vehiculo: Vehiculo) -> str:
    """Una línea legible. Es el `text` del evento: lo que se busca por texto
    completo y lo que un operador lee en `/events` sin abrir `raw_data`."""
    descripcion = " ".join(
        parte
        for parte in (
            vehiculo.tipo_vehiculo,
            vehiculo.marca,
            vehiculo.modelo,
            vehiculo.color.lower() if vehiculo.color else None,
            str(vehiculo.anio) if vehiculo.anio else None,
        )
        if parte
    )
    partes = [f"{_TITULAR[vehiculo.estado]}: {descripcion or 'sin descripción'}"]
    partes.append(f"patente {vehiculo.patente}" if vehiculo.patente else "sin patente informada")
    texto = ", ".join(partes) + "."

    if vehiculo.estado is VehicleStatus.RECUPERADO and vehiculo.recuperado_en:
        texto += f" Recuperado en {vehiculo.recuperado_en}"
        texto += f" ({vehiculo.autoridad})." if vehiculo.autoridad else "."
    if vehiculo.lugar:
        abandonado = vehiculo.estado is VehicleStatus.ABANDONADO
        etiqueta = "Ubicación" if abandonado else "Lugar del delito"
        texto += f" {etiqueta}: {vehiculo.lugar}."
    if vehiculo.delito and vehiculo.estado is not VehicleStatus.ABANDONADO:
        texto += f" Delito: {vehiculo.delito}"
        texto += f" ({vehiculo.fecha_delito.isoformat()})." if vehiculo.fecha_delito else "."
    if vehiculo.tiempo_abandono:
        texto += f" Abandonado hace {vehiculo.tiempo_abandono}."
    return texto + " Fuente: GBV."
