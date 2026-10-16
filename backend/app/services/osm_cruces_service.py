"""Carga el índice de cruces de OSM desde el release mensual del repositorio.

El índice lo arma `scripts/osm_cruces.py` en GitHub Actions
(`.github/workflows/osm-cruces.yml`) y lo publica como release
`osm-cruces-AAAA-MM-DD` con el archivo `osm_cruces.csv.gz`. Los workers
revisan cada `OSM_CRUCES_POLL_SECONDS` si hay uno más nuevo que el cargado
(`osm_indice.version`) y, si lo hay, lo bajan y reemplazan la tabla entera.

Así no hace falta darle a GitHub la contraseña de la base: el workflow sólo
publica un archivo público, y quien escribe en la base es el backend, que ya
tiene la conexión.

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

PREFIJO_RELEASE = "osm-cruces-"
COLUMNAS = ("nodo", "lat", "lon", "nombre_a", "nombre_b", "ultima_a", "ultima_b", "comunas")

#: Estados de una revisión, para el log.
AL_DIA = "al_dia"
CARGADO = "cargado"
SIN_RELEASE = "sin_release"
RECHAZADO = "rechazado"


class IndiceInvalidoError(ValueError):
    """El archivo del release no sirve: columnas, filas o tamaño."""


def ultimo_release(releases: Any) -> tuple[str, str, str | None] | None:
    """`(etiqueta, url_del_archivo, fecha_de_los_datos)` del release más nuevo."""
    candidatos = []
    for release in releases if isinstance(releases, list) else []:
        etiqueta = str(release.get("tag_name") or "")
        if not etiqueta.startswith(PREFIJO_RELEASE) or release.get("draft") or release.get(
            "prerelease"
        ):
            continue
        archivo = next(
            (
                a
                for a in release.get("assets") or []
                if a.get("name") == settings.OSM_CRUCES_ASSET and a.get("browser_download_url")
            ),
            None,
        )
        if archivo is None:
            continue
        candidatos.append((etiqueta, str(archivo["browser_download_url"]), release.get("body")))
    if not candidatos:
        return None
    # Las etiquetas llevan la fecha ISO: el orden de texto es el cronológico.
    etiqueta, url, cuerpo = max(candidatos, key=lambda c: c[0])
    return etiqueta, url, _fecha_de_datos(cuerpo)


def _fecha_de_datos(cuerpo: Any) -> str | None:
    """La línea `datos_de: …` que el workflow escribe en la descripción."""
    for linea in str(cuerpo or "").splitlines():
        if linea.strip().lower().startswith("datos_de:"):
            valor = linea.split(":", 1)[1].strip()
            return valor or None
    return None


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
    """Carga el release más nuevo si no es el que ya está. Devuelve el estado."""
    propio = client is None
    client = client or httpx.AsyncClient(
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": nominatim_user_agent(), "Accept": "application/vnd.github+json"},
    )
    try:
        respuesta = await client.get(
            f"https://api.github.com/repos/{settings.OSM_CRUCES_REPO}/releases",
            params={"per_page": 20},
        )
        respuesta.raise_for_status()
        ultimo = ultimo_release(respuesta.json())
        if ultimo is None:
            return SIN_RELEASE
        version, url, datos_de = ultimo
        if await version_cargada(session) == version:
            return AL_DIA

        archivo = await client.get(url, headers={"Accept": "application/octet-stream"})
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
    "PREFIJO_RELEASE",
    "RECHAZADO",
    "SIN_RELEASE",
    "Fila",
    "IndiceInvalidoError",
    "iterar_filas",
    "reemplazar",
    "refrescar",
    "ultimo_release",
    "version_cargada",
]
