"""Puntos de consulta meteorológica: las comunas de la Región de Valparaíso.

Por qué una lista fija y no una capa geográfica
-----------------------------------------------

Open-Meteo entrega el pronóstico de un punto, no de un polígono. Haría falta un
punto por comuna de todas formas, así que la alternativa —leer los centroides de
la capa de límites comunales— añadiría una dependencia geoespacial y una consulta
por corrida para llegar al mismo par de coordenadas que cabe en esta tabla.

Los 36 puntos son el **centro urbano** de cada comuna, no su centroide
geométrico, que en las comunas de cordillera cae en un cajón sin habitantes. La
precisión es de ~1 km, y da igual: la grilla del modelo global es de 9 a 11 km
—ver `openmeteo_client`— así que el error de la grilla domina por un orden de
magnitud sobre el de esta lista.

La consecuencia de esa resolución hay que decirla clara, porque cambia lo que el
dato significa: **Valparaíso, Viña del Mar y Concón pueden caer en la misma celda
de grilla** y devolver series idénticas. El flag `riesgo_inundacion` es una señal
de escala comunal, jamás de una quebrada o una calle en particular. Aun así se
emite un evento por comuna: el `external_id` se construye con el nombre de la
comuna (no con la coordenada), de modo que la capa del mapa tiene una señal por
comuna incluso cuando dos comparten celda.

Las dos comunas insulares quedan fuera a propósito
---------------------------------------------------

Isla de Pascua (-27.15, -109.43) y Juan Fernández (-33.64, -78.83) son parte de
la región y **no** están acá. Caen fuera de `settings.region_bbox`, así que sus
eventos entrarían marcados como fuera de región —o rechazados de plano si alguien
activa `REJECT_OUTSIDE_REGION`— y su clima no tiene nada que ver con el de la
franja continental. Si alguna vez se necesitan, se agregan por `.env` sin tocar
este archivo: es justamente para eso que `OPENMETEO_COMUNAS` existe.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Comuna:
    """Un punto de consulta con el nombre con que se publica en el mapa."""

    nombre: str
    lat: float
    lon: float

    @property
    def slug(self) -> str:
        """Clave estable para el `external_id`. Ver `slug()`."""
        return slug(self.nombre)


#: Reemplazos mínimos para el slug. `unicodedata.normalize` haría lo mismo, pero
#: con tres vocales y una eñe en juego, una tabla explícita es más fácil de leer
#: y no depende de la forma de normalización que traiga la plataforma.
_TILDES = str.maketrans(
    {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ü": "u", "ñ": "n"}
)


def slug(nombre: str) -> str:
    """Nombre de comuna → clave estable, sin tildes ni espacios.

    Es la mitad del `external_id` del que depende la idempotencia, así que se
    deriva del nombre y no se guarda aparte: si alguien corrige "Concon" a
    "Concón" en la lista, el slug se mueve con él y el upsert empieza una serie
    nueva. Es lo correcto —son dos etiquetas distintas— pero conviene saberlo
    antes de editar la tabla de comunas.
    """
    limpio = nombre.strip().lower().translate(_TILDES)
    return "".join(caracter if caracter.isalnum() else "-" for caracter in limpio)


#: Las 36 comunas continentales de la Región de Valparaíso, agrupadas por
#: provincia. El orden importa poco para el sistema y mucho para quien lea la
#: lista buscando si falta alguna.
COMUNAS_V_REGION: tuple[Comuna, ...] = (
    # -- Provincia de Valparaíso ---------------------------------------------
    Comuna("Valparaíso", -33.0472, -71.6127),
    Comuna("Viña del Mar", -33.0245, -71.5518),
    Comuna("Concón", -32.9256, -71.5222),
    Comuna("Quintero", -32.7833, -71.5333),
    Comuna("Puchuncaví", -32.7233, -71.4167),
    Comuna("Casablanca", -33.3200, -71.4100),
    # -- Provincia de Marga Marga --------------------------------------------
    Comuna("Quilpué", -33.0472, -71.4425),
    Comuna("Villa Alemana", -33.0422, -71.3733),
    Comuna("Limache", -32.9903, -71.2703),
    Comuna("Olmué", -32.9989, -71.1889),
    # -- Provincia de Quillota -----------------------------------------------
    Comuna("Quillota", -32.8800, -71.2500),
    Comuna("La Calera", -32.7869, -71.1897),
    Comuna("La Cruz", -32.8250, -71.2333),
    Comuna("Hijuelas", -32.8028, -71.1478),
    Comuna("Nogales", -32.7333, -71.2000),
    # -- Provincia de San Antonio --------------------------------------------
    Comuna("San Antonio", -33.5933, -71.6217),
    Comuna("Cartagena", -33.5500, -71.6067),
    Comuna("El Tabo", -33.4500, -71.6667),
    Comuna("El Quisco", -33.3986, -71.6931),
    Comuna("Algarrobo", -33.3600, -71.6689),
    Comuna("Santo Domingo", -33.6389, -71.6250),
    # -- Provincia de San Felipe de Aconcagua --------------------------------
    Comuna("San Felipe", -32.7500, -70.7250),
    Comuna("Catemu", -32.7833, -70.9667),
    Comuna("Llaillay", -32.8383, -70.9558),
    Comuna("Panquehue", -32.8000, -70.9167),
    Comuna("Putaendo", -32.6289, -70.7208),
    Comuna("Santa María", -32.7500, -70.6667),
    # -- Provincia de Los Andes ----------------------------------------------
    Comuna("Los Andes", -32.8337, -70.5983),
    Comuna("Calle Larga", -32.8583, -70.6167),
    Comuna("Rinconada", -32.8333, -70.7167),
    Comuna("San Esteban", -32.7917, -70.5833),
    # -- Provincia de Petorca ------------------------------------------------
    Comuna("La Ligua", -32.4522, -71.2311),
    Comuna("Cabildo", -32.4267, -71.0750),
    Comuna("Papudo", -32.5083, -71.4467),
    Comuna("Petorca", -32.2500, -70.9333),
    Comuna("Zapallar", -32.5533, -71.4642),
)


def parse_comunas(raw: str | Sequence[str] | None) -> list[Comuna]:
    """Convierte la declaración del `.env` en comunas. Vacío = la lista fija.

    Formato ``nombre|lat|lon``, separando varias con ``;`` — el mismo que usa
    `parse_source_specs` para las capas institucionales, para no tener dos
    gramáticas de configuración en el mismo proyecto::

        OPENMETEO_COMUNAS="Valparaíso|-33.0472|-71.6127;Quilpué|-33.0472|-71.4425"

    Una declaración mal formada **revienta acá**, en la construcción del
    collector. El runner convierte eso en una corrida `failed` con el mensaje
    visible en `collector_runs` (ver `_record_bootstrap_failure`), que es
    preferible a arrancar consultando media región en silencio.
    """
    if raw is None:
        return list(COMUNAS_V_REGION)

    trozos: Iterable[str] = raw.split(";") if isinstance(raw, str) else raw
    comunas: list[Comuna] = []
    for trozo in trozos:
        token = trozo.strip()
        if not token:
            continue
        partes = [parte.strip() for parte in token.split("|")]
        if len(partes) != 3:
            raise ValueError(
                f"comuna mal declarada: {token!r}. Formato esperado 'nombre|lat|lon'"
            )
        nombre, lat_txt, lon_txt = partes
        if not nombre:
            raise ValueError(f"comuna sin nombre en {token!r}")
        try:
            lat, lon = float(lat_txt), float(lon_txt)
        except ValueError as exc:
            raise ValueError(
                f"coordenadas no numéricas en {token!r}: {lat_txt!r}, {lon_txt!r}"
            ) from exc
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            raise ValueError(f"coordenadas fuera de rango en {token!r}")
        comunas.append(Comuna(nombre, lat, lon))

    return comunas or list(COMUNAS_V_REGION)
