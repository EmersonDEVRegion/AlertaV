"""El día que la prensa declara para el hecho, cuando lo declara.

Para qué existe
---------------
Los filtros de frescura de los collectors de texto (`es_reciente` en prensa,
`is_fresh` en X e Instagram) miden la antigüedad **de la publicación**. Una nota
publicada hace una hora pasa, aunque cuente algo de hace cuatro días.

El 2026-09-03, un jueves, @alertanoticiasvalparaiso publicó: "Una mujer resultó
gravemente lesionada luego de ser atropellada … durante la mañana del domingo en
Av. Errázuriz". El post tenía tres horas; el atropello, cuatro días. El mapa lo
mostró como un accidente activo.

Este módulo lee el día del hecho en el texto —"la mañana del domingo", "ayer",
"el pasado 28 de agosto", "hace tres días"— y lo resuelve contra el día de
publicación, en hora de Chile. Es el complemento de `horas.py`, que lee la hora
del día y deja expresamente el día a quien llama.

La regla: descartar sólo lo que se puede probar viejo
------------------------------------------------------
Un día no es un instante. "El domingo" puede ser las 00:05 o las 23:55, así que
se usa la **edad mínima**: el tiempo transcurrido desde el ÚLTIMO momento en que
el hecho pudo ocurrir. Si incluso esa edad supera la ventana, el hecho es viejo;
si no, pasa. "Ayer" leído a la 01:00 puede ser de hace una hora y pasa; leído a
las 10:00 ya no.

Y dos vetos, porque un falso negativo pierde un siniestro y uno positivo sólo
deja un pin de más:

* **Marcas de presente o de continuidad** —"hoy", "esta mañana", "a esta hora",
  "aún", "sigue", "sin control"— anulan el descarte. "El incendio que comenzó el
  domingo sigue sin control" es de hoy.
* **Sólo formas ancladas al hecho.** Un día de la semana suelto puede ser el
  anuncio de otra cosa ("el lunes se reunirá el concejo"), y una fecha suelta
  puede ser contexto ("como el incendio del 2 de febrero de 2024"). Se leen sólo
  la franja ("la mañana del domingo"), el "pasado" ("el pasado sábado"), un
  verbo de ocurrencia ("ocurrió el domingo"), "ayer", "anoche", "anteayer" y
  "hace N días".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.collectors.geoservices import normalise_text

#: Hora de pared de la región. Los días de la semana de una nota son días
#: chilenos: el "domingo" de un post del lunes a las 22:00 en Valparaíso no es
#: el domingo de UTC, donde ya es martes.
CHILE_TZ = ZoneInfo("America/Santiago")

_DIAS_SEMANA: dict[str, int] = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}

_MESES: dict[str, int] = {
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

_NUMEROS: dict[str, int] = {
    "un": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
}

_SEMANA = "(?P<semana>" + "|".join(_DIAS_SEMANA) + ")"
_FECHA = (
    r"(?:(?:" + "|".join(_DIAS_SEMANA) + r")\s+)?"
    r"(?P<dia>\d{1,2})\s+de\s+(?P<mes>" + "|".join(_MESES) + r")"
    r"(?:\s+(?:de|del)\s+(?P<anio>\d{4}))?"
)
#: El día nombrado: de la semana ("domingo") o con fecha ("sábado 29 de agosto").
_DIA = rf"(?:{_FECHA}|{_SEMANA})"

#: Formas ancladas al hecho. Ver el docstring del módulo: sólo éstas.
_ANCLADAS: tuple[re.Pattern[str], ...] = (
    # "durante la mañana del domingo", "la madrugada de este sábado",
    # "la noche del pasado viernes", "la tarde del 31 de agosto".
    re.compile(
        r"\b(?:madrugada|manana|mediodia|tarde|noche|jornada)\s+"
        rf"(?:del|de\s+este|de\s+el)\s+(?:pasado\s+)?{_DIA}\b"
    ),
    # "el pasado domingo", "el pasado 28 de agosto", "el domingo pasado".
    re.compile(rf"\b(?:el|este)\s+pasado\s+{_DIA}\b"),
    re.compile(rf"\bel\s+{_SEMANA}\s+pasado\b"),
    # "ocurrió el domingo", "se registró este sábado 29 de agosto".
    re.compile(
        # "se registró" y no "registr…" a secas: "el Registro Civil atenderá el
        # lunes" no fecha ningún hecho.
        r"\b(?:ocurri\w*|sucedi\w*|se\s+registr\w*|registrad\w*|produj\w*|producid\w*|"
        r"accidentad\w*|lesionad\w*|fallecid\w*|falleci\w*|muri\w*)\b"
        rf"[^.;:]{{0,40}}?\b(?:el|este)\s+(?:pasado\s+)?{_DIA}\b"
    ),
)

_AYER = re.compile(r"\bayer\b")
_ANTEAYER = re.compile(r"\b(?:anteayer|antier|antes de ayer)\b")
_ANOCHE = re.compile(r"\banoche\b")
_HACE_DIAS = re.compile(
    r"\bhace\s+(?P<n>\d{1,2}|" + "|".join(_NUMEROS) + r")\s+(?P<unidad>dias?|semanas?)\b"
)

#: Presente y continuidad. Cualquiera de éstas anula el descarte: el texto dice
#: que algo está pasando AHORA, aunque también nombre un día anterior.
_PRESENTE = re.compile(
    r"\b(?:hoy|esta\s+(?:madrugada|manana|tarde|noche|jornada)|este\s+mediodia|"
    r"a\s+esta\s+hora|en\s+estos\s+momentos|en\s+este\s+momento|minutos\s+atras|"
    r"aun|todavia|sigue|siguen|continua|continuan|se\s+mantiene|se\s+mantienen|"
    r"persiste|persisten|sin\s+control|en\s+desarrollo|en\s+curso)\b"
)

#: "Anoche" se estira hasta la madrugada: un choque a las 02:00 también es de
#: anoche. El último momento posible del hecho es ésta hora de HOY.
_FIN_DE_LA_NOCHE = time(6, 0)


@dataclass(frozen=True, slots=True)
class DiaDeclarado:
    """El día del hecho según el texto, y hasta cuándo pudo ocurrir."""

    dia: date
    #: Último instante (hora de Chile) en que el hecho pudo haber ocurrido. Es
    #: contra esto que se mide la edad mínima.
    hasta: datetime
    #: El fragmento que lo produjo. Para auditar la lectura en `raw_data`.
    fragmento: str


def _local(instante: datetime) -> datetime:
    if instante.tzinfo is None:
        # Los collectors trabajan en UTC; un instante ingenuo es UTC.
        instante = instante.replace(tzinfo=UTC)
    return instante.astimezone(CHILE_TZ)


def _fin_del_dia(dia: date) -> datetime:
    return datetime.combine(dia + timedelta(days=1), time(0, 0), tzinfo=CHILE_TZ)


def _resolver(match: re.Match[str], hoy: date) -> date | None:
    """El día que nombra un match de `_DIA`, relativo a `hoy`. None si es futuro."""
    semana = match.groupdict().get("semana")
    if semana:
        atras = (hoy.weekday() - _DIAS_SEMANA[semana]) % 7
        if atras == 0 and "pasado" in match.group(0):
            atras = 7  # "el pasado jueves" dicho un jueves es el de la semana anterior
        return hoy - timedelta(days=atras)

    dia, mes = int(match.group("dia")), _MESES[match.group("mes")]
    anio = int(match.group("anio")) if match.group("anio") else hoy.year
    try:
        fecha = date(anio, mes, dia)
    except ValueError:
        return None
    if fecha > hoy:
        if match.group("anio"):
            return None  # una fecha futura con año no es el día de un hecho pasado
        # "el pasado 30 de diciembre" leído el 2 de enero es del año anterior.
        try:
            fecha = date(anio - 1, mes, dia)
        except ValueError:
            return None
    return fecha


def parse_dia_declarado(texto: str, *, publicado: datetime) -> DiaDeclarado | None:
    """Día del hecho que el texto declara, resuelto contra `publicado`.

    None si el texto no declara ninguno en una forma anclada. Es el resultado
    más frecuente y es legítimo: casi ninguna nota de un hecho de hoy dice "hoy".
    """
    plano = normalise_text(texto)
    if not plano:
        return None
    hoy = _local(publicado).date()

    candidatos: list[tuple[int, DiaDeclarado]] = []

    for patron in _ANCLADAS:
        for match in patron.finditer(plano):
            dia = _resolver(match, hoy)
            if dia is not None:
                candidatos.append(
                    (match.start(), DiaDeclarado(dia, _fin_del_dia(dia), match.group(0)))
                )

    for match in _ANTEAYER.finditer(plano):
        dia = hoy - timedelta(days=2)
        candidatos.append((match.start(), DiaDeclarado(dia, _fin_del_dia(dia), match.group(0))))

    for match in _AYER.finditer(plano):
        # "antes de ayer" ya lo contó `_ANTEAYER`.
        if plano[max(0, match.start() - 9) : match.start()].endswith("antes de "):
            continue
        dia = hoy - timedelta(days=1)
        candidatos.append((match.start(), DiaDeclarado(dia, _fin_del_dia(dia), match.group(0))))

    for match in _ANOCHE.finditer(plano):
        hasta = datetime.combine(hoy, _FIN_DE_LA_NOCHE, tzinfo=CHILE_TZ)
        candidatos.append(
            (match.start(), DiaDeclarado(hoy - timedelta(days=1), hasta, match.group(0)))
        )

    for match in _HACE_DIAS.finditer(plano):
        crudo = match.group("n")
        cantidad = int(crudo) if crudo.isdigit() else _NUMEROS[crudo]
        if match.group("unidad").startswith("semana"):
            cantidad *= 7
        if cantidad <= 0:
            continue
        dia = hoy - timedelta(days=cantidad)
        candidatos.append((match.start(), DiaDeclarado(dia, _fin_del_dia(dia), match.group(0))))

    if not candidatos:
        return None
    # El primero en el texto: la prensa fecha el hecho al contarlo, y lo que
    # viene después suele ser contexto ("… igual que el pasado lunes").
    candidatos.sort(key=lambda par: par[0])
    return candidatos[0][1]


def habla_del_presente(texto: str) -> bool:
    """¿El texto dice que algo está pasando ahora? Ver `_PRESENTE`."""
    return bool(_PRESENTE.search(normalise_text(texto)))


def hecho_fuera_de_ventana(
    texto: str | None,
    *,
    publicado: datetime | None,
    ahora: datetime,
    max_age_minutes: int,
) -> bool:
    """¿Se puede afirmar que el hecho que cuenta el texto es más viejo que la ventana?

    True sólo cuando el texto fecha el hecho en una forma anclada, no habla del
    presente, y hasta el último momento en que el hecho pudo ocurrir quedó más
    atrás que `max_age_minutes`. Ante cualquier duda, False: ver el docstring
    del módulo.

    `publicado` es la referencia para leer "el domingo" o "ayer"; sin ella se
    usa `ahora`, que sólo puede hacer al hecho más reciente de lo que es —el
    error que se permite—.
    """
    if not texto:
        return False
    if habla_del_presente(texto):
        return False
    declarado = parse_dia_declarado(texto, publicado=publicado or ahora)
    if declarado is None:
        return False
    edad_minima = _local(ahora) - declarado.hasta
    return edad_minima > timedelta(minutes=max_age_minutes)


__all__ = [
    "CHILE_TZ",
    "DiaDeclarado",
    "habla_del_presente",
    "hecho_fuera_de_ventana",
    "parse_dia_declarado",
]
