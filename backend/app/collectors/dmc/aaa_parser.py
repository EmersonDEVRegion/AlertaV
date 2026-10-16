"""Lectura del Sistema de Alerta Meteorológica de la DMC (`datos_AAA.json`).

Qué es el archivo
-----------------
El mapa «Sistema de Alerta Meteorológica» de meteochile.gob.cl
(`archivos.meteochile.gob.cl/portaldmc/AAA/aaa_mapa.php`) se alimenta de un
JSON estático con todo lo vigente del país:

    {"Avisos": 12, "Alertas": 0, "Alarmas": 0,
     "fecha_actualizacion": "2026-10-16 18:10",
     "AAA": [{"id": "5852", "tipo": "Aviso", "codigoMeteo": "A561/2026", ...}]}

No está documentado. Lo que sigue es lo verificado contra el archivo real del
16-10-2026 (ver `tests/test_dmc_avisos.py`):

* `tipo` es `Aviso`, `Alerta` o `Alarma`: severidad moderada, fuerte e intensa.
* `codigoMeteo` lleva un sufijo `-N` cuando es una actualización
  (`A552-1/2026`). La DMC retira la versión anterior del archivo, así que
  cada `id` es una emisión distinta y no hay que fundir nada.
* Los textos vienen con entidades HTML (`El&eacute;ctricas`).
* `desde` y `hasta` son texto: «la tarde del lunes 19 de octubre del 2026».
  La DMC no publica horas, sólo el tramo del día.
* `tipoZonaAfecta` dice cómo leer `dataZonaAfecta`: `regiones` trae códigos
  de región (`04,05,05m`) y `zonas` trae zonas de pronóstico
  (`05_Litoral,05_Cordillera_Costa`). Son los `indice` de los GeoJSON del
  mismo directorio.
* **`05m` es la Región Metropolitana**, no una parte de Valparaíso. Nada acá
  compara por prefijo de texto.
* `tablaHTML` es una tabla por región con los valores estimados (km/h, mm, °C).
  Se lee sólo la sección de Valparaíso; el HTML no viaja a la PWA.

Funciones puras: sin red ni base.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from html.parser import HTMLParser
from typing import Any
from zoneinfo import ZoneInfo

CHILE_TZ = ZoneInfo("America/Santiago")

#: Bloque propio dentro de `raw_data`, como `_esval` en los cortes de agua.
DMC_KEY = "_dmc"
EXTERNAL_ID_PREFIX = "dmc:aaa:"

#: La región en los códigos de la DMC. Juan Fernández (`jf`) e Isla de Pascua
#: (`ip`) son de la región, pero quedan fuera por decisión del proyecto.
REGION = "05"

TIPOS: tuple[str, ...] = ("Aviso", "Alerta", "Alarma")
SEVERIDAD: dict[str, int] = {"Aviso": 1, "Alerta": 2, "Alarma": 3}

#: Claves que el archivo trae siempre. Si faltan en todos los registros, el
#: formato cambió.
CLAVES_ESPERADAS: tuple[str, ...] = (
    "id",
    "tipo",
    "codigoMeteo",
    "emision",
    "fenomeno",
    "desde",
    "hasta",
    "tipoZonaAfecta",
    "dataZonaAfecta",
    "titulo",
)

#: Nombre legible de cada zona de pronóstico de la región. La DMC escribe
#: `Cordillera_Costa`; en su tabla, «Precordillera y valles precordilleranos».
NOMBRE_ZONA: dict[str, str] = {
    "Litoral": "Litoral",
    "Cordillera_Costa": "Cordillera de la Costa",
    "Precordillera": "Precordillera",
    "Cordillera": "Cordillera",
    "Valle_Longitudinal": "Valle",
}

#: Zonas de pronóstico de cada región en `coordenadas_zonas_DMC_v3.geojson`.
ZONAS_DE_LA_REGION: dict[str, frozenset[str]] = {
    "05": frozenset({"Litoral", "Cordillera_Costa", "Precordillera", "Cordillera"}),
}

#: Orden de costa a cordillera, para listar las zonas siempre igual.
ORDEN_ZONA: tuple[str, ...] = (
    "Litoral",
    "Cordillera_Costa",
    "Valle_Longitudinal",
    "Precordillera",
    "Cordillera",
)

#: Tramos del día en que la DMC expresa la vigencia. No los publica: son la
#: lectura convencional de cada palabra, y se eligen anchos a propósito. Un
#: aviso que se muestra una hora de más no daña; uno que se esconde antes, sí.
#: «La noche del martes» termina el miércoles a las 06:00.
TRAMOS: dict[str, tuple[time, int, time]] = {
    # tramo: (inicio, días que agrega el fin, fin)
    "madrugada": (time(0, 0), 0, time(6, 0)),
    "manana": (time(6, 0), 0, time(12, 0)),
    "mediodia": (time(11, 0), 0, time(15, 0)),
    "tarde": (time(12, 0), 0, time(20, 0)),
    "noche": (time(20, 0), 1, time(6, 0)),
}

MESES: dict[str, int] = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

_DIA_MES_ANIO = re.compile(r"(\d{1,2})\s+de\s+([a-z]+)\s+(?:del?\s+)?(\d{4})")
_HORA = re.compile(r"(\d{1,2})[:.](\d{2})")
_ETIQUETA = re.compile(r"<[^>]+>")
_ESPACIOS = re.compile(r"\s+")


def plano(texto: str) -> str:
    """Minúsculas sin tildes ni signos repetidos: para buscar, no para mostrar."""
    descompuesto = unicodedata.normalize("NFKD", texto.lower())
    sin_tildes = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return _ESPACIOS.sub(" ", sin_tildes).strip()


def texto_limpio(valor: Any) -> str | None:
    """Entidades decodificadas, sin etiquetas y con espacios simples."""
    if valor is None:
        return None
    texto = html.unescape(str(valor))
    texto = _ETIQUETA.sub(" ", texto)
    texto = _ESPACIOS.sub(" ", texto).strip()
    return texto or None


# -- Fechas --------------------------------------------------------------------


def parse_emision(texto: Any) -> datetime | None:
    """«Viernes 16 de octubre del 2026 a las 12:10 hrs.» → instante en UTC."""
    limpio = texto_limpio(texto)
    if not limpio:
        return None
    base = plano(limpio)
    fecha = _fecha(base)
    if fecha is None:
        return None
    reloj = _HORA.search(base)
    hora = time(int(reloj.group(1)), int(reloj.group(2))) if reloj else time(0, 0)
    try:
        local = datetime.combine(fecha, hora, tzinfo=CHILE_TZ)
    except ValueError:
        return None
    return local.astimezone(UTC)


def _fecha(base: str) -> date | None:
    encontrada = _DIA_MES_ANIO.search(base)
    if not encontrada:
        return None
    mes = MESES.get(encontrada.group(2))
    if mes is None:
        return None
    try:
        return date(int(encontrada.group(3)), mes, int(encontrada.group(1)))
    except ValueError:
        return None


def _tramo(base: str) -> str | None:
    # «mediodía» antes que «día»; «mañana» sin tilde por `plano`.
    for nombre in ("madrugada", "mediodia", "manana", "tarde", "noche"):
        if re.search(rf"\b{nombre}\b", base):
            return nombre
    return None


@dataclass(frozen=True, slots=True)
class Momento:
    """Un extremo de la vigencia: el texto de la DMC y el instante que se le asigna."""

    texto: str | None
    instante: datetime | None
    tramo: str | None


def parse_vigencia(texto: Any, *, extremo: str) -> Momento:
    """«la tarde del lunes 19 de octubre del 2026» → inicio o fin de ese tramo.

    `extremo` es `desde` o `hasta`. Sin tramo reconocible, el día completo
    (desde las 00:00 o hasta las 24:00): de nuevo, se prefiere ancho.
    """
    limpio = texto_limpio(texto)
    if not limpio:
        return Momento(None, None, None)
    base = plano(limpio)
    fecha = _fecha(base)
    tramo = _tramo(base)
    if fecha is None:
        return Momento(limpio, None, tramo)
    if tramo is None:
        dia = fecha if extremo == "desde" else fecha + timedelta(days=1)
        local = datetime.combine(dia, time(0, 0), tzinfo=CHILE_TZ)
        return Momento(limpio, local.astimezone(UTC), None)
    inicio, dias_extra, fin = TRAMOS[tramo]
    if extremo == "desde":
        local = datetime.combine(fecha, inicio, tzinfo=CHILE_TZ)
    else:
        local = datetime.combine(fecha + timedelta(days=dias_extra), fin, tzinfo=CHILE_TZ)
    return Momento(limpio, local.astimezone(UTC), tramo)


# -- Zonas ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Alcance:
    """Qué parte de la región toca un aviso."""

    #: `region` (toda la región) o `zonas` (algunas zonas de pronóstico).
    tipo: str
    #: Claves de zona de la DMC sin el prefijo de región (`Litoral`), en orden
    #: de costa a cordillera. Vacía si `tipo == "region"`.
    zonas: tuple[str, ...]


def alcance_en_region(tipo_zona: Any, data_zona: Any, *, region: str = REGION) -> Alcance | None:
    """La parte de la región que toca el aviso, o `None` si no la toca.

    Compara códigos completos, nunca prefijos: `05m` (Metropolitana) no es `05`,
    y `05m_Cordillera` no es una zona de Valparaíso.
    """
    codigos = [c.strip() for c in str(data_zona or "").split(",") if c.strip()]
    tipo = plano(str(tipo_zona or ""))
    if tipo.startswith("region"):
        return Alcance("region", ()) if region in codigos else None
    propias: list[str] = []
    for codigo in codigos:
        reg, _, zona = codigo.partition("_")
        if reg == region and zona:
            propias.append(zona)
        elif reg == region and not zona:
            # Un código de región dentro de una lista de zonas: toda la región.
            return Alcance("region", ())
    if not propias:
        return None
    if ZONAS_DE_LA_REGION.get(region, frozenset()) <= set(propias):
        # Las cuatro zonas de la región: decir «toda la región» es más claro.
        return Alcance("region", ())
    orden = {z: i for i, z in enumerate(ORDEN_ZONA)}
    unicas = sorted(set(propias), key=lambda z: (orden.get(z, len(orden)), z))
    return Alcance("zonas", tuple(unicas))


def nombre_zona(zona: str) -> str:
    return NOMBRE_ZONA.get(zona, zona.replace("_", " "))


# -- Tabla de valores estimados ------------------------------------------------


@dataclass(frozen=True, slots=True)
class FilaTabla:
    zona: str
    valores: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Tabla:
    titulo: str | None
    columnas: tuple[str, ...]
    filas: tuple[FilaTabla, ...]


class _Celdas(HTMLParser):
    """Filas de la tabla como listas de (es_encabezado, texto)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.filas: list[list[tuple[bool, str]]] = []
        self._fila: list[tuple[bool, str]] | None = None
        self._celda: list[str] | None = None
        self._es_th = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "tr":
            self._fila = []
        elif tag in ("td", "th") and self._fila is not None:
            self._celda = []
            self._es_th = tag == "th"

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._fila is not None and self._celda is not None:
            self._fila.append((self._es_th, _ESPACIOS.sub(" ", "".join(self._celda)).strip()))
            self._celda = None
        elif tag == "tr" and self._fila is not None:
            self.filas.append(self._fila)
            self._fila = None

    def handle_data(self, data: str) -> None:
        if self._celda is not None:
            self._celda.append(data)


def tabla_de_region(
    tabla_html: Any, titulo: Any = None, *, nombre_region: str = "valparaiso"
) -> Tabla | None:
    """La sección de una región de `tablaHTML`, o `None` si no está o no se entiende."""
    if not tabla_html or not isinstance(tabla_html, str):
        return None
    lector = _Celdas()
    try:
        lector.feed(tabla_html)
        lector.close()
    except Exception:
        return None

    dentro = False
    columnas: tuple[str, ...] = ()
    filas: list[FilaTabla] = []
    for fila in lector.filas:
        if len(fila) == 1 and fila[0][0]:
            encabezado = plano(fila[0][1])
            if encabezado.startswith("region"):
                if dentro:
                    break
                dentro = nombre_region in encabezado
            continue
        if not dentro or not fila:
            continue
        if all(es_th for es_th, _ in fila):
            columnas = tuple(texto for _, texto in fila[1:])
            continue
        zona, *valores = (texto for _, texto in fila)
        if zona:
            filas.append(FilaTabla(zona=zona, valores=tuple(valores)))
    if not filas:
        return None
    return Tabla(titulo=texto_limpio(titulo), columnas=columnas, filas=tuple(filas))


# -- El aviso ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AvisoDmc:
    """Un aviso, alerta o alarma tal como se guarda en `raw_data["_dmc"]`."""

    id: str
    tipo: str
    codigo: str | None
    fenomeno: str | None
    titulo: str | None
    emision: datetime | None
    emision_texto: str | None
    desde: Momento
    hasta: Momento
    observacion: str | None
    condicion_sinoptica: str | None
    alcance: Alcance
    zona_texto: str | None
    icono: str | None
    tabla: Tabla | None
    raw: Mapping[str, Any] = field(repr=False)

    @property
    def severidad(self) -> int:
        return SEVERIDAD.get(self.tipo, 1)


def claves_ausentes(registros: Sequence[Any]) -> list[str]:
    """Claves esperadas que no trae NINGÚN registro: el formato cambió."""
    dicts = [r for r in registros if isinstance(r, Mapping)]
    if not dicts:
        return []
    return [c for c in CLAVES_ESPERADAS if not any(c in r for r in dicts)]


def parse_aviso(registro: Any, *, region: str = REGION) -> AvisoDmc | None:
    """Un registro de `AAA` que toca la región → `AvisoDmc`. `None` si no la toca.

    Lanza `ValueError` si el registro no tiene `id` o su `tipo` no es uno de
    los tres: eso no es un aviso fuera de alcance, es un formato que no se
    entiende.
    """
    if not isinstance(registro, Mapping):
        raise ValueError("el registro no es un objeto")
    ident = texto_limpio(registro.get("id"))
    if not ident:
        raise ValueError("registro sin id")
    tipo = (texto_limpio(registro.get("tipo")) or "").capitalize()
    if tipo not in TIPOS:
        raise ValueError(f"tipo desconocido: {registro.get('tipo')!r}")

    alcance = alcance_en_region(
        registro.get("tipoZonaAfecta"), registro.get("dataZonaAfecta"), region=region
    )
    if alcance is None:
        return None

    return AvisoDmc(
        id=ident,
        tipo=tipo,
        codigo=texto_limpio(registro.get("codigoMeteo")),
        fenomeno=texto_limpio(registro.get("fenomeno")),
        titulo=texto_limpio(registro.get("titulo")),
        emision=parse_emision(registro.get("emision")),
        emision_texto=texto_limpio(registro.get("emision")),
        desde=parse_vigencia(registro.get("desde"), extremo="desde"),
        hasta=parse_vigencia(registro.get("hasta"), extremo="hasta"),
        observacion=texto_limpio(registro.get("observacion")),
        condicion_sinoptica=texto_limpio(registro.get("condicionSinoptica")),
        alcance=alcance,
        zona_texto=texto_limpio(registro.get("textoZonaAfecta")),
        icono=texto_limpio(registro.get("icono")),
        tabla=tabla_de_region(registro.get("tablaHTML"), registro.get("tablaTitulo")),
        raw=registro,
    )


def build_external_id(aviso: AvisoDmc) -> str:
    return f"{EXTERNAL_ID_PREFIX}{aviso.id}"


def build_text(aviso: AvisoDmc) -> str:
    """El texto de la señal: «Aviso DMC A561/2026: Probables Tormentas Eléctricas…»."""
    encabezado = f"{aviso.tipo} DMC {aviso.codigo}" if aviso.codigo else f"{aviso.tipo} DMC"
    return f"{encabezado}: {aviso.titulo or aviso.fenomeno or 'fenómeno meteorológico'}"


def _iso(momento: datetime | None) -> str | None:
    return momento.isoformat() if momento else None


def detalle(aviso: AvisoDmc, *, visto_en: datetime) -> dict[str, Any]:
    """El bloque `_dmc` de `raw_data`: lo que lee `GET /feed/avisos-meteo`."""
    tabla = (
        {
            "titulo": aviso.tabla.titulo,
            "columnas": list(aviso.tabla.columnas),
            "filas": [{"zona": f.zona, "valores": list(f.valores)} for f in aviso.tabla.filas],
        }
        if aviso.tabla
        else None
    )
    return {
        "id": aviso.id,
        "tipo": aviso.tipo,
        "severidad": aviso.severidad,
        "codigo": aviso.codigo,
        "fenomeno": aviso.fenomeno,
        "titulo": aviso.titulo,
        "emision": _iso(aviso.emision),
        "emision_texto": aviso.emision_texto,
        "desde": _iso(aviso.desde.instante),
        "desde_texto": aviso.desde.texto,
        "hasta": _iso(aviso.hasta.instante),
        "hasta_texto": aviso.hasta.texto,
        "observacion": aviso.observacion,
        "condicion_sinoptica": aviso.condicion_sinoptica,
        "alcance": aviso.alcance.tipo,
        "zonas": list(aviso.alcance.zonas),
        "zona_texto": aviso.zona_texto,
        "icono": aviso.icono,
        "tabla": tabla,
        "visto_en": visto_en.isoformat(),
    }


__all__ = [
    "CHILE_TZ",
    "CLAVES_ESPERADAS",
    "DMC_KEY",
    "REGION",
    "SEVERIDAD",
    "TIPOS",
    "Alcance",
    "AvisoDmc",
    "Momento",
    "Tabla",
    "alcance_en_region",
    "build_external_id",
    "build_text",
    "claves_ausentes",
    "detalle",
    "nombre_zona",
    "parse_aviso",
    "parse_emision",
    "parse_vigencia",
    "plano",
    "tabla_de_region",
    "texto_limpio",
]
