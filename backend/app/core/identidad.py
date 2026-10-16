"""Cómo se presenta AlertaV ante cada servicio que consulta.

Quien opera cualquiera de los servidores que consulta AlertaV tiene que poder
escribirle a alguien antes de bloquear la IP de Render, que es la única que
tiene este backend para todo. Por eso hay una sola identidad, y ningún cliente HTTP sale con el
`User-Agent` por defecto de httpx. Se arma con:

* `CONTACT_URL` — el repositorio real;
* un correo de contacto — `CONTACT_EMAIL`, o si está vacío el `mailto:` de
  `VAPID_SUBJECT`, o el correo que traiga `NOMINATIM_USER_AGENT` (ver
  `Settings.contacto_email`): Render ya tiene los dos últimos configurados, así
  que no hace falta una variable más;
* y, para los portales con WAF, el navegador delante (`navegador=True`), que es
  lo que un WordPress con un plugin de seguridad exige para no devolver 403.

**Sin tildes, siempre.** Las cabeceras HTTP van en latin-1 y httpx rechaza
cualquier valor no ASCII al construir el cliente, con un error que no menciona
la cabecera. `user_agent()` normaliza lo que recibe.

Cada `*_USER_AGENT` de la configuración sigue existiendo como anulación: vacío
(el valor por defecto) usa esta identidad; con valor, manda ese valor.
"""

from __future__ import annotations

import unicodedata

from app.core.config import settings

#: Versión que se anuncia. No sigue a `pyproject`: cambiarla cambia la firma
#: que los operadores ajenos pueden tener en una lista blanca.
VERSION = "1.0"

#: Navegador que anteceden los scrapers de portales con WAF. Uno solo para todo
#: el backend: siete copias de la cadena envejecían a ritmos distintos.
NAVEGADOR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def solo_ascii(texto: str) -> str:
    """«Región de Valparaíso» → «Region de Valparaiso». Lo demás se descarta."""
    plano = unicodedata.normalize("NFKD", texto)
    return plano.encode("ascii", "ignore").decode("ascii")


def user_agent(proposito: str | None = None, *, navegador: bool = False) -> str:
    """`AlertaV/1.0 (+<repo>; <correo>; <propósito>)`, con navegador si hace falta.

    `proposito` dice qué se está consultando («cortes de agua»): un operador que
    ve la cabecera en su log sabe sin preguntar por qué le pegamos.
    """
    detalles: list[str] = []
    url = settings.CONTACT_URL.strip()
    if url:
        detalles.append(f"+{url}")
    correo = settings.contacto_email
    if correo:
        detalles.append(correo)
    if proposito and proposito.strip():
        detalles.append(proposito.strip())

    propio = f"AlertaV/{VERSION}"
    if detalles:
        propio = f"{propio} ({'; '.join(detalles)})"
    return solo_ascii(f"{NAVEGADOR} {propio}" if navegador else propio)


def user_agent_o(anulacion: str, proposito: str | None = None, *, navegador: bool = False) -> str:
    """La anulación de la configuración si tiene valor; si no, `user_agent()`."""
    valor = (anulacion or "").strip()
    return valor if valor else user_agent(proposito, navegador=navegador)


def nominatim_user_agent() -> str:
    """El de Nominatim: sin navegador, con contacto. Es su política de uso."""
    return user_agent_o(settings.NOMINATIM_USER_AGENT)


__all__ = [
    "NAVEGADOR",
    "VERSION",
    "nominatim_user_agent",
    "solo_ascii",
    "user_agent",
    "user_agent_o",
]
