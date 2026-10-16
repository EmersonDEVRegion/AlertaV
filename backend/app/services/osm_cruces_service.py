"""Carga el índice de cruces de OSM desde el release del repositorio.

El índice lo arma `scripts/osm_cruces.py` en GitHub Actions
(`.github/workflows/osm-cruces.yml`) y lo deja en el release fijo
`osm-cruces`, con `osm_cruces.csv.gz` y `osm_cruces.json` (fecha de los datos
y filas). Los workers revisan cada `OSM_CRUCES_POLL_SECONDS` el JSON —1 KB— y,
si su `generado_en` no es el cargado (`osm_indice.version`), bajan el CSV y
reemplazan la tabla entera.

Se baja por la URL pública de descarga del release y **no por la API de
GitHub**: la API sin autenticar permite 60 peticiones por hora por IP, y la IP
de salida de Render es compartida (respondía 403 «rate limit exceeded» a la
primera). Tampoco hace falta darle a GitHub la contraseña de la base: el
workflow sólo publica archivos, y quien escribe en la base es el backend.

Reemplazar es todo o nada, dentro de una transacción: quien busca un cruce
mientras se carga ve el índice anterior hasta el `COMMIT`. Un archivo con
menos de `OSM_CRUCES_MIN_FILAS` filas se rechaza: un extracto roto no puede
dejar a la región sin esquinas.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.identidad import nominatim_user_agent

logger = logging.getLogger(__name__)

COLUMNAS = ("nodo", "lat", "lon", "nombre_a", "nombre_b", "ultima_a", "ultima_b", "comunas")

#: Estados de una revisión, para el log.
AL_DIA = "al_dia"
CARGADO = "cargado"
SIN_RELEASE = "sin_release"
RECHAZADO = "rechazado"


class IndiceInvalidoError(ValueError):
    """El archivo del release no sirve: columnas, filas o tamaño."""


def url_de(archivo: str) -> str:
    """URL pública de un archivo del release del índice."""
    return (
        f"https://github.com/{settings.OSM_CRUCES_REPO}/releases/download/"
        f"{settings.OSM_CRUCES_RELEASE}/{archivo}"
    )


def version_de(meta: Any) -> tuple[str, str | None] | None:
    """`(version, datos_de)` del JSON del release, o None si no sirve."""
    if not isinstance(meta, dict) or not meta.get("generado_en"):
        return None
    datos_de = meta.get("datos_de")
    return str(meta["generado_en"]), str(datos_de) if datos_de else None


Fila = tuple[int, float, float, str, str, str, str, list[str]]


def iterar_filas(comprimido: bytes) -> Iterator[Fila]:
    """El CSV del release → filas listas para `COPY`, de a una. Lanza `IndiceInvalidoError`.

    Generador y no lista: el plan de Render tiene 512 MB y el índice entero en
    tuplas de Python pesa del orden de 100 MB. Así `COPY` lo consume mientras
    se descomprime.
    """
    try:
        texto = io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(comprimido)), encoding="utf-8")
        lector = csv.reader(texto)
        encabezado = next(lector, None)
    except (OSError, EOFError, UnicodeDecodeError) as exc:
        raise IndiceInvalidoError(f"no es un gzip de texto: {exc}") from exc
    if tuple(encabezado or ()) != COLUMNAS:
        raise IndiceInvalidoError(f"columnas inesperadas: {encabezado!r}")
    try:
        for numero, fila in enumerate(lector, start=2):
            if len(fila) != len(COLUMNAS):
                raise IndiceInvalidoError(f"la fila {numero} tiene {len(fila)} columnas")
            nodo, lat, lon, nombre_a, nombre_b, ultima_a, ultima_b, comunas = fila
            try:
                yield (
                    int(nodo),
                    float(lat),
                    float(lon),
                    nombre_a[:200],
                    nombre_b[:200],
                    ultima_a[:80],
                    ultima_b[:80],
                    [c[:40] for c in comunas.split("|") if c],
                )
            except ValueError as exc:
                raise IndiceInvalidoError(f"la fila {numero} no se puede leer: {exc}") from exc
    except (OSError, EOFError, UnicodeDecodeError) as exc:
        raise IndiceInvalidoError(f"el gzip está cortado: {exc}") from exc


async def version_cargada(session: AsyncSession) -> str | None:
    resultado = await session.execute(
        text(f"SELECT version FROM {settings.DB_SCHEMA}.osm_indice WHERE id = 1")
    )
    return resultado.scalar_one_or_none()


async def reemplazar(
    session: AsyncSession,
    filas: Iterable[Fila],
    *,
    version: str,
    datos_de: str | None,
    minimo: int = 0,
) -> int:
    """Reemplaza el índice entero, en una transacción. Devuelve las filas cargadas.

    Lanza `IndiceInvalidoError` (y no toca la tabla) si llegan menos de `minimo`.
    """
    esquema = settings.DB_SCHEMA
    conexion = await session.connection()
    crudo = (await conexion.get_raw_connection()).driver_connection
    # `COPY` no sabe de `geometry`: se copia a una tabla temporal con lat/lon y
    # el punto se arma con PostGIS al pasarla a la tabla de verdad.
    await session.execute(
        text(
            "CREATE TEMP TABLE osm_cruces_nuevo ("
            "nodo BIGINT, lat DOUBLE PRECISION, lon DOUBLE PRECISION, "
            "nombre_a VARCHAR(200), nombre_b VARCHAR(200), "
            "ultima_a VARCHAR(80), ultima_b VARCHAR(80), comunas VARCHAR(40)[]"
            ") ON COMMIT DROP"
        )
    )
    await crudo.copy_records_to_table("osm_cruces_nuevo", records=filas, columns=list(COLUMNAS))
    total = int(
        (await session.execute(text("SELECT count(*) FROM osm_cruces_nuevo"))).scalar_one()
    )
    if total < minimo:
        raise IndiceInvalidoError(f"{total} filas, menos que el mínimo {minimo}")
    await session.execute(text(f"TRUNCATE {esquema}.osm_cruces"))
    await session.execute(
        text(
            f"INSERT INTO {esquema}.osm_cruces "
            "(nodo, nombre_a, nombre_b, ultima_a, ultima_b, comunas, geom) "
            "SELECT nodo, nombre_a, nombre_b, ultima_a, ultima_b, comunas, "
            "ST_SetSRID(ST_MakePoint(lon, lat), 4326) FROM osm_cruces_nuevo"
        )
    )
    fecha = None
    if datos_de:
        try:
            fecha = datetime.fromisoformat(datos_de.replace("Z", "+00:00"))
        except ValueError:
            fecha = None
    await session.execute(
        text(
            f"INSERT INTO {esquema}.osm_indice (id, version, datos_de, cargado_en, filas) "
            "VALUES (1, :version, :datos_de, :ahora, :filas) "
            "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, "
            "datos_de = EXCLUDED.datos_de, cargado_en = EXCLUDED.cargado_en, "
            "filas = EXCLUDED.filas"
        ),
        {"version": version, "datos_de": fecha, "ahora": datetime.now(UTC), "filas": total},
    )
    await session.commit()
    return total


async def refrescar(session: AsyncSession, *, client: httpx.AsyncClient | None = None) -> str:
    """Carga el índice del release si no es el que ya está. Devuelve el estado."""
    propio = client is None
    client = client or httpx.AsyncClient(
        timeout=60.0, follow_redirects=True, headers={"User-Agent": nominatim_user_agent()}
    )
    try:
        respuesta = await client.get(url_de(settings.OSM_CRUCES_META))
        if respuesta.status_code == 404:
            return SIN_RELEASE
        respuesta.raise_for_status()
        try:
            leida = version_de(respuesta.json())
        except ValueError:
            leida = None
        if leida is None:
            raise IndiceInvalidoError("el JSON del release no trae generado_en")
        version, datos_de = leida
        if await version_cargada(session) == version:
            return AL_DIA

        archivo = await client.get(url_de(settings.OSM_CRUCES_ASSET))
        archivo.raise_for_status()
        if len(archivo.content) > settings.OSM_CRUCES_MAX_BYTES:
            raise IndiceInvalidoError(f"pesa {len(archivo.content)} bytes")
        cargadas = await reemplazar(
            session,
            iterar_filas(archivo.content),
            version=version,
            datos_de=datos_de,
            minimo=settings.OSM_CRUCES_MIN_FILAS,
        )
        logger.info(
            "índice de cruces cargado",
            extra={"version": version, "filas": cargadas, "datos_de": datos_de},
        )
        return CARGADO
    except IndiceInvalidoError as exc:
        await session.rollback()
        logger.error("índice de cruces rechazado", extra={"motivo": str(exc)})
        return RECHAZADO
    finally:
        if propio:
            await client.aclose()


__all__ = [
    "AL_DIA",
    "CARGADO",
    "COLUMNAS",
    "RECHAZADO",
    "SIN_RELEASE",
    "Fila",
    "IndiceInvalidoError",
    "iterar_filas",
    "reemplazar",
    "refrescar",
    "url_de",
    "version_cargada",
    "version_de",
]
