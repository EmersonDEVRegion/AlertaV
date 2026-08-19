"""Limitador de tasa por cliente, en memoria del proceso.

Qué resuelve y qué no
---------------------
Resuelve el abuso casual: la persona que aprieta "enviar" seis veces porque no
vio el acuse, y el que descubre el botón y reporta diez incendios inventados en
un minuto. Contra eso alcanza y sobra.

**No resuelve un ataque decidido.** Quien quiera saltárselo tiene dos caminos
abiertos y conviene tenerlos escritos antes de que alguien los descubra:

* **Rotar de IP.** Una red móvil cambia de salida NAT sola; una botnet o un pool
  de proxies lo hace a voluntad. El límite por IP no distingue eso.
* **Compartir IP legítimamente.** El reverso del mismo problema y el que más
  duele: una universidad, una oficina o un CGNAT de operador móvil salen todos
  por la misma dirección. Diez personas viendo el mismo incendio desde el mismo
  edificio son diez reportes válidos, y este limitador deja pasar uno.

Ese segundo caso es la razón de que el techo del ciclo de vida por confianza
—el descarte a los 5 minutos sin corroborar— sea la defensa principal y ésta
sólo la primera línea. Un reporte falso que pasa el limitador muere igual; un
reporte válido bloqueado por compartir IP se pierde para siempre.

Por qué en memoria
------------------
Porque hoy hay **un solo contenedor**. El estado vive en un diccionario del
proceso y eso es correcto mientras la topología sea la de `app/workers.py`: un
uvicorn, un event loop. Con dos réplicas el límite se volvería "1 reporte cada
10 minutos por IP **y por réplica**", que es silenciosamente el doble. Está
anotado en el docstring de `RateLimiter.check` y es lo primero que habría que
mover a Redis o a una tabla si el servicio crece.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Cada cuántas comprobaciones se barren las entradas vencidas. Sin poda, el
#: diccionario crece con cada IP que haya reportado alguna vez: una fuga lenta
#: pero segura en un proceso que corre semanas.
_PRUNE_EVERY = 256


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Resultado de una comprobación."""

    allowed: bool
    #: Segundos que faltan para poder reintentar. 0 si está permitido. Alimenta
    #: la cabecera `Retry-After`, que es lo que convierte un 429 opaco en un
    #: mensaje que el cliente puede mostrar.
    retry_after_seconds: int = 0


class RateLimiter:
    """Ventana fija por clave: una acción cada `interval_seconds`.

    Ventana fija y no deslizante a propósito. Para un límite de 1 evento por
    ventana las dos son equivalentes, y la fija se implementa con un solo
    timestamp por clave en vez de una lista — con miles de IPs, esa diferencia
    es la que decide si el limitador cabe en los 512 MB de la instancia.

    Usa `time.monotonic()`: un ajuste de reloj del sistema (NTP corrigiendo
    hacia atrás) haría que un reloj de pared retrocediera y la ventana se
    reabriera antes de tiempo.
    """

    def __init__(self, *, interval_seconds: float) -> None:
        self.interval_seconds = max(0.0, interval_seconds)
        self._last_seen: dict[str, float] = {}
        self._checks_since_prune = 0

    def check(self, key: str, *, consume: bool = True) -> RateLimitDecision:
        """¿Puede `key` actuar ahora?

        `consume=False` permite consultar sin gastar la ventana, que es lo que
        hace falta si algún día se quiere avisar "te quedan N segundos" sin
        penalizar la consulta.

        No es seguro entre procesos ni entre hilos: asume el event loop único de
        este servicio. Ver el docstring del módulo.
        """
        if self.interval_seconds <= 0:
            return RateLimitDecision(allowed=True)

        now = time.monotonic()
        self._maybe_prune(now)

        last = self._last_seen.get(key)
        if last is not None:
            elapsed = now - last
            if elapsed < self.interval_seconds:
                return RateLimitDecision(
                    allowed=False,
                    # Se redondea hacia arriba: decirle a alguien que espere 0
                    # segundos y volver a rechazarlo es peor que pedirle uno de más.
                    retry_after_seconds=max(
                        1, int(self.interval_seconds - elapsed) + 1
                    ),
                )

        if consume:
            self._last_seen[key] = now
        return RateLimitDecision(allowed=True)

    def reset(self, key: str | None = None) -> None:
        """Olvida una clave, o todas. Para pruebas y para soporte."""
        if key is None:
            self._last_seen.clear()
        else:
            self._last_seen.pop(key, None)

    def _maybe_prune(self, now: float) -> None:
        self._checks_since_prune += 1
        if self._checks_since_prune < _PRUNE_EVERY:
            return

        self._checks_since_prune = 0
        cutoff = now - self.interval_seconds
        vencidas = [key for key, seen in self._last_seen.items() if seen < cutoff]
        for key in vencidas:
            del self._last_seen[key]

        if vencidas:
            logger.debug(
                "limitador podado",
                extra={"eliminadas": len(vencidas), "vigentes": len(self._last_seen)},
            )


def client_ip(
    *, forwarded_for: str | None, real_ip: str | None, peer: str | None
) -> str:
    """Dirección del cliente detrás del proxy de la plataforma.

    Se toma el **primer** elemento de `X-Forwarded-For`. La cabecera es una lista
    en la que cada proxy anexa a la derecha, así que el cliente original es el de
    más a la izquierda y los de la derecha son la cadena de intermediarios.

    Aquí hay una advertencia que merece estar escrita: **esa cabecera la puede
    falsificar cualquiera**. Un cliente que mande su propio `X-Forwarded-For`
    inyecta un valor arbitrario a la izquierda y se salta el límite cambiándolo
    en cada petición. Sólo es fiable porque el edge de la plataforma —Koyeb o
    Render— reescribe la cabecera antes de que llegue acá; si algún día el
    servicio quedara expuesto directamente a internet, este valor pasaría a ser
    una entrada del usuario y habría que dejar de confiar en él.

    Cae a `X-Real-IP` y luego a la dirección del socket. Cuando no hay ninguna
    —tests, llamadas locales— devuelve un centinela en vez de `None` para que el
    limitador nunca reciba una clave vacía que agrupe a todo el mundo.
    """
    if forwarded_for:
        primera = forwarded_for.split(",")[0].strip()
        if primera:
            return primera
    if real_ip and real_ip.strip():
        return real_ip.strip()
    if peer and peer.strip():
        return peer.strip()
    return "desconocida"


__all__ = ["RateLimitDecision", "RateLimiter", "client_ip"]
