"""Normalización de texto para comparar nombres y frases en español."""

from __future__ import annotations

import unicodedata


def sin_tildes(texto: object) -> str:
    """Minúsculas y sin tildes: «Valparaíso» → «valparaiso». Conserva los espacios."""
    if texto is None:
        return ""
    descompuesto = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")
