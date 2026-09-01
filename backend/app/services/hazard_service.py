"""Servicio de la capa de amenaza sísmica (MASCSN26 del Centro Sismológico Nacional).

Qué sirve, y por qué existe un servicio para leer un archivo
============================================================
La capa de amenaza es un **modelo probabilístico estático**: dice cuánto puede
llegar a acelerar el suelo en cada celda de la región, no qué está temblando
ahora. El CSN lo publica una vez por versión del modelo y lo cambiará cuando
saque el MASCSN27 — probablemente en años. Por eso no lo recolecta el pipeline
de cinco minutos: `scripts/fetch_seismic_hazard.py` lo baja a mano y deja el
GeoJSON recortado en `static/geo/amenaza_sismica_valpo.json`.

Hasta ahora ese archivo se servía con el `StaticFiles` montado en `/static`, y
esa decisión tenía dos agujeros que sólo se ven cuando el artefacto **no está**:

1. **Un 404 desnudo no dice qué hacer.** El artefacto se genera a mano; que
   falte no es un bug del servidor sino un paso de despliegue pendiente. Quien
   lo depure merece leerlo, no deducirlo.
2. **`StaticFiles` no tiene respaldo.** Si una regeneración deja el archivo a
   medias, o un despliegue parcial lo borra, la capa se cae y no hay nada que
   sirva la última versión buena que este proceso ya leyó.

Este servicio los cierra: mantiene en memoria la última copia válida y sólo se
rinde —con un 502 explícito y accionable— cuando no tiene absolutamente nada que
entregar.

Por qué la caché es del proceso y no de Redis
---------------------------------------------
Porque el dato cambia cada varios años y pesa cientos de KB. Una caché
compartida entre réplicas resolvería un problema de coherencia que aquí no
existe: todas las réplicas leen el mismo archivo del mismo `COPY` del Dockerfile.
Lo que sí resuelve la caché en memoria es no volver a `json.loads` cientos de KB
en cada petición, y tener de dónde sacar la respuesta cuando el disco falla.

Por qué 502 y no 404
--------------------
El artefacto es un producto del CSN que este backend intermedia. Que no esté
disponible es un fallo de la fuente ajena —o de la tubería que la trae—, que es
justo lo que `CollectorError` significa en este proyecto y lo que el manejador
de excepciones ya traduce a un 502 con sobre JSON. Un 404 diría «esta ruta no
existe», que es falso y manda a buscar en el lugar equivocado.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.core.exceptions import CollectorError

logger = logging.getLogger(__name__)

#: Ubicación del artefacto. Coincide con `DEFAULT_OUTPUT` de
#: `scripts/fetch_seismic_hazard.py`; si una cambia, la otra también.
HAZARD_ARTIFACT_PATH = (
    Path(__file__).resolve().parents[2] / "static" / "geo" / "amenaza_sismica_valpo.json"
)

#: Instrucción exacta para reponer la capa. Viaja en el mensaje de error porque
#: es la única acción que resuelve el problema.
_REGENERAR = "python -m scripts.fetch_seismic_hazard"


@dataclass(frozen=True, slots=True)
class HazardArtifact:
    """Artefacto listo para servir."""

    payload: dict[str, Any]
    etag: str
    #: Cuándo lo generó el script, si el artefacto lo declara.
    generated_at: str | None
    #: True cuando esta copia salió de la caché porque el disco falló. El
    #: endpoint lo publica en una cabecera: una capa vieja que se sigue viendo
    #: sin avisar es una forma silenciosa de mentir.
    stale: bool


def _looks_usable(payload: object) -> tuple[bool, str]:
    """¿Es esto un artefacto de amenaza servible?

    Se comprueban las mismas tres cosas que valida el frontend, y por el mismo
    motivo que `validate_header` en el script de extracción: un artefacto que
    llega bien formado pero vacío se dibuja como una región sin amenaza, y eso
    es indistinguible de «acá no tiembla» sobre una de las zonas sísmicas más
    activas del planeta. Un artefacto que miente en silencio es peor que no
    tener artefacto.
    """
    if not isinstance(payload, dict):
        return (False, "el artefacto no es un objeto JSON")
    if payload.get("type") != "FeatureCollection":
        return (False, "el artefacto no es un FeatureCollection")
    features = payload.get("features")
    if not isinstance(features, list):
        return (False, "el artefacto no trae una lista de features")
    if not features:
        return (False, "el artefacto no tiene ninguna celda")
    return (True, "")


class SeismicHazardService:
    """Lee el artefacto de amenaza, con respaldo en memoria.

    La caché es de **clase** a propósito: el servicio se instancia por petición
    (es una dependencia de FastAPI) y una caché de instancia no sobreviviría a
    la petición, que es exactamente cuando hace falta.
    """

    _cached: HazardArtifact | None = None
    #: `mtime_ns` del archivo cuando se llenó la caché. Sirve para releer sólo
    #: cuando el archivo cambió de verdad — un `stat` por petición es barato,
    #: un `json.loads` de cientos de KB no.
    _cached_mtime_ns: int | None = None

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or HAZARD_ARTIFACT_PATH

    # -- API pública ----------------------------------------------------------

    def load(self) -> HazardArtifact:
        """Devuelve el artefacto. Nunca lanza una excepción sin traducir.

        Orden de preferencia, de mejor a peor:

        1. el archivo en disco, si está y es legible;
        2. la caché del proceso, marcada como rancia, si el disco falló;
        3. `CollectorError` → 502 con sobre JSON, si no hay ninguna de las dos.
        """
        try:
            stat = self.path.stat()
        except OSError as exc:
            return self._fallback_or_fail(
                f"no se pudo leer {self.path.name} ({type(exc).__name__})"
            )

        cached = type(self)._cached
        if cached is not None and type(self)._cached_mtime_ns == stat.st_mtime_ns:
            # Mismo archivo que la última vez: se sirve la copia ya parseada.
            # `stale=False` aunque venga de memoria — la caché coincide con el
            # disco, que es la definición de fresco.
            return cached

        try:
            crudo = self.path.read_bytes()
            payload = json.loads(crudo)
        except (OSError, json.JSONDecodeError) as exc:
            # El caso realista: una regeneración interrumpida. El script escribe
            # de forma atómica justo para evitarlo, pero un `COPY` de Docker a
            # medias o un volumen montado a mitad de sincronización no pasan por
            # el script.
            return self._fallback_or_fail(
                f"{self.path.name} no se pudo parsear ({type(exc).__name__}: {exc})"
            )

        usable, motivo = _looks_usable(payload)
        if not usable:
            return self._fallback_or_fail(f"{self.path.name} no sirve: {motivo}")

        artifact = HazardArtifact(
            payload=payload,
            etag=f'"{sha256(crudo).hexdigest()[:32]}"',
            generated_at=_generated_at(payload),
            stale=False,
        )
        type(self)._cached = artifact
        type(self)._cached_mtime_ns = stat.st_mtime_ns
        return artifact

    @classmethod
    def reset_cache(cls) -> None:
        """Vacía la caché. Para los tests y para un recargado en caliente."""
        cls._cached = None
        cls._cached_mtime_ns = None

    # -- Interno --------------------------------------------------------------

    def _fallback_or_fail(self, motivo: str) -> HazardArtifact:
        """Última copia buena, o 502 explícito. Nunca un volcado de pila."""
        cached = type(self)._cached
        if cached is not None:
            logger.warning(
                "capa de amenaza sísmica servida desde la caché",
                extra={"motivo": motivo, "ruta": str(self.path)},
            )
            # La caché se conserva: el disco puede recuperarse en la siguiente
            # petición y no hay razón para tirar lo único que queda.
            return HazardArtifact(
                payload=cached.payload,
                etag=cached.etag,
                generated_at=cached.generated_at,
                stale=True,
            )

        logger.error(
            "capa de amenaza sísmica no disponible",
            extra={"motivo": motivo, "ruta": str(self.path)},
        )
        raise CollectorError(
            "La capa de amenaza sísmica no está disponible. Se genera a mano "
            f"desde el CSN con `{_REGENERAR}` y se sirve como artefacto estático.",
            detail={"motivo": motivo, "artefacto": str(self.path), "regenerar": _REGENERAR},
        )


def _generated_at(payload: dict[str, Any]) -> str | None:
    """Marca de generación, si el artefacto la declara en su `metadata`."""
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    valor = metadata.get("generated_at")
    return valor if isinstance(valor, str) else None


__all__ = ["HAZARD_ARTIFACT_PATH", "HazardArtifact", "SeismicHazardService"]
