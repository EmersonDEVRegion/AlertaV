"""Política táctica multiamenaza: de una serie horaria a un estado de alerta. Módulo puro.

Acá vive la única pregunta interesante de este collector, y desde la v2 son
cuatro preguntas en vez de una. Saber que van a caer 40 mm no dice nada por sí
solo: 40 mm repartidos en dos días son un invierno normal en Valparaíso y 40 mm
en tres horas son quebradas desbordadas y la Ruta 68 cortada. Saber que hará
30 °C tampoco dice nada: 30 °C con 60 % de humedad y calma es un domingo de
enero, y 30 °C con 25 % de humedad y ráfagas de 40 km/h es el escenario en que
un incendio de interfaz se vuelve incontrolable en veinte minutos.

Lo que importa nunca es una variable, es una **combinación en una ventana**.

Las cuatro familias de amenaza
------------------------------

===============  ==============================  ==================================
Familia          Mecanismo físico                 Ventana
===============  ==============================  ==================================
`lluvia`         Anegamiento urbano: el drenaje   24 h
                 se satura en una hora.
`remocion`       Remoción en masa: el suelo se    24 h
                 satura en horas y el cerro
                 cede.
`incendio`       Propagación: calor + sequedad    12 h
                 + viento sobre el combustible.
`viento`         Ráfaga por sí sola: caída de     12 h
                 tendido, suspensión del
                 combate aéreo.
`calor`          Golpe de calor. Riesgo directo   24 h
                 a la salud.
`uv`             Daño por exposición sostenida.   6 h
===============  ==============================  ==================================

**Las ventanas son distintas a propósito y no por comodidad.** Un acumulado de
24 h describe la saturación de un cerro, que es un proceso lento y acumulativo;
un índice UV de 11 describe lo que le pasa a la piel de alguien que está afuera
*ahora*. Anunciar a las 22:00 que mañana a las 14:00 el UV será 11 no es
información táctica, es ruido que apaga el widget para el día en que sí importe.
Por eso la ventana del UV es la más corta del módulo: seis horas es el ancho del
bloque de mediodía, así que el aviso se enciende por la mañana del día peligroso
y se apaga al atardecer.

Dos severidades, y por qué sólo dos
------------------------------------

`aviso` (ámbar) y `critica` (rojo). No hay una tercera. El consumidor de esto es
un widget de 180 px en la barra superior de un teléfono, y la pregunta que ese
widget responde es binaria con un matiz: *¿tengo que hacer algo, y tengo que
hacerlo ya?* Una escala de cinco niveles obligaría a leer una leyenda para
distinguir «moderado» de «considerable», que es exactamente el trabajo que un
tablero táctico no puede pedirle a nadie.

De dónde salen los números, uno por uno
----------------------------------------

Esta es la sección que hay que leer antes de mover cualquier constante.

* **UV — 8 y 11.** Los únicos umbrales del módulo que son un estándar y no una
  hipótesis. Son las bandas «muy alto» (rojo) y «extremo» (morado) del índice UV
  global de la OMS/OMM, idénticas en la escala de la EPA y del ICNIRP. No se
  tocan: moverlos sería inventar una escala nueva con el nombre de una que la
  gente ya reconoce por el color.

* **Incendio — 30/30/30.** Temperatura ≥ 30 °C, humedad relativa ≤ 30 %,
  ráfaga ≥ 30 km/h. Es el «Factor 30-30-30» que CONAF y la prensa chilena usan
  para comunicar riesgo de propagación. **Y hay que decir su límite, porque es
  grande:** no es un índice de peligro validado —no lo respalda un modelo de
  combustible— y la propia CONAF mostró en un ejercicio en Laguna Verde,
  Valparaíso, que con 18 °C, 48 % de humedad y 20 km/h un incendio puede ser
  igual de devastador. En la costa de esta región el 30-30-30 casi nunca se
  cumple y los incendios ocurren igual.

  De ahí el segundo tramo, que es una decisión propia y no un estándar: un
  **aviso costero** en 25 °C / 40 % / 25 km/h. Sigue por encima de las cifras de
  Laguna Verde —bajar hasta ahí dejaría el widget en ámbar todo el verano, que
  es la forma más rápida de que nadie lo mire— pero cubre el régimen en que esta
  región se quema de verdad. El 30-30-30 queda como el tramo **crítico**, que es
  el papel que le corresponde: cuando se cumple, no hay discusión.

  La regla se evalúa **en el mismo paso horario**, nunca sobre máximos
  independientes. Ver `evaluar_incendio`.

* **Calor — 32 °C y 36 °C.** Acá hay una divergencia deliberada de la autoridad
  y conviene que quede escrita. La DMC define ola de calor como *tres días
  consecutivos por encima del percentil 90 diario de la climatología de esa
  estación*. Es la definición correcta y este collector **no puede calcularla**:
  no tiene la serie 1991-2020 por estación, y un pronóstico de 24 h no ve tres
  días. Implementar algo y llamarlo «ola de calor» sería mentir sobre el aval.

  Lo que sí se puede medir es el **riesgo directo a la salud**, que es lo que la
  app necesita, y ese no es un percentil: es un número absoluto. 32 °C es donde
  la DMC empieza a emitir avisos por altas temperaturas para los valles
  interiores de esta región; 36 °C es el techo de los avisos que efectivamente
  emitió en 2026 para Valparaíso. Por eso el vocabulario del módulo dice
  `calor` y nunca `ola de calor`.

  El tercer criterio, `noche_tropical_c`, no dispara solo: **agrava**. La carga
  epidemiológica del calor no la produce el pico de las 15:00 sino la ausencia
  de alivio nocturno; una mínima que no baja de 20 °C significa que el cuerpo no
  recupera, y por eso 32 °C con noche tropical se trata como 36 °C sin ella.

* **Lluvia y remoción — 5/15/40 y 10/25/60.** Los tres primeros son los de la
  v1 y se conservan. Los tres críticos son nuevos y separan lo que antes era un
  único flag. La partición por mecanismo tampoco es cosmética: la **intensidad
  horaria** describe el drenaje urbano saturándose —agua corriendo por la calle,
  un problema de una hora— y el **acumulado en 3 y 24 h** describe el suelo
  perdiendo capacidad de infiltración, que es lo que hace ceder un talud. Son
  dos amenazas distintas con dos respuestas distintas, y el widget tiene que
  poder nombrar la que corresponde.

  Igual que en la v1: **no son umbrales oficiales.** No salen de una norma de la
  DMC ni de SENAPRED —que publican categorías cualitativas, no cortes numéricos
  para inundación urbana— sino de la geografía del caso: cerros con pendiente
  fuerte, quebradas canalizadas y drenaje urbano antiguo, donde el problema
  aparece con intensidades más bajas que en una ciudad plana. La forma correcta
  de fijarlos sigue siendo contrastar esta capa con los avisos de vía cortada de
  Transporte Informa a lo largo de un invierno.

* **Viento — 60 y 80 km/h.** Independientes del 30-30-30 y del incendio, porque
  el mecanismo es otro: a 60 km/h se suspende el combate aéreo y empiezan a caer
  ramas sobre el tendido —que es, literalmente, la capa de cortes de luz de este
  mismo sistema— y a 80 km/h el daño estructural es esperable con o sin fuego.
  Un día de temporal invernal con 70 km/h y 12 °C no cumple ninguna condición de
  incendio y sigue siendo una tarde en que hay que avisar.

Por qué la probabilidad NO filtra
---------------------------------

`precipitation_probability` se publica en la salida —el producto lo pidió— pero
**no participa de la decisión**. Un modelo que anuncia 20 mm/h con 30 % de
probabilidad es justo el caso que una app de emergencias tiene que mostrar; usar
la probabilidad como veto significaría esconder el escenario grave por ser el
menos probable. La certeza es una dimensión aparte, y en este sistema se comunica
aparte: la confianza de la fuente (0.10) y el `nivel` de la señal. Es la misma
separación que `style_for` hace en `models/enums.py` entre color (cuánto sabemos)
e ícono (de qué se trata).

Además hay una razón práctica: no todos los modelos de Open-Meteo publican
probabilidad. Si filtrara, una comuna servida por un modelo sin ese campo
perdería el flag en silencio.

Un `None` nunca dispara
-----------------------

Ninguna regla de este módulo se cumple con un dato ausente. Es la misma decisión
que ya tomó `PuntoHorario.mm` en la v1, extendida a las cinco variables nuevas:
si el modelo no publica humedad relativa, el 30-30-30 **no se evalúa** para esa
comuna en vez de evaluarse con un cero implícito —que sería una humedad del 0 %
y un incendio crítico permanente— o con un cien implícito, que apagaría la
amenaza para siempre sin que nadie se entere. Lo que se hace es no decidir, y
que `SerieComunal` cuente cuántas variables llegaron.

Compatibilidad hacia atrás
--------------------------

`riesgo_inundacion`, `nivel` y `motivos` siguen significando exactamente lo
mismo que en la v1 y siguen calculándose sólo con las tres reglas de lluvia. La
capa de MapLibre, el GeoJSON y sus tests no saben que existe el resto, y no
tienen por qué: el mapa dibuja lluvia y el widget dibuja el estado táctico.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.collectors.weather.comunas import Comuna

#: Hora de pared chilena, sólo para el texto legible. Se usa `zoneinfo` y no un
#: desfase fijo por lo mismo que en `outage_parser` y `csn_parser`: Chile cambia
#: de UTC-4 a UTC-3 en septiembre y ha movido las fechas de cambio varias veces.
CHILE_TZ = ZoneInfo("America/Santiago")

#: Nivel de la señal DE LLUVIA. Es vocabulario de PRESENTACIÓN heredado de la
#: v1: decide el color y el grosor de la capa del mapa. El contrato que el
#: frontend debe leer para decidir *si hay riesgo de inundación* es el booleano
#: `riesgo_inundacion`; para el estado táctico completo, `severidad`.
NIVEL_SECO = "seco"
NIVEL_LLUVIA = "lluvia"
NIVEL_RIESGO = "riesgo"
NIVEL_RIESGO_ALTO = "riesgo_alto"

#: Multiplicador de la intensidad horaria a partir del cual una sola regla ya
#: basta para `riesgo_alto`. 2× el umbral (10 mm/h por defecto) es un chubasco
#: que no necesita corroboración de las otras dos reglas para ser grave.
_FACTOR_ALTO = 2.0


# ===========================================================================
# Vocabulario del estado táctico
# ===========================================================================
#
# Cadenas y no `enum.Enum`, por el mismo motivo que `NIVEL_*`: estos valores
# viajan dentro de un JSONB, salen por la API y terminan comparados en
# TypeScript. Un `Enum` obligaría a un `.value` en cada frontera y no compraría
# nada — el conjunto cerrado se protege con los tests y con el tipo del schema.

#: Nada cruzó un umbral. El widget se queda en su diseño silencioso.
SEVERIDAD_NINGUNA = "ninguna"
#: Ámbar. Hay una condición que merece atención pero no acción inmediata.
SEVERIDAD_AVISO = "aviso"
#: Rojo. Condición peligrosa para las próximas horas.
SEVERIDAD_CRITICA = "critica"

#: Orden total de la severidad. Existe porque «la peor de dos» se pregunta en
#: tres sitios (por amenaza, por comuna, por región) y comparar cadenas por
#: orden alfabético daría `aviso < critica < ninguna`, que es exactamente al
#: revés en el extremo que importa.
ORDEN_SEVERIDAD: dict[str, int] = {
    SEVERIDAD_NINGUNA: 0,
    SEVERIDAD_AVISO: 1,
    SEVERIDAD_CRITICA: 2,
}

#: Discriminador del payload. Una fila meteorológica describe **una comuna** o
#: **la región entera**, y quien la lea tiene que poder distinguirlo sin
#: adivinar por el nombre.
#:
#: Vive acá y no en `region.py` para que `Pronostico.to_dict()` pueda marcarse
#: como comunal sin importar el módulo que consolida la región — que sí importa
#: éste, y el ciclo tumbaría el arranque. Es la misma lección que dejó
#: `ConfidenceLevel` cuando tuvo que bajar a `models/enums`.
AMBITO_KEY = "ambito"
AMBITO_COMUNA = "comuna"
AMBITO_REGION = "region"

#: Anegamiento urbano por intensidad horaria. El drenaje no da abasto.
AMENAZA_LLUVIA = "lluvia"
#: Remoción en masa por saturación del terreno. Acumulado en 3 y 24 h.
AMENAZA_REMOCION = "remocion"
#: Propagación de incendio forestal. Regla 30-30-30 y su tramo costero.
AMENAZA_INCENDIO = "incendio"
#: Ráfaga por sí sola: tendido eléctrico y combate aéreo.
AMENAZA_VIENTO = "viento"
#: Calor con riesgo directo a la salud. NO es «ola de calor» (ver el encabezado).
AMENAZA_CALOR = "calor"
#: Índice UV. Daño por exposición sostenida.
AMENAZA_UV = "uv"

#: Desempate cuando dos amenazas comparten severidad.
#:
#: No es un orden estético: es el orden en que estas cosas matan gente en esta
#: región. Una remoción en masa sobre una toma en un cerro y un incendio de
#: interfaz no se comparan con un índice UV de 11, aunque los tres estén en
#: rojo. El widget expande UNA métrica y ésta es la que elige cuál.
PRIORIDAD_AMENAZA: dict[str, int] = {
    AMENAZA_REMOCION: 6,
    AMENAZA_INCENDIO: 5,
    AMENAZA_LLUVIA: 4,
    AMENAZA_VIENTO: 3,
    AMENAZA_CALOR: 2,
    AMENAZA_UV: 1,
}


def peor_severidad(*valores: str) -> str:
    """La más grave de las severidades dadas. Ignora las desconocidas."""
    return max(
        (valor for valor in valores if valor in ORDEN_SEVERIDAD),
        key=lambda valor: ORDEN_SEVERIDAD[valor],
        default=SEVERIDAD_NINGUNA,
    )


@dataclass(frozen=True, slots=True)
class Disparo:
    """Una regla que se cumplió, con la cifra que la cumplió.

    Es la unidad de explicación de toda la capa. El widget no dice «alerta»: dice
    «38 °C» en grande y «umbral 36 °C» debajo, y para eso necesita el número
    culpable, su unidad y contra qué se comparó. Guardar sólo el booleano
    obligaría a recalcular la explicación en el frontend, que es la duplicación
    de política que este proyecto evita en todas partes.
    """

    amenaza: str
    severidad: str
    #: Clave estable de la métrica (`mm_hora_max`, `temp_max_c`, `uv_max`…).
    #: El frontend la usa para elegir el ícono y el formato; no para decidir.
    metrica: str
    valor: float
    unidad: str
    umbral: float
    #: Frase legible en español, ya redactada. Lo que va al popup y a
    #: `raw_events.text`.
    texto: str
    #: Cuándo ocurre, en UTC. `None` cuando la regla es de acumulado y no de
    #: instante. El widget lo convierte a hora de Chile.
    momento: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "amenaza": self.amenaza,
            "severidad": self.severidad,
            "metrica": self.metrica,
            "valor": self.valor,
            "unidad": self.unidad,
            "umbral": self.umbral,
            "texto": self.texto,
            "momento": self.momento.isoformat() if self.momento else None,
        }


@dataclass(frozen=True, slots=True)
class PuntoHorario:
    """Un paso horario del pronóstico, con las seis variables tácticas.

    **Todas las medidas son `float | None`, y eso no es defensivo: es la
    distinción entre "no va a llover" y "el modelo no entregó el dato".**
    Colapsar `None` a `0.0` acá haría que una respuesta con el campo vacío se
    leyera como una tarde seca —el modo de fallo que este proyecto persigue en
    todas las fuentes— y en las variables nuevas sería peor todavía: una humedad
    ausente leída como 0 % pondría la región entera en 30-30-30 crítico.

    Ver `SerieComunal.datos_validos` y `variables_ausentes`.
    """

    momento: datetime
    mm: float | None
    probabilidad: int | None = None
    #: Temperatura a 2 m, en °C.
    temp_c: float | None = None
    #: Humedad relativa a 2 m, en %.
    humedad: float | None = None
    #: Viento medio a 10 m, en km/h.
    viento_kmh: float | None = None
    #: Ráfaga a 10 m, en km/h. Es la que gobierna las reglas: una ráfaga de
    #: 60 km/h tumba una rama sobre un cable aunque el viento medio sea 25.
    rafaga_kmh: float | None = None
    #: Índice UV, adimensional.
    uv: float | None = None


@dataclass(frozen=True, slots=True)
class Umbrales:
    """Parámetros de la política. Se pasan por argumento para poder testearlos.

    Todos los valores por defecto están justificados uno por uno en el
    encabezado del módulo. Los de UV son un estándar internacional; el resto son
    hipótesis calibrables, y así hay que leerlas.
    """

    # -- Lluvia: anegamiento urbano y remoción en masa -----------------------
    intensidad_mm_h: float = 5.0
    intensidad_critica_mm_h: float = 10.0
    acumulado_3h_mm: float = 15.0
    acumulado_3h_critico_mm: float = 25.0
    acumulado_24h_mm: float = 40.0
    acumulado_24h_critico_mm: float = 60.0
    #: Milímetros en la ventana por debajo de los cuales la comuna no genera
    #: evento POR LLUVIA. 0.2 mm en 24 h es llovizna de modelo. Ojo: desde la
    #: v2 no es el único motivo de emisión — una comuna seca con UV 12 sí emite.
    mm_minimo_ingesta: float = 0.2
    ventana_horas: int = 24

    # -- Incendio: propagación (regla 30-30-30 y tramo costero) --------------
    incendio_temp_c: float = 30.0
    incendio_humedad: float = 30.0
    incendio_rafaga_kmh: float = 30.0
    incendio_aviso_temp_c: float = 25.0
    incendio_aviso_humedad: float = 40.0
    incendio_aviso_rafaga_kmh: float = 25.0
    ventana_incendio_horas: int = 12

    # -- Viento por sí solo --------------------------------------------------
    rafaga_aviso_kmh: float = 60.0
    rafaga_critica_kmh: float = 80.0

    # -- Calor: riesgo directo a la salud ------------------------------------
    calor_aviso_c: float = 32.0
    calor_critico_c: float = 36.0
    #: Mínima por encima de la cual la noche no da alivio térmico. No dispara
    #: sola: agrava un aviso de calor a crítico.
    noche_tropical_c: float = 20.0
    ventana_calor_horas: int = 24

    # -- Índice UV -----------------------------------------------------------
    #: Bandas «muy alto» y «extremo» de la escala OMS. No son negociables.
    uv_aviso: float = 8.0
    uv_critico: float = 11.0
    ventana_uv_horas: int = 6

    @classmethod
    def from_settings(cls) -> Umbrales:
        from app.core.config import settings

        return cls(
            intensidad_mm_h=settings.OPENMETEO_INTENSITY_MM_H,
            intensidad_critica_mm_h=settings.OPENMETEO_INTENSITY_CRITICAL_MM_H,
            acumulado_3h_mm=settings.OPENMETEO_ACCUM_3H_MM,
            acumulado_3h_critico_mm=settings.OPENMETEO_ACCUM_3H_CRITICAL_MM,
            acumulado_24h_mm=settings.OPENMETEO_ACCUM_24H_MM,
            acumulado_24h_critico_mm=settings.OPENMETEO_ACCUM_24H_CRITICAL_MM,
            mm_minimo_ingesta=settings.OPENMETEO_MIN_INGEST_MM,
            ventana_horas=settings.OPENMETEO_WINDOW_HOURS,
            incendio_temp_c=settings.OPENMETEO_FIRE_TEMP_C,
            incendio_humedad=settings.OPENMETEO_FIRE_HUMIDITY_PCT,
            incendio_rafaga_kmh=settings.OPENMETEO_FIRE_GUST_KMH,
            incendio_aviso_temp_c=settings.OPENMETEO_FIRE_WATCH_TEMP_C,
            incendio_aviso_humedad=settings.OPENMETEO_FIRE_WATCH_HUMIDITY_PCT,
            incendio_aviso_rafaga_kmh=settings.OPENMETEO_FIRE_WATCH_GUST_KMH,
            ventana_incendio_horas=settings.OPENMETEO_FIRE_WINDOW_HOURS,
            rafaga_aviso_kmh=settings.OPENMETEO_GUST_WATCH_KMH,
            rafaga_critica_kmh=settings.OPENMETEO_GUST_CRITICAL_KMH,
            calor_aviso_c=settings.OPENMETEO_HEAT_WATCH_C,
            calor_critico_c=settings.OPENMETEO_HEAT_CRITICAL_C,
            noche_tropical_c=settings.OPENMETEO_TROPICAL_NIGHT_C,
            ventana_calor_horas=settings.OPENMETEO_HEAT_WINDOW_HOURS,
            uv_aviso=settings.OPENMETEO_UV_WATCH,
            uv_critico=settings.OPENMETEO_UV_CRITICAL,
            ventana_uv_horas=settings.OPENMETEO_UV_WINDOW_HOURS,
        )


@dataclass(frozen=True, slots=True)
class Pronostico:
    """Lo que este collector sabe de una comuna. Es el contrato de salida.

    `to_dict()` produce el JSON estandarizado y ligero que viaja en
    `raw_data["_weather"]` y que un endpoint de mapa puede servir tal cual, sin
    volver a consultar Open-Meteo ni recalcular nada.

    Los campos se agrupan en tres bloques y conviene no mezclarlos:

    * **Lluvia** (`mm_*`, `riesgo_inundacion`, `nivel`, `motivos`) — el contrato
      de la v1, intacto. Es lo que dibuja la capa de MapLibre.
    * **Ambiente** (`temp_*`, `humedad_min`, `viento_*`, `rafaga_max_kmh`,
      `uv_max`) — los números que el widget muestra en su estado silencioso.
    * **Estado táctico** (`severidad`, `amenaza`, `disparos`) — la decisión.
    """

    comuna: str
    lat: float
    lon: float
    inicio: datetime
    fin: datetime
    horas: int

    # -- Bloque de lluvia (contrato v1) --------------------------------------
    mm_total: float
    mm_hora_max: float
    mm_3h_max: float
    hora_pico: datetime | None
    probabilidad_max: int | None
    horas_con_lluvia: int
    riesgo_inundacion: bool
    nivel: str
    motivos: tuple[str, ...]

    # -- Bloque de ambiente --------------------------------------------------
    #: Lectura de la hora en curso: el primer paso de la ventana. Es lo que el
    #: widget muestra cuando no hay nada que alertar, y por eso se guarda aparte
    #: de los máximos: «22 °C ahora» y «31 °C hoy» son dos frases distintas.
    temp_actual_c: float | None
    viento_actual_kmh: float | None
    temp_max_c: float | None
    temp_min_c: float | None
    humedad_min: float | None
    viento_max_kmh: float | None
    rafaga_max_kmh: float | None
    uv_max: float | None

    # -- Bloque de estado táctico --------------------------------------------
    severidad: str
    #: Amenaza responsable del estado, o `None` en calma. Es la que el widget
    #: expande. Ver `PRIORIDAD_AMENAZA` para el desempate.
    amenaza: str | None
    disparos: tuple[Disparo, ...]

    modelo: str

    @property
    def hay_lluvia(self) -> bool:
        """¿La comuna tiene lluvia por encima del piso de emisión?"""
        return self.nivel != NIVEL_SECO

    @property
    def hay_senal(self) -> bool:
        """¿Vale la pena guardar esta comuna como señal?

        La v1 preguntaba sólo por lluvia, y era correcto mientras el collector
        midiera una sola variable. Con seis, «no llovió» dejó de ser sinónimo de
        «no pasa nada»: una tarde de febrero a 38 °C con UV 12 y 0,0 mm es
        exactamente el estado que esta capa existe para describir, y con el
        criterio antiguo no habría generado ni una fila.
        """
        return self.hay_lluvia or self.severidad != SEVERIDAD_NINGUNA

    @property
    def disparo_principal(self) -> Disparo | None:
        """El disparo que el widget expande. `None` en calma."""
        return mas_grave(self.disparos)

    def to_dict(self) -> dict[str, Any]:
        """JSON plano y serializable. Nada de datetimes ni de enums acá dentro.

        Las fechas salen en ISO-8601 UTC porque este diccionario termina en una
        columna JSONB, y un `datetime` no es serializable a JSON. La lección ya
        se pagó una vez en `seismic_row`: asyncpg tampoco convierte de vuelta el
        texto a `timestamptz`, así que quien lo relea tiene que reconstruirlo.
        """
        principal = self.disparo_principal
        return {
            AMBITO_KEY: AMBITO_COMUNA,
            "comuna": self.comuna,
            "lat": self.lat,
            "lon": self.lon,
            "ventana_horas": self.horas,
            "inicio": self.inicio.isoformat(),
            "fin": self.fin.isoformat(),
            "mm_total": self.mm_total,
            "mm_hora_max": self.mm_hora_max,
            "mm_3h_max": self.mm_3h_max,
            "hora_pico": self.hora_pico.isoformat() if self.hora_pico else None,
            "probabilidad_max": self.probabilidad_max,
            "horas_con_lluvia": self.horas_con_lluvia,
            "riesgo_inundacion": self.riesgo_inundacion,
            "nivel": self.nivel,
            "motivos": list(self.motivos),
            "temp_actual_c": self.temp_actual_c,
            "viento_actual_kmh": self.viento_actual_kmh,
            "temp_max_c": self.temp_max_c,
            "temp_min_c": self.temp_min_c,
            "humedad_min": self.humedad_min,
            "viento_max_kmh": self.viento_max_kmh,
            "rafaga_max_kmh": self.rafaga_max_kmh,
            "uv_max": self.uv_max,
            "severidad": self.severidad,
            "amenaza": self.amenaza,
            "disparos": [disparo.to_dict() for disparo in self.disparos],
            "disparo_principal": principal.to_dict() if principal else None,
            "modelo": self.modelo,
            "fuente": "open-meteo",
        }


# ===========================================================================
# Ventanas y agregados
# ===========================================================================


def piso_horario(momento: datetime) -> datetime:
    """Trunca a la hora en curso, en UTC.

    Es el ancla de todo el módulo: marca el inicio de la ventana, el `timestamp`
    del evento y la clave horaria del `external_id`. Se trunca hacia **abajo**
    —no se redondea— por una razón concreta: `EventCreate` rechaza timestamps más
    de `INGEST_FUTURE_TOLERANCE_SECONDS` (5 min) en el futuro. Redondear hacia
    arriba pondría el evento hasta 59 minutos adelante y la validación lo
    tumbaría, con el collector reportando todo rechazado sin explicar por qué.
    """
    return momento.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def recortar_ventana(
    puntos: Sequence[PuntoHorario], *, desde: datetime, horas: int
) -> list[PuntoHorario]:
    """Los pasos horarios de `[desde, desde + horas)`, en orden.

    Open-Meteo devuelve el día completo desde las 00:00, así que la mitad de la
    respuesta suele ser pasado. Se descarta: a las 22:00 no interesa que a las
    04:00 de esta mañana llovió, interesa la madrugada que viene.

    Se llama una vez por amenaza con `horas` distintas —24 para el suelo, 6 para
    el UV—. Recortar cuatro veces sobre 48 puntos es trabajo despreciable
    comparado con mantener cuatro listas paralelas y equivocarse en una.
    """
    fin = desde + timedelta(hours=horas)
    dentro = [punto for punto in puntos if desde <= punto.momento < fin]
    return sorted(dentro, key=lambda punto: punto.momento)


def acumulado_maximo(
    puntos: Sequence[PuntoHorario], *, horas: int
) -> tuple[float, datetime | None]:
    """Máximo acumulado en una ventana móvil de `horas`, y cuándo empieza.

    La ventana se define por **timestamp** y no por número de posiciones. Con una
    serie horaria completa da lo mismo, pero si el modelo entrega pasos de 3 h
    —los hay— o si falta una hora, contar posiciones sumaría seis horas de lluvia
    creyendo que son tres y el flag saltaría solo. Sumar por tiempo no puede
    equivocarse en eso.
    """
    mejor = 0.0
    inicio: datetime | None = None
    for indice, punto in enumerate(puntos):
        limite = punto.momento + timedelta(hours=horas)
        total = sum(otro.mm or 0.0 for otro in puntos[indice:] if otro.momento < limite)
        if total > mejor:
            mejor, inicio = total, punto.momento
    return round(mejor, 1), inicio


def _extremo(
    puntos: Sequence[PuntoHorario],
    campo: str,
    *,
    maximo: bool = True,
) -> tuple[float | None, datetime | None]:
    """Máximo (o mínimo) de una variable y el momento en que ocurre.

    Devuelve `(None, None)` cuando **ninguna** hora trae el dato. Es la
    diferencia entre "la humedad no bajó de 60 %" y "este modelo no publica
    humedad", y el que la confunda pondrá la región en 30-30-30 por defecto.
    """
    validos = [
        (getattr(punto, campo), punto.momento)
        for punto in puntos
        if getattr(punto, campo) is not None
    ]
    if not validos:
        return None, None
    elegir = max if maximo else min
    elegido = elegir(validos, key=lambda par: par[0])
    return round(float(elegido[0]), 1), elegido[1]


def _primero(puntos: Sequence[PuntoHorario], campo: str) -> float | None:
    """El valor de la hora en curso: el primer paso que lo traiga.

    No es lo mismo que el máximo. El widget silencioso dice «19 °C» porque son
    las 19 °C de ahora; anunciar los 31 °C del mediodía a las 21:00 sería
    describir un pasado que ya no existe.
    """
    for punto in puntos:
        valor = getattr(punto, campo)
        if valor is not None:
            return round(float(valor), 1)
    return None


def mas_grave(disparos: Sequence[Disparo]) -> Disparo | None:
    """El disparo que manda: primero por severidad, luego por `PRIORIDAD_AMENAZA`.

    El segundo criterio existe porque los empates son la norma, no la excepción:
    una tarde de temporal cruza intensidad, 3 h y 24 h a la vez, y una tarde de
    febrero cruza calor y UV a la vez. Sin un desempate estable, la métrica que
    el widget expande dependería del orden en que se evaluaron las reglas.
    """
    if not disparos:
        return None
    return max(
        disparos,
        key=lambda disparo: (
            ORDEN_SEVERIDAD.get(disparo.severidad, 0),
            PRIORIDAD_AMENAZA.get(disparo.amenaza, 0),
            disparo.valor,
        ),
    )


# ===========================================================================
# Las reglas, una función por amenaza
# ===========================================================================
#
# Todas tienen la misma firma —serie ya recortada + umbrales → lista de
# disparos— y todas son puras. Es lo que permite testear el 30-30-30 con cuatro
# puntos escritos a mano y sin tocar la red.


def evaluar_lluvia(
    puntos: Sequence[PuntoHorario], reglas: Umbrales
) -> tuple[list[Disparo], float, float, float]:
    """Las tres reglas de agua, repartidas en dos amenazas.

    Devuelve `(disparos, mm_total, mm_hora_max, mm_3h_max)` porque los tres
    agregados también son parte del contrato de salida y calcularlos dos veces
    invitaría a que divergieran.

    **La intensidad horaria es `lluvia`; los acumulados son `remocion`.** El
    mecanismo es distinto y la respuesta también: ante 12 mm en una hora se
    evita un paso bajo nivel, ante 70 mm en un día se evita un cerro.
    """
    validos = [punto for punto in puntos if punto.mm is not None]
    mm_total = round(sum(punto.mm or 0.0 for punto in validos), 1)
    mm_hora_max = round(max((punto.mm or 0.0 for punto in validos), default=0.0), 1)
    mm_3h_max, inicio_3h = acumulado_maximo(puntos, horas=3)

    pico = max(validos, key=lambda punto: punto.mm or 0.0, default=None)
    hora_pico = pico.momento if pico and (pico.mm or 0.0) > 0 else None

    disparos: list[Disparo] = []

    if mm_hora_max >= reglas.intensidad_mm_h:
        critico = mm_hora_max >= reglas.intensidad_critica_mm_h
        umbral = reglas.intensidad_critica_mm_h if critico else reglas.intensidad_mm_h
        disparos.append(
            Disparo(
                amenaza=AMENAZA_LLUVIA,
                severidad=SEVERIDAD_CRITICA if critico else SEVERIDAD_AVISO,
                metrica="mm_hora_max",
                valor=mm_hora_max,
                unidad="mm/h",
                umbral=umbral,
                texto=f"intensidad {mm_hora_max:.1f} mm/h ≥ {umbral:.1f} mm/h",
                momento=hora_pico,
            )
        )

    if mm_3h_max >= reglas.acumulado_3h_mm:
        critico = mm_3h_max >= reglas.acumulado_3h_critico_mm
        umbral = reglas.acumulado_3h_critico_mm if critico else reglas.acumulado_3h_mm
        disparos.append(
            Disparo(
                amenaza=AMENAZA_REMOCION,
                severidad=SEVERIDAD_CRITICA if critico else SEVERIDAD_AVISO,
                metrica="mm_3h_max",
                valor=mm_3h_max,
                unidad="mm/3 h",
                umbral=umbral,
                texto=f"acumulado en 3 h {mm_3h_max:.1f} mm ≥ {umbral:.1f} mm",
                momento=inicio_3h,
            )
        )

    if mm_total >= reglas.acumulado_24h_mm:
        critico = mm_total >= reglas.acumulado_24h_critico_mm
        umbral = reglas.acumulado_24h_critico_mm if critico else reglas.acumulado_24h_mm
        disparos.append(
            Disparo(
                amenaza=AMENAZA_REMOCION,
                severidad=SEVERIDAD_CRITICA if critico else SEVERIDAD_AVISO,
                metrica="mm_total",
                valor=mm_total,
                unidad=f"mm/{reglas.ventana_horas} h",
                umbral=umbral,
                texto=(
                    f"acumulado en {reglas.ventana_horas} h {mm_total:.1f} mm "
                    f"≥ {umbral:.1f} mm"
                ),
            )
        )

    return disparos, mm_total, mm_hora_max, mm_3h_max


def evaluar_incendio(
    puntos: Sequence[PuntoHorario], reglas: Umbrales
) -> list[Disparo]:
    """Regla 30-30-30 y su tramo costero.

    # Las tres condiciones se exigen en el MISMO paso horario

    Es la decisión de implementación que hace que esta regla signifique algo. La
    versión perezosa —comparar `temp_max`, `humedad_min` y `rafaga_max` de la
    ventana, cada uno por su lado— cumpliría el 30-30-30 en un día cualquiera de
    Valparaíso: 30 °C a las 16:00, 28 % de humedad a las 15:00 y una ráfaga de
    35 km/h a las 03:00 de la madrugada, cuando hacía 12 °C y había rocío. Eso no
    es un escenario de propagación, es un artefacto de tres máximos que nunca se
    encontraron.

    Lo que enciende un cerro es la **coincidencia**, así que la coincidencia es
    lo que se busca: se recorre paso a paso y se exige que las tres condiciones
    se cumplan en la misma hora. El disparo lleva la hora en que ocurre, que es
    justamente lo que una brigada necesita saber.

    # Un paso incompleto no cuenta

    Si falta cualquiera de las tres variables en esa hora, la hora se descarta.
    No se rellena con el valor de la hora anterior ni con un cero: no sabemos si
    se cumple, y afirmar que sí —o que no— sería inventar.
    """
    completos = [
        punto
        for punto in puntos
        if punto.temp_c is not None
        and punto.humedad is not None
        and punto.rafaga_kmh is not None
    ]
    if not completos:
        return []

    def cumple(punto: PuntoHorario, temp: float, hum: float, rafaga: float) -> bool:
        return (
            (punto.temp_c or 0.0) >= temp
            and (punto.humedad if punto.humedad is not None else 100.0) <= hum
            and (punto.rafaga_kmh or 0.0) >= rafaga
        )

    criticos = [
        punto
        for punto in completos
        if cumple(
            punto,
            reglas.incendio_temp_c,
            reglas.incendio_humedad,
            reglas.incendio_rafaga_kmh,
        )
    ]
    if criticos:
        peor = max(criticos, key=lambda punto: (punto.temp_c or 0.0, punto.rafaga_kmh or 0.0))
        return [
            Disparo(
                amenaza=AMENAZA_INCENDIO,
                severidad=SEVERIDAD_CRITICA,
                metrica="regla_30_30_30",
                # El valor que se expande es la temperatura: es la cifra que la
                # gente reconoce del titular «30-30-30». Las otras dos van en el
                # texto.
                valor=round(float(peor.temp_c or 0.0), 1),
                unidad="°C",
                umbral=reglas.incendio_temp_c,
                texto=(
                    f"regla 30-30-30: {peor.temp_c:.0f} °C, "
                    f"{peor.humedad:.0f} % de humedad y ráfagas de "
                    f"{peor.rafaga_kmh:.0f} km/h en la misma hora "
                    f"({len(criticos)} h en la ventana)"
                ),
                momento=peor.momento,
            )
        ]

    avisos = [
        punto
        for punto in completos
        if cumple(
            punto,
            reglas.incendio_aviso_temp_c,
            reglas.incendio_aviso_humedad,
            reglas.incendio_aviso_rafaga_kmh,
        )
    ]
    if not avisos:
        return []

    peor = max(avisos, key=lambda punto: (punto.temp_c or 0.0, punto.rafaga_kmh or 0.0))
    return [
        Disparo(
            amenaza=AMENAZA_INCENDIO,
            severidad=SEVERIDAD_AVISO,
            metrica="regla_30_30_30",
            valor=round(float(peor.temp_c or 0.0), 1),
            unidad="°C",
            umbral=reglas.incendio_aviso_temp_c,
            texto=(
                f"condición de propagación: {peor.temp_c:.0f} °C, "
                f"{peor.humedad:.0f} % de humedad y ráfagas de "
                f"{peor.rafaga_kmh:.0f} km/h en la misma hora"
            ),
            momento=peor.momento,
        )
    ]


def evaluar_viento(puntos: Sequence[PuntoHorario], reglas: Umbrales) -> list[Disparo]:
    """Ráfaga por sí sola, sin pedirle nada al termómetro.

    Independiente del 30-30-30 a propósito: a 60 km/h se suspende el combate
    aéreo y empiezan a caer ramas sobre el tendido —la capa de cortes de luz de
    este mismo sistema— y nada de eso necesita que haga calor. Un temporal
    invernal de 70 km/h y 12 °C no cumple ninguna condición de incendio y sigue
    siendo una tarde en la que hay que avisar.
    """
    rafaga, momento = _extremo(puntos, "rafaga_kmh")
    if rafaga is None or rafaga < reglas.rafaga_aviso_kmh:
        return []

    critico = rafaga >= reglas.rafaga_critica_kmh
    umbral = reglas.rafaga_critica_kmh if critico else reglas.rafaga_aviso_kmh
    return [
        Disparo(
            amenaza=AMENAZA_VIENTO,
            severidad=SEVERIDAD_CRITICA if critico else SEVERIDAD_AVISO,
            metrica="rafaga_max_kmh",
            valor=rafaga,
            unidad="km/h",
            umbral=umbral,
            texto=f"ráfagas de {rafaga:.0f} km/h ≥ {umbral:.0f} km/h",
            momento=momento,
        )
    ]


def evaluar_calor(puntos: Sequence[PuntoHorario], reglas: Umbrales) -> list[Disparo]:
    """Calor con riesgo directo a la salud. **No** es una declaración de ola de calor.

    La distinción está desarrollada en el encabezado del módulo y es importante
    de mantener en el vocabulario: la DMC define ola de calor por percentil 90
    diario durante tres días consecutivos, y este collector no puede calcular ni
    una cosa ni la otra. Lo que mide es una temperatura absoluta con
    consecuencias fisiológicas conocidas.

    # La noche tropical agrava, no dispara

    Una mínima que no baja de 20 °C no es peligrosa por sí misma —hay noches así
    en la costa sin ninguna consecuencia—, pero convierte un día de 33 °C en algo
    distinto: el cuerpo no recupera entre exposiciones, y la mortalidad asociada
    al calor sube justamente cuando desaparece el alivio nocturno. Por eso
    escala el aviso a crítico en vez de emitir un disparo propio: un segundo
    disparo competiría por el sitio del widget con la métrica que la gente
    entiende, que es la máxima.
    """
    maxima, momento = _extremo(puntos, "temp_c")
    if maxima is None or maxima < reglas.calor_aviso_c:
        return []

    minima, _ = _extremo(puntos, "temp_c", maximo=False)
    noche_tropical = minima is not None and minima >= reglas.noche_tropical_c

    critico = maxima >= reglas.calor_critico_c or noche_tropical
    umbral = reglas.calor_critico_c if maxima >= reglas.calor_critico_c else reglas.calor_aviso_c

    texto = f"máxima de {maxima:.0f} °C ≥ {umbral:.0f} °C"
    if noche_tropical and maxima < reglas.calor_critico_c:
        texto += (
            f", con mínima de {minima:.0f} °C: la noche no da alivio térmico "
            f"(≥ {reglas.noche_tropical_c:.0f} °C)"
        )

    return [
        Disparo(
            amenaza=AMENAZA_CALOR,
            severidad=SEVERIDAD_CRITICA if critico else SEVERIDAD_AVISO,
            metrica="temp_max_c",
            valor=maxima,
            unidad="°C",
            umbral=umbral,
            texto=texto,
            momento=momento,
        )
    ]


def evaluar_uv(puntos: Sequence[PuntoHorario], reglas: Umbrales) -> list[Disparo]:
    """Índice UV contra las bandas de la OMS.

    Los únicos umbrales de este módulo que no son una hipótesis: 8 es el inicio
    de la banda «muy alto» (rojo) y 11 el de «extremo» (morado) en la escala
    global de la OMS/OMM, la misma que usan la EPA y el ICNIRP. El widget pinta
    el morado en 11 porque ese color ya significa eso para quien haya visto un
    pronóstico en su vida.

    La ventana es la más corta del módulo —6 h— y ahí está toda la gracia: el UV
    sólo existe de día y su bloque útil es el entorno del mediodía solar, así que
    seis horas encienden el aviso la mañana del día peligroso y lo apagan al
    atardecer, en vez de dejarlo encendido veinticuatro horas por algo que va a
    pasar mañana.
    """
    uv, momento = _extremo(puntos, "uv")
    if uv is None or uv < reglas.uv_aviso:
        return []

    critico = uv >= reglas.uv_critico
    umbral = reglas.uv_critico if critico else reglas.uv_aviso
    banda = "extremo" if critico else "muy alto"
    return [
        Disparo(
            amenaza=AMENAZA_UV,
            severidad=SEVERIDAD_CRITICA if critico else SEVERIDAD_AVISO,
            metrica="uv_max",
            valor=uv,
            unidad="UV",
            umbral=umbral,
            texto=f"índice UV {uv:.0f} ({banda}, ≥ {umbral:.0f})",
            momento=momento,
        )
    ]


# ===========================================================================
# Orquestación
# ===========================================================================


def evaluar(
    comuna: Comuna,
    puntos: Sequence[PuntoHorario],
    *,
    ahora: datetime | None = None,
    umbrales: Umbrales | None = None,
    modelo: str = "best_match",
) -> Pronostico:
    """Aplica la política completa a la serie de una comuna. Sin red, sin base de datos.

    Es el corazón testeable del collector: se le pasan puntos armados a mano y se
    comprueba el estado. `openmeteo_worker` no decide nada que no esté acá.

    Cada amenaza se evalúa sobre **su** ventana. La lluvia mira 24 h porque el
    suelo se satura despacio; el UV mira 6 porque quema ahora. Ver el encabezado.
    """
    reglas = umbrales or Umbrales()
    inicio = piso_horario(ahora or datetime.now(UTC))

    ventana_lluvia = recortar_ventana(puntos, desde=inicio, horas=reglas.ventana_horas)
    ventana_incendio = recortar_ventana(
        puntos, desde=inicio, horas=reglas.ventana_incendio_horas
    )
    ventana_calor = recortar_ventana(puntos, desde=inicio, horas=reglas.ventana_calor_horas)
    ventana_uv = recortar_ventana(puntos, desde=inicio, horas=reglas.ventana_uv_horas)

    disparos_lluvia, mm_total, mm_hora_max, mm_3h_max = evaluar_lluvia(
        ventana_lluvia, reglas
    )

    validos = [punto for punto in ventana_lluvia if punto.mm is not None]
    pico = max(validos, key=lambda punto: punto.mm or 0.0, default=None)
    hora_pico = pico.momento if pico and (pico.mm or 0.0) > 0 else None

    probabilidades = [
        punto.probabilidad for punto in ventana_lluvia if punto.probabilidad is not None
    ]
    probabilidad_max = max(probabilidades) if probabilidades else None

    # 0.1 mm es el paso de publicación de la variable: por debajo de eso el
    # modelo está diciendo "traza", no "hora con lluvia".
    horas_con_lluvia = sum(1 for punto in validos if (punto.mm or 0.0) >= 0.1)

    # `motivos` conserva EXACTAMENTE el significado de la v1 —sólo agua— porque
    # es lo que lee la capa de MapLibre. El estado táctico completo viaja en
    # `disparos`, que es un campo nuevo y nadie viejo consume.
    motivos = tuple(disparo.texto for disparo in disparos_lluvia)

    disparos = [
        *disparos_lluvia,
        *evaluar_incendio(ventana_incendio, reglas),
        *evaluar_viento(ventana_incendio, reglas),
        *evaluar_calor(ventana_calor, reglas),
        *evaluar_uv(ventana_uv, reglas),
    ]

    temp_max, _ = _extremo(ventana_calor, "temp_c")
    temp_min, _ = _extremo(ventana_calor, "temp_c", maximo=False)
    humedad_min, _ = _extremo(ventana_incendio, "humedad", maximo=False)
    viento_max, _ = _extremo(ventana_incendio, "viento_kmh")
    rafaga_max, _ = _extremo(ventana_incendio, "rafaga_kmh")
    uv_max, _ = _extremo(ventana_uv, "uv")

    principal = mas_grave(disparos)

    return Pronostico(
        comuna=comuna.nombre,
        lat=comuna.lat,
        lon=comuna.lon,
        inicio=inicio,
        fin=inicio + timedelta(hours=reglas.ventana_horas),
        horas=reglas.ventana_horas,
        mm_total=mm_total,
        mm_hora_max=mm_hora_max,
        mm_3h_max=mm_3h_max,
        hora_pico=hora_pico,
        probabilidad_max=probabilidad_max,
        horas_con_lluvia=horas_con_lluvia,
        riesgo_inundacion=bool(disparos_lluvia),
        nivel=_nivel(mm_total, mm_hora_max, motivos, reglas),
        motivos=motivos,
        temp_actual_c=_primero(ventana_calor, "temp_c"),
        viento_actual_kmh=_primero(ventana_incendio, "viento_kmh"),
        temp_max_c=temp_max,
        temp_min_c=temp_min,
        humedad_min=humedad_min,
        viento_max_kmh=viento_max,
        rafaga_max_kmh=rafaga_max,
        uv_max=uv_max,
        severidad=principal.severidad if principal else SEVERIDAD_NINGUNA,
        amenaza=principal.amenaza if principal else None,
        disparos=tuple(disparos),
        modelo=modelo,
    )


def _nivel(
    mm_total: float, mm_hora_max: float, motivos: Sequence[str], reglas: Umbrales
) -> str:
    if not motivos:
        return NIVEL_LLUVIA if mm_total >= reglas.mm_minimo_ingesta else NIVEL_SECO
    if len(motivos) >= 2 or mm_hora_max >= reglas.intensidad_mm_h * _FACTOR_ALTO:
        return NIVEL_RIESGO_ALTO
    return NIVEL_RIESGO


# ===========================================================================
# Texto legible
# ===========================================================================

#: Encabezado de la frase de alerta, por amenaza. En mayúsculas y sin adornos:
#: es lo que alguien lee de reojo en la lista de señales.
#:
#: Ninguno de estos rótulos afirma que algo esté ocurriendo. «CONDICIÓN DE
#: PROPAGACIÓN» no dice que haya un incendio, dice que si lo hubiera correría;
#: «CALOR EXTREMO» no dice «ola de calor», que es un término con una definición
#: oficial que esta capa no puede cumplir. Ver el encabezado del módulo.
ROTULO_AMENAZA: dict[str, str] = {
    AMENAZA_LLUVIA: "RIESGO DE ANEGAMIENTO",
    AMENAZA_REMOCION: "RIESGO DE REMOCIÓN EN MASA",
    AMENAZA_INCENDIO: "CONDICIÓN DE PROPAGACIÓN DE INCENDIOS",
    AMENAZA_VIENTO: "VIENTO FUERTE",
    AMENAZA_CALOR: "CALOR EXTREMO",
    AMENAZA_UV: "ÍNDICE UV PELIGROSO",
}


def describir(pronostico: Pronostico) -> str:
    """Texto legible en español para el popup del mapa y para `raw_events.text`.

    Tres detalles deliberados:

    * La hora del pico se expresa en **hora de Chile**, porque la lee una
      persona. Todo lo demás del sistema es UTC y sigue siéndolo en el payload.
    * La primera frase describe el ambiente aunque no haya nada que alertar. Una
      señal que sólo sabe decir «riesgo» no sirve para el 95 % de los días del
      año, que es cuando el operador igual quiere saber qué tiempo hace.
    * Termina aclarando que es un pronóstico y que las alertas las declara
      SENAPRED. Es la misma cautela con que el collector del USGS aclara que el
      protocolo de tsunami no es una alerta de tsunami: el sistema no puede
      insinuar que declaró algo que no le corresponde declarar.

    El formato ``Comuna: X.`` no es decorativo: es el patrón que
    `communes._TEXT_COMMUNE_PATTERNS` sabe leer.
    """
    frases: list[str] = []

    if pronostico.hay_lluvia:
        partes = [
            f"Lluvia pronosticada en {pronostico.comuna}: "
            f"{pronostico.mm_total:.1f} mm en {pronostico.horas} h, "
            f"máximo de {pronostico.mm_hora_max:.1f} mm/h"
        ]
        if pronostico.hora_pico is not None:
            local = pronostico.hora_pico.astimezone(CHILE_TZ)
            partes.append(f" hacia las {local:%H:%M} h")
        if pronostico.probabilidad_max is not None:
            partes.append(f" (probabilidad {pronostico.probabilidad_max} %)")
        partes.append(".")
        frases.append("".join(partes))
    else:
        frases.append(_frase_ambiente(pronostico))

    # `riesgo_inundacion` conserva su rótulo literal de la v1: hay un test que
    # comprueba que la cadena "RIESGO DE INUNDACIÓN" siga apareciendo, y con
    # razón — es el texto que el hand-off del backend prometió al frontend.
    if pronostico.riesgo_inundacion:
        frases.append("RIESGO DE INUNDACIÓN: " + "; ".join(pronostico.motivos) + ".")

    otros = [
        disparo
        for disparo in pronostico.disparos
        if disparo.amenaza not in (AMENAZA_LLUVIA, AMENAZA_REMOCION)
    ]
    for disparo in sorted(
        otros,
        key=lambda item: (
            -ORDEN_SEVERIDAD.get(item.severidad, 0),
            -PRIORIDAD_AMENAZA.get(item.amenaza, 0),
        ),
    ):
        rotulo = ROTULO_AMENAZA.get(disparo.amenaza, disparo.amenaza.upper())
        frases.append(f"{rotulo}: {disparo.texto}.")

    frases.append(f"Comuna: {pronostico.comuna}.")
    frases.append(
        f"Pronóstico de Open-Meteo ({pronostico.modelo}); no es una alerta "
        f"oficial: las declara SENAPRED."
    )
    return " ".join(frases)


def _frase_ambiente(pronostico: Pronostico) -> str:
    """La primera frase cuando no hay lluvia. Describe, no alerta."""
    piezas: list[str] = []
    if pronostico.temp_max_c is not None:
        piezas.append(f"máxima de {pronostico.temp_max_c:.0f} °C")
    if pronostico.rafaga_max_kmh is not None:
        piezas.append(f"ráfagas de hasta {pronostico.rafaga_max_kmh:.0f} km/h")
    if pronostico.humedad_min is not None:
        piezas.append(f"humedad mínima de {pronostico.humedad_min:.0f} %")
    if pronostico.uv_max is not None:
        piezas.append(f"índice UV máximo de {pronostico.uv_max:.0f}")

    if not piezas:
        return f"Sin lluvia pronosticada en {pronostico.comuna}."
    return (
        f"Sin lluvia pronosticada en {pronostico.comuna}: "
        + ", ".join(piezas)
        + f" en las próximas {pronostico.horas} h."
    )
