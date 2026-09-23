"""Esval: lectura de la API de cortes y del KML de zonas del visor oficial.

Funciones puras, sin red ni base de datos. El collector (`esval_worker`) las
compone; los tests las ejercitan con capturas reales.

Dos fuentes, porque ninguna alcanza sola
----------------------------------------
**La API de la Oficina Virtual** (`ov.esval.cl/api-ov/api/EstadoDeServicio/
CortesActivos`) es la que describe el corte: fechas, calles, motivo, tipo y el
número `sisda`, que es el folio con el que Esval lo gestiona. Forma observada el
2026-09-23:

    {"data": [{
        "localidad": "06", "localidadNombre": "Viña del mar",
        "localidadNombreNormalizado": "VINA DEL MAR",
        "sector": null, "callesAfectadas": "LOS PENSAMIENTOS",
        "fechaInicio": "23-09-2026", "fechaFin": "23-09-2026",
        "horaInicio": "11:00", "horaTermino": "17:00",
        "motivoCorte": "VIDA UTIL VENCIDA", "sisda": "2916567",
        "estaGeoeferenciado": true, "tipoCorte": "Corte emergencia",
        "urlMapa": "http://tupuntodeagua.esval.cl/?sisda=2916567",
        "otros": null, "empresaId": 1, "_id": 9439}]}

Lo que **no** trae es una coordenada: ni `lat`, ni `latitud`, ni `coordenadas`.
Un parser que buscara esas claves descartaría todos los cortes y culparía a un
"cambio de esquema" que no ocurrió. `estaGeoeferenciado` (con la errata de la
fuente) sólo avisa de que la geometría existe en otro lado.

**El KML del visor** (`tupuntodeagua.esval.cl/script/generaKmlZonasCorte.aspx`)
es ese otro lado. Trae un `<Placemark>` por sector afectado —un corte puede
abarcar varios— con el punto del corte (`lat_wf`/`lon_wf`), el polígono del
sector, la comuna correcta y una ficha HTML en `tooltip_wf`. Se une con la API
por `num_sisda_wf` == `sisda`.

Trampas verificadas
-------------------
* **La cabecera `XCodempresa` no filtra.** La API devuelve también los cortes de
  Aguas del Valle (`empresaId: 2`, IV Región). El filtro va en el collector.
* **"Activos" incluye los que todavía no empiezan.** Un corte programado aparece
  días antes, y el KML lo marca "EN CURSO" igual. El collector los deja fuera
  hasta su hora de inicio.
* **`localidadNombre` no siempre es la comuna.** El corte 2914979 dice
  "Rinconada" y es Rinconada de Silva, en **Putaendo**; Rinconada también es
  una comuna, a unos 19 km. Por eso la comuna sale primero del KML.
* **`horaTermino` puede cruzar la medianoche** (15:00 → 02:00). `fechaFin` lo
  resuelve casi siempre; si igual queda `fin < inicio`, se suma un día.
* **Las horas son de pared chilena**, sin zona. Se anclan en
  `America/Santiago` con `zoneinfo`, nunca con un desfase fijo.
* **`callesAfectadas` viene cortado a 255 caracteres.** Se guarda tal cual.
* Las etiquetas del KML son propias (`<lat_wf>` va directo bajo `<Placemark>`,
  no en `<ExtendedData>`), así que `power/kmz_parser` no sirve tal cual.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from html import unescape
from typing import Any
from xml.etree import ElementTree as ET

from app.collectors.geoservices import as_float, normalise_text
from app.collectors.lugares import comuna_por_nombre
from app.collectors.power.outage_parser import CHILE_TZ

#: Nombre de la empresa en los metadatos y en los mensajes.
COMPANY = "esval"

#: Prefijo del `external_id`. El sufijo es el `sisda` del corte.
EXTERNAL_ID_PREFIX = "esval:corte:"

#: Clave namespaced de `raw_data` con el detalle del corte.
ESVAL_KEY = "_esval"

#: Decimales de los vértices que se guardan: ~1 m. El KML trae 14, que es
#: precisión de nanómetros sobre un polígono dibujado a mano.
DECIMALES_ANILLO = 5

# --- API: alias por campo ----------------------------------------------------
#
# El esquema de hoy está verificado, pero la API es interna de un frontend y
# puede cambiar sin aviso. Los alias cubren variantes razonables —camelCase,
# snake_case, nombres genéricos— sin fingir que se conoce un contrato que nadie
# publicó. El primero de cada tupla es el nombre observado.

_SISDA_KEYS: tuple[str, ...] = ("sisda", "numSisda", "num_sisda", "sisdaId")
_ID_KEYS: tuple[str, ...] = ("_id", "id", "idCorte")
_EMPRESA_KEYS: tuple[str, ...] = ("empresaId", "empresa_id", "codEmpresa", "idEmpresa")
_LOCALIDAD_KEYS: tuple[str, ...] = (
    "localidadNombre",
    "localidad_nombre",
    "nombreLocalidad",
    "comuna",
    "nombreComuna",
)
_LOCALIDAD_NORMALIZADA_KEYS: tuple[str, ...] = ("localidadNombreNormalizado",)
_SECTOR_KEYS: tuple[str, ...] = ("sector",)
_CALLES_KEYS: tuple[str, ...] = ("callesAfectadas", "calles_afectadas", "calles", "direccion")
_FECHA_INICIO_KEYS: tuple[str, ...] = ("fechaInicio", "fecha_inicio", "inicio")
_HORA_INICIO_KEYS: tuple[str, ...] = ("horaInicio", "hora_inicio")
_FECHA_FIN_KEYS: tuple[str, ...] = ("fechaFin", "fecha_fin", "fechaTermino", "fin")
_HORA_FIN_KEYS: tuple[str, ...] = ("horaTermino", "hora_termino", "horaFin", "hora_fin")
_TIPO_KEYS: tuple[str, ...] = ("tipoCorte", "tipo_corte", "tipo")
_MOTIVO_KEYS: tuple[str, ...] = ("motivoCorte", "motivo_corte", "motivo")
_OTROS_KEYS: tuple[str, ...] = ("otros", "observaciones")
_URL_KEYS: tuple[str, ...] = ("urlMapa", "url_mapa")
#: Con la errata de la fuente primero, y la forma correcta por si la arreglan.
_GEO_KEYS: tuple[str, ...] = ("estaGeoeferenciado", "estaGeoreferenciado", "georreferenciado")

#: Coordenadas propias de la API. Hoy **no existen**; se buscan por si algún día
#: aparecen, y entonces sirven de respaldo cuando el KML no tenga el corte.
_LAT_KEYS: tuple[str, ...] = ("lat", "latitud", "latitude")
_LON_KEYS: tuple[str, ...] = ("lon", "lng", "longitud", "longitude")
_COORDS_KEYS: tuple[str, ...] = ("coordenadas", "coordinates", "coords", "ubicacion")

#: Claves que el esquema observado trae en **todos** los registros. Si alguna
#: falta en todos a la vez, el esquema cambió y hay que mirarlo: el collector lo
#: avisa con el primer registro en el log. Una sola ausencia aislada es un
#: registro incompleto, no un cambio de contrato.
CLAVES_ESPERADAS: tuple[str, ...] = (
    "sisda",
    "empresaId",
    "localidadNombre",
    "fechaInicio",
    "horaInicio",
    "fechaFin",
    "horaTermino",
    "tipoCorte",
)

#: Chile continental con holgura. No es un filtro de negocio: detecta que se
#: leyó el campo equivocado o que lat y lon vienen al revés.
_LAT_CHILE = (-60.0, -15.0)
_LON_CHILE = (-115.0, -65.0)


def _clave(nombre: Any) -> str:
    return str(nombre).lower().replace("_", "").replace("-", "").replace(" ", "")


def _primero(registro: Mapping[str, Any], claves: Sequence[str]) -> Any:
    """Primer alias presente y no vacío, sin distinguir mayúsculas ni `_`.

    Normalizar una vez por registro evita multiplicar los alias por cada forma
    de escribir lo mismo (`callesAfectadas`, `calles_afectadas`, `CALLES`).
    """
    normalizado = {_clave(k): v for k, v in registro.items()}
    for clave in claves:
        valor = normalizado.get(_clave(clave))
        if valor is None:
            continue
        if isinstance(valor, str) and valor.strip().lower() in ("", "null", "none"):
            continue
        return valor
    return None


def _texto(valor: Any) -> str | None:
    """Texto limpio de espacios, o None."""
    if valor is None:
        return None
    limpio = " ".join(str(valor).split())
    return limpio or None


def _entero(valor: Any) -> int | None:
    numero = as_float(valor)
    if numero is None or numero != int(numero):
        return None
    return int(numero)


def _booleano(valor: Any) -> bool | None:
    if isinstance(valor, bool):
        return valor
    if valor is None:
        return None
    texto = normalise_text(valor)
    if texto in ("true", "si", "1", "s", "yes"):
        return True
    if texto in ("false", "no", "0", "n"):
        return False
    return None


def _en_chile(lat: float, lon: float) -> bool:
    return _LAT_CHILE[0] <= lat <= _LAT_CHILE[1] and _LON_CHILE[0] <= lon <= _LON_CHILE[1]


def _par_lat_lon(a: float | None, b: float | None) -> tuple[float, float] | None:
    """Dos números → (lat, lon), en el orden que sea.

    En Chile los rangos de latitud y longitud no se solapan, así que el orden se
    puede deducir en vez de adivinarlo: invertido, un punto de Valparaíso cae en
    el océano Índico sin que nada falle.
    """
    if a is None or b is None:
        return None
    if _en_chile(a, b):
        return (a, b)
    if _en_chile(b, a):
        return (b, a)
    return None


# --- Fechas ------------------------------------------------------------------

_FECHA_DMY = re.compile(
    r"^\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?:[T ]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*$"
)
_FECHA_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}))?)?")
_HORA = re.compile(r"^\s*(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?\s*(?:h|hrs?\.?)?\s*$", re.IGNORECASE)

#: Hora que se asume cuando la fuente da la fecha sin hora. El inicio, al
#: principio del día; el fin, al final: la ventana nunca se achica por un dato
#: que falta.
INICIO_POR_DEFECTO = time(0, 0)
FIN_POR_DEFECTO = time(23, 59)


def _fecha_y_hora_embebida(valor: Any) -> tuple[date, time | None] | None:
    """`23-09-2026`, `23-09-2026 11:00`, `2026-09-23` o `2026-09-23T11:00` → (fecha, hora?)."""
    if valor is None:
        return None
    texto = str(valor)
    coincide = _FECHA_DMY.match(texto)
    try:
        if coincide:
            dia, mes, anio = int(coincide[1]), int(coincide[2]), int(coincide[3])
            hora = None
            if coincide[4] is not None:
                hora = time(int(coincide[4]), int(coincide[5]), int(coincide[6] or 0))
            return (date(anio, mes, dia), hora)
        coincide = _FECHA_ISO.match(texto)
        if coincide:
            anio, mes, dia = int(coincide[1]), int(coincide[2]), int(coincide[3])
            hora = None
            if coincide[4] is not None:
                hora = time(int(coincide[4]), int(coincide[5]), int(coincide[6] or 0))
            return (date(anio, mes, dia), hora)
    except ValueError:  # 31-02-2026 y compañía
        return None
    return None


def _hora(valor: Any) -> time | None:
    if valor is None:
        return None
    coincide = _HORA.match(str(valor))
    if not coincide:
        return None
    horas, minutos = int(coincide[1]), int(coincide[2])
    segundos = int(coincide[3] or 0)
    if horas == 24 and minutos == 0 and segundos == 0:
        # "24:00" existe en los avisos chilenos y significa fin del día.
        return time(23, 59, 59)
    try:
        return time(horas, minutos, segundos)
    except ValueError:
        return None


def fecha_hora_local(fecha: Any, hora: Any, *, por_defecto: time) -> datetime | None:
    """Fecha y hora de pared chilena → `datetime` en UTC.

    Se ancla con `zoneinfo` y no con un desfase fijo: Chile cambia la hora dos
    veces al año y un `-3` escrito a mano acierta la mitad del tiempo. Es el
    mismo criterio que `outage_parser._as_local_datetime`, con la diferencia de
    que acá la fuente separa fecha y hora en dos campos.
    """
    partes = _fecha_y_hora_embebida(fecha)
    if partes is None:
        return None
    dia, hora_embebida = partes
    reloj = _hora(hora) or hora_embebida or por_defecto
    return datetime.combine(dia, reloj, tzinfo=CHILE_TZ).astimezone(UTC)


def ventana(
    inicio: datetime | None, fin: datetime | None
) -> tuple[datetime | None, datetime | None]:
    """Corrige un fin que cae antes del inicio.

    El caso real es un corte de 15:00 a 02:00 publicado con la misma fecha en
    los dos extremos. Se le suma un día; si ni así cuadra, el fin es basura y se
    descarta en vez de guardar una ventana negativa.
    """
    if inicio is None or fin is None or fin >= inicio:
        return inicio, fin
    corregido = fin + timedelta(days=1)
    return (inicio, corregido) if corregido >= inicio else (inicio, None)


# --- API: registros ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CorteApi:
    """Un registro de `CortesActivos`, con los tipos ya resueltos."""

    sisda: str | None
    registro_id: str | None
    empresa_id: int | None
    #: `localidadNombre` tal cual. **No** es necesariamente la comuna.
    localidad: str | None
    localidad_normalizada: str | None
    sector: str | None
    calles: str | None
    tipo: str | None
    motivo: str | None
    otros: str | None
    url_mapa: str | None
    georreferenciado: bool | None
    #: En UTC. Estimados por la propia empresa.
    inicio: datetime | None
    fin: datetime | None
    #: Coordenadas propias de la API. Hoy siempre None; ver `_COORDS_KEYS`.
    lat: float | None
    lon: float | None
    raw: Mapping[str, Any]


def _coordenadas_api(registro: Mapping[str, Any]) -> tuple[float, float] | None:
    """Busca coordenadas en el registro de la API, en cualquier forma plausible.

    Es la heurística que se pidió para un esquema desconocido. Con el esquema
    verificado no encuentra nada —la API no las trae—, pero cuesta poco y deja
    lista la lectura si Esval las agrega: `lat`/`lon` sueltos, un par
    `"lat,lon"`, una lista `[lat, lon]` o un objeto `{lat, lng}`.
    """
    par = _par_lat_lon(
        as_float(_primero(registro, _LAT_KEYS)), as_float(_primero(registro, _LON_KEYS))
    )
    if par is not None:
        return par

    crudo = _primero(registro, _COORDS_KEYS)
    if isinstance(crudo, Mapping):
        return _par_lat_lon(
            as_float(_primero(crudo, _LAT_KEYS)), as_float(_primero(crudo, _LON_KEYS))
        )
    if isinstance(crudo, str):
        crudo = [pieza for pieza in re.split(r"[,;\s]+", crudo.strip()) if pieza]
    if isinstance(crudo, list | tuple) and len(crudo) >= 2:
        return _par_lat_lon(as_float(crudo[0]), as_float(crudo[1]))
    return None


def parse_corte(registro: Any) -> CorteApi | None:
    """Un registro de la API → `CorteApi`. None si no se entiende nada de él.

    Todo se lee con alias y toda ausencia da None, nunca una excepción: un corte
    sin motivo o sin hora de término es un dato incompleto y normal. Sólo se
    descarta el registro que no trae **nada** que lo identifique: ni folio, ni
    id, ni localidad, ni calles.
    """
    if not isinstance(registro, Mapping):
        return None

    sisda = _texto(_primero(registro, _SISDA_KEYS))
    registro_id = _texto(_primero(registro, _ID_KEYS))
    localidad = _texto(_primero(registro, _LOCALIDAD_KEYS))
    calles = _texto(_primero(registro, _CALLES_KEYS))
    if not any((sisda, registro_id, localidad, calles)):
        return None

    inicio, fin = ventana(
        fecha_hora_local(
            _primero(registro, _FECHA_INICIO_KEYS),
            _primero(registro, _HORA_INICIO_KEYS),
            por_defecto=INICIO_POR_DEFECTO,
        ),
        fecha_hora_local(
            _primero(registro, _FECHA_FIN_KEYS),
            _primero(registro, _HORA_FIN_KEYS),
            por_defecto=FIN_POR_DEFECTO,
        ),
    )
    coords = _coordenadas_api(registro)

    return CorteApi(
        sisda=sisda,
        registro_id=registro_id,
        empresa_id=_entero(_primero(registro, _EMPRESA_KEYS)),
        localidad=localidad,
        localidad_normalizada=_texto(_primero(registro, _LOCALIDAD_NORMALIZADA_KEYS)),
        sector=_texto(_primero(registro, _SECTOR_KEYS)),
        calles=calles,
        tipo=_texto(_primero(registro, _TIPO_KEYS)),
        motivo=_texto(_primero(registro, _MOTIVO_KEYS)),
        otros=_texto(_primero(registro, _OTROS_KEYS)),
        url_mapa=_texto(_primero(registro, _URL_KEYS)),
        georreferenciado=_booleano(_primero(registro, _GEO_KEYS)),
        inicio=inicio,
        fin=fin,
        lat=coords[0] if coords else None,
        lon=coords[1] if coords else None,
        raw=dict(registro),
    )


def claves_ausentes(registros: Sequence[Any]) -> list[str]:
    """Claves esperadas que no aparecen en **ningún** registro.

    Es la señal de cambio de esquema: una clave ausente en todos a la vez no es
    un registro incompleto, es un contrato distinto. Se compara sin mayúsculas
    ni `_`, igual que `_primero`.
    """
    presentes: set[str] = set()
    hay_objetos = False
    for registro in registros:
        if isinstance(registro, Mapping):
            hay_objetos = True
            presentes.update(_clave(k) for k in registro)
    if not hay_objetos:
        return list(CLAVES_ESPERADAS) if registros else []
    return [clave for clave in CLAVES_ESPERADAS if _clave(clave) not in presentes]


def muestra_registro(registro: Any, *, limite: int = 2000) -> str:
    """El registro como JSON legible y acotado, para el log.

    Acotado porque va a los logs de Render en cada corrida mientras el esquema
    siga distinto, y un registro con un polígono embebido podría pesar cientos
    de KB.
    """
    try:
        texto = json.dumps(registro, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        texto = repr(registro)
    return texto if len(texto) <= limite else texto[:limite] + "…"


# --- KML de zonas ------------------------------------------------------------


class KmlZonasError(Exception):
    """El KML del visor no se pudo leer. El collector sigue sin coordenadas."""


@dataclass(frozen=True, slots=True)
class SectorAfectado:
    """Un sector de la red dentro de la zona de corte."""

    sector_id: str | None
    #: Anillo exterior en orden GeoJSON: `(lon, lat)`, cerrado.
    anillo: tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class ZonaCorte:
    """Lo que el visor oficial sabe de un corte: dónde, y en qué sectores."""

    sisda: str
    lat: float | None
    lon: float | None
    #: Tal cual la publica el KML, en mayúsculas y sin tildes ("VINA DEL MAR").
    comuna: str | None
    region: str | None
    estado: str | None
    nombre: str | None
    #: La ficha de `tooltip_wf`: donde, categoria, motivo, suministro_alternativo…
    ficha: Mapping[str, str]
    sectores: tuple[SectorAfectado, ...]


def _local(tag: Any) -> str:
    """Nombre de la etiqueta sin namespace. El KML declara el 2.0 de Google."""
    return tag.rpartition("}")[2] if isinstance(tag, str) else ""


_CELDA = re.compile(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_ETIQUETA_HTML = re.compile(r"<[^>]+>")


def _sin_html(fragmento: str) -> str:
    return " ".join(unescape(_ETIQUETA_HTML.sub(" ", unescape(fragmento))).split())


def parse_ficha(tooltip: str | None) -> dict[str, str]:
    """`<tr><th>Donde:</th><td>…</td></tr>…` → `{"donde": "…", …}`.

    Las etiquetas se normalizan —sin tildes, sin `:` ni `(*)`, con `_` entre
    palabras— para que "Categoría:" y "Suministro Alternativo:" lleguen como
    `categoria` y `suministro_alternativo`.
    """
    ficha: dict[str, str] = {}
    for etiqueta, valor in _CELDA.findall(tooltip or ""):
        clave = normalise_text(_sin_html(etiqueta)).replace("(*)", "").strip(" :")
        clave = "_".join(clave.split())
        limpio = _sin_html(valor)
        if clave and limpio:
            ficha.setdefault(clave, limpio)
    return ficha


def _anillo(poligono: ET.Element) -> tuple[tuple[float, float], ...]:
    """Primer anillo exterior del polígono, en `(lon, lat)` y con 5 decimales."""
    nodos = [n for n in poligono.iter() if _local(n.tag) == "outerBoundaryIs"] or [poligono]
    for nodo in nodos:
        for coords in nodo.iter():
            if _local(coords.tag) != "coordinates" or not coords.text:
                continue
            vertices: list[tuple[float, float]] = []
            for tupla in coords.text.split():
                partes = tupla.split(",")
                if len(partes) < 2:
                    continue
                # lon,lat[,alt]: el orden de la especificación KML.
                lon, lat = as_float(partes[0]), as_float(partes[1])
                if lon is None or lat is None or not _en_chile(lat, lon):
                    continue
                vertices.append((round(lon, DECIMALES_ANILLO), round(lat, DECIMALES_ANILLO)))
            if len(vertices) >= 3:
                return tuple(vertices)
    return ()


def _campos(placemark: ET.Element) -> dict[str, str]:
    """Hijos directos del Placemark como `{etiqueta: texto}`. El CDATA ya viene
    resuelto como texto por ElementTree."""
    return {
        _local(hijo.tag).lower(): (hijo.text or "").strip()
        for hijo in placemark
        if _local(hijo.tag)
    }


def _punto(campos: Mapping[str, str]) -> tuple[float, float] | None:
    """El punto del corte; si no está, el del sector."""
    for lat_key, lon_key in (("lat_wf", "lon_wf"), ("lat_sector", "lon_sector")):
        par = _par_lat_lon(as_float(campos.get(lat_key)), as_float(campos.get(lon_key)))
        if par is not None:
            return par
    return None


def parse_zonas_kml(kml: str | bytes) -> dict[str, ZonaCorte]:
    """KML del visor → zonas de corte por `sisda`.

    Los Placemark del mismo corte (uno por sector) se funden en una sola zona
    con varios sectores. Un Placemark sin `num_sisda_wf` no se puede unir con la
    API y se ignora.

    Lanza `KmlZonasError` si el cuerpo no es un KML —el visor es ASP.NET y ante
    un error sirve una página HTML con código 200— o si el XML está roto.
    """
    crudo = kml.encode("utf-8") if isinstance(kml, str) else kml
    cabecera = crudo[:4000].lower()
    if b"<kml" not in cabecera:
        inicio = crudo[:200].decode("utf-8", errors="replace")
        raise KmlZonasError(f"la respuesta no es un KML. Inicio del cuerpo: {inicio!r}")
    try:
        raiz = ET.fromstring(crudo)
    except ET.ParseError as exc:
        raise KmlZonasError(f"KML ilegible: {exc}") from exc

    zonas: dict[str, ZonaCorte] = {}
    for placemark in raiz.iter():
        if _local(placemark.tag) != "Placemark":
            continue
        campos = _campos(placemark)
        sisda = _texto(campos.get("num_sisda_wf"))
        if not sisda:
            continue

        sectores: list[SectorAfectado] = []
        for poligono in placemark.iter():
            if _local(poligono.tag) == "Polygon":
                anillo = _anillo(poligono)
                if anillo:
                    sectores.append(
                        SectorAfectado(sector_id=_texto(campos.get("id_sector")), anillo=anillo)
                    )

        previa = zonas.get(sisda)
        if previa is not None:
            conocidos = {s.sector_id for s in previa.sectores}
            nuevos = tuple(
                s for s in sectores if s.sector_id is None or s.sector_id not in conocidos
            )
            zonas[sisda] = _con_sectores(previa, previa.sectores + nuevos)
            continue

        punto = _punto(campos)
        zonas[sisda] = ZonaCorte(
            sisda=sisda,
            lat=punto[0] if punto else None,
            lon=punto[1] if punto else None,
            comuna=_texto(campos.get("nombre_comuna_wf")) or _texto(campos.get("localidad_wf")),
            region=_texto(campos.get("region_wf")),
            estado=_texto(campos.get("estado_wf")),
            nombre=_texto(campos.get("nombre_wf")),
            ficha=parse_ficha(campos.get("tooltip_wf")),
            sectores=tuple(sectores),
        )
    return zonas


def _con_sectores(zona: ZonaCorte, sectores: tuple[SectorAfectado, ...]) -> ZonaCorte:
    return ZonaCorte(
        sisda=zona.sisda,
        lat=zona.lat,
        lon=zona.lon,
        comuna=zona.comuna,
        region=zona.region,
        estado=zona.estado,
        nombre=zona.nombre,
        ficha=zona.ficha,
        sectores=sectores,
    )


# --- Unión, identidad y texto ------------------------------------------------


@dataclass(frozen=True, slots=True)
class CorteAgua:
    """Un corte de la API con su zona del visor, si la tiene."""

    corte: CorteApi
    zona: ZonaCorte | None

    @property
    def punto(self) -> tuple[float, float] | None:
        """Del KML primero; de la API sólo si algún día trae las suyas."""
        if self.zona is not None and self.zona.lat is not None and self.zona.lon is not None:
            return (self.zona.lat, self.zona.lon)
        if self.corte.lat is not None and self.corte.lon is not None:
            return (self.corte.lat, self.corte.lon)
        return None

    @property
    def origen_del_punto(self) -> str | None:
        if self.zona is not None and self.zona.lat is not None:
            return "kml"
        if self.corte.lat is not None:
            return "api"
        return None


def unir(cortes: Iterable[CorteApi], zonas: Mapping[str, ZonaCorte]) -> list[CorteAgua]:
    """Empareja cada corte con su zona por `sisda`."""
    return [
        CorteAgua(corte=corte, zona=zonas.get(corte.sisda) if corte.sisda else None)
        for corte in cortes
    ]


def comuna_del_corte(corte: CorteAgua) -> str | None:
    """Nombre canónico de la comuna, o None si no es una de la V Región.

    Con zona, manda el KML y **sólo** el KML: es la única de las dos fuentes que
    trae la comuna de verdad, y caer a `localidadNombre` cuando el KML dice otra
    cosa es exactamente el error de Rinconada/Putaendo. Sin zona no queda otra
    que `localidadNombre`, sabiendo que a veces es una localidad y no la comuna.
    """
    if corte.zona is not None and corte.zona.comuna:
        return comuna_por_nombre(corte.zona.comuna)
    return comuna_por_nombre(corte.corte.localidad) or comuna_por_nombre(
        corte.corte.localidad_normalizada
    )


def build_external_id(corte: CorteApi) -> str:
    """`esval:corte:{sisda}`, estable durante toda la vida del corte.

    Sin `sisda`, el `_id` de la API. Sin ninguno de los dos, un hash de campos
    **de la API**: nunca de las coordenadas del KML. Si el KML cayera en una
    corrida y el id dependiera de él, el mismo corte entraría dos veces.
    """
    if corte.sisda:
        return f"{EXTERNAL_ID_PREFIX}{corte.sisda}"
    if corte.registro_id:
        return f"{EXTERNAL_ID_PREFIX}id:{corte.registro_id}"
    inicio = corte.inicio.isoformat() if corte.inicio else "sin-inicio"
    semilla = "|".join((normalise_text(corte.localidad), normalise_text(corte.calles), inicio))
    digest = hashlib.sha256(semilla.encode("utf-8")).hexdigest()[:24]
    return f"{EXTERNAL_ID_PREFIX}h:{digest}"


def tipo_legible(tipo: str | None) -> str | None:
    """ "Corte emergencia" → "emergencia"; "Corte programado" → "programado"."""
    if not tipo:
        return None
    texto = normalise_text(tipo)
    if texto.startswith("corte "):
        texto = texto[len("corte ") :]
    if texto.startswith("de "):
        texto = texto[len("de ") :]
    return texto or None


def es_programado(tipo: str | None) -> bool | None:
    """True si es programado, False si es de emergencia o no programado."""
    texto = normalise_text(tipo)
    if not texto:
        return None
    if "no programado" in texto or "emergencia" in texto:
        return False
    if "programado" in texto:
        return True
    return None


def _suavizar(texto: str) -> str:
    """ "VIDA UTIL VENCIDA" → "Vida util vencida". El resto, tal cual."""
    return texto.capitalize() if texto.isupper() else texto


def _recortar(texto: str, limite: int) -> str:
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip(" ,;") + "…"


def ventana_legible(inicio: datetime | None, fin: datetime | None) -> str | None:
    """ "23-09 11:00 a 17:00", o con las dos fechas si cruza de día. Hora chilena."""
    if inicio is None and fin is None:
        return None
    ini = inicio.astimezone(CHILE_TZ) if inicio else None
    fn = fin.astimezone(CHILE_TZ) if fin else None
    if ini and fn:
        if ini.date() == fn.date():
            return f"{ini:%d-%m %H:%M} a {fn:%H:%M}"
        return f"{ini:%d-%m %H:%M} a {fn:%d-%m %H:%M}"
    if ini:
        return f"desde {ini:%d-%m %H:%M}"
    return f"hasta {fn:%d-%m %H:%M}" if fn else None


def build_text(corte: CorteAgua, comuna: str | None) -> str:
    """Descripción legible. La fuente no entrega una armada."""
    api = corte.corte
    tipo = tipo_legible(api.tipo)
    partes = [f"Corte de agua (Esval, {tipo})" if tipo else "Corte de agua (Esval)"]

    lugar = comuna or api.localidad
    if lugar:
        partes.append(lugar)

    donde = api.calles or api.sector
    if not donde and corte.zona is not None:
        donde = corte.zona.ficha.get("donde")
    if donde:
        partes.append(_recortar(donde, 160))

    horario = ventana_legible(api.inicio, api.fin)
    if horario:
        partes.append(horario)

    if api.motivo:
        partes.append(_suavizar(api.motivo))

    return " — ".join(partes)


def detalle_del_corte(
    corte: CorteAgua, *, comuna: str | None, visto_en: datetime
) -> dict[str, Any]:
    """Lo que va en `raw_data["_esval"]`.

    `visto_en` se reescribe en cada corrida —el upsert pisa `raw_data`— y es lo
    que permite a una capa del mapa distinguir un corte vigente de uno que ya
    salió de la API: `CortesActivos` no avisa cuándo termina un corte, sólo deja
    de listarlo.
    """
    api = corte.corte
    zona = corte.zona
    return {
        "sisda": api.sisda,
        "id_api": api.registro_id,
        "empresa_id": api.empresa_id,
        "tipo": tipo_legible(api.tipo),
        "programado": es_programado(api.tipo),
        "motivo": api.motivo,
        "localidad": api.localidad,
        "comuna": comuna,
        "sector": api.sector,
        "calles": api.calles,
        "otros": api.otros,
        "inicio": api.inicio.isoformat() if api.inicio else None,
        "fin": api.fin.isoformat() if api.fin else None,
        "url_mapa": api.url_mapa,
        "georreferenciado": corte.punto is not None,
        "origen_del_punto": corte.origen_del_punto,
        "visor": (
            {
                "estado": zona.estado,
                "nombre": zona.nombre,
                "comuna": zona.comuna,
                "region": zona.region,
                **dict(zona.ficha),
            }
            if zona is not None
            else None
        ),
        "sectores": (
            [
                {"id": sector.sector_id, "anillo": [list(v) for v in sector.anillo]}
                for sector in zona.sectores
            ]
            if zona is not None
            else []
        ),
        "visto_en": visto_en.isoformat(),
    }


def sin_duplicados(cortes: Sequence[CorteApi]) -> tuple[list[CorteApi], int]:
    """Un corte por `external_id`. Si se repite, gana el de inicio más reciente.

    Dos filas con el mismo `sisda` se fundirían igual en el upsert, pero en un
    orden que nadie controla. Acá se decide a propósito: la ventana más nueva es
    la que describe lo que está pasando.
    """
    minimo = datetime.min.replace(tzinfo=UTC)
    elegidos: dict[str, CorteApi] = {}
    for corte in cortes:
        clave = build_external_id(corte)
        previo = elegidos.get(clave)
        if previo is None or (corte.inicio or minimo) > (previo.inicio or minimo):
            elegidos[clave] = corte
    return list(elegidos.values()), len(cortes) - len(elegidos)


__all__ = [
    "CLAVES_ESPERADAS",
    "COMPANY",
    "ESVAL_KEY",
    "EXTERNAL_ID_PREFIX",
    "CorteAgua",
    "CorteApi",
    "KmlZonasError",
    "SectorAfectado",
    "ZonaCorte",
    "build_external_id",
    "build_text",
    "claves_ausentes",
    "comuna_del_corte",
    "detalle_del_corte",
    "es_programado",
    "fecha_hora_local",
    "muestra_registro",
    "parse_corte",
    "parse_ficha",
    "parse_zonas_kml",
    "sin_duplicados",
    "tipo_legible",
    "unir",
    "ventana",
    "ventana_legible",
]
