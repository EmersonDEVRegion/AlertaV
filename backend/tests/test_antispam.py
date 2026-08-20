"""Las dos defensas anti-spam del reporte ciudadano.

Están escritas juntas porque se sostienen mutuamente y ninguna basta sola:

* El **límite por IP** frena a quien insiste. No frena a quien manda un solo
  reporte falso, ni a quien rota de IP.
* El **ciclo de vida por confianza** hace que cualquier reporte sin corroborar
  muera a los pocos minutos. Es la que de verdad protege el mapa, y la que
  también cubre el caso que el límite por IP no puede ver.

El riesgo de la segunda es el opuesto al de la primera: si se pasa de estricta,
mata reportes ciudadanos legítimos que simplemente fueron los primeros en llegar.
Por eso la mayoría de estos tests verifican lo que **no** debe descartarse.
"""

from __future__ import annotations

import re
import struct
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from app.core.ratelimit import RateLimiter, client_ip
from app.models.enums import EventSource, IncidentStatus
from app.repositories.incident_repository import IncidentRepository

AHORA = datetime(2026, 8, 19, 14, 0, tzinfo=UTC)


# --- Nivel 1: límite por IP --------------------------------------------------


def test_el_primer_reporte_pasa():
    limiter = RateLimiter(interval_seconds=600)
    assert limiter.check("1.2.3.4").allowed is True


def test_el_segundo_reporte_de_la_misma_ip_se_rechaza():
    limiter = RateLimiter(interval_seconds=600)
    limiter.check("1.2.3.4")

    decision = limiter.check("1.2.3.4")

    assert decision.allowed is False
    assert 0 < decision.retry_after_seconds <= 601, "Retry-After debe ser accionable"


def test_ips_distintas_no_se_estorban():
    """Dos personas viendo el mismo incendio desde casas distintas."""
    limiter = RateLimiter(interval_seconds=600)
    assert limiter.check("1.2.3.4").allowed is True
    assert limiter.check("5.6.7.8").allowed is True


def test_la_ventana_se_reabre_al_expirar():
    limiter = RateLimiter(interval_seconds=0.05)
    assert limiter.check("1.2.3.4").allowed is True
    assert limiter.check("1.2.3.4").allowed is False
    time.sleep(0.06)
    assert limiter.check("1.2.3.4").allowed is True


def test_un_rechazo_no_reinicia_la_ventana():
    """Insistir no puede alargar el castigo indefinidamente.

    Si cada intento fallido reiniciara el contador, alguien que reintenta cada
    segundo quedaría bloqueado para siempre — incluso de buena fe, con un botón
    que responde lento.
    """
    limiter = RateLimiter(interval_seconds=0.10)
    limiter.check("1.2.3.4")
    time.sleep(0.05)
    assert limiter.check("1.2.3.4").allowed is False  # rechazo a mitad de ventana
    time.sleep(0.06)
    assert limiter.check("1.2.3.4").allowed is True, "el rechazo extendió la ventana"


def test_consume_false_no_gasta_la_ventana():
    limiter = RateLimiter(interval_seconds=600)
    assert limiter.check("1.2.3.4", consume=False).allowed is True
    assert limiter.check("1.2.3.4").allowed is True


def test_intervalo_cero_desactiva_el_limite():
    """Lo que quieren los tests de endpoint y una demo local."""
    limiter = RateLimiter(interval_seconds=0)
    for _ in range(5):
        assert limiter.check("1.2.3.4").allowed is True


def test_el_limitador_se_poda_y_no_crece_sin_fin():
    """Sin poda, el diccionario crece con cada IP vista: una fuga lenta."""
    limiter = RateLimiter(interval_seconds=0.01)
    for i in range(600):
        limiter.check(f"10.0.0.{i % 250}")
        if i % 100 == 0:
            time.sleep(0.011)

    assert len(limiter._last_seen) < 600


# --- Identificación del cliente ----------------------------------------------


@pytest.mark.parametrize(
    ("forwarded", "real", "peer", "esperado"),
    [
        # El cliente es el de más a la izquierda; a la derecha van los proxies.
        ("203.0.113.9, 10.0.0.1, 10.0.0.2", None, "10.0.0.2", "203.0.113.9"),
        ("  203.0.113.9  ", None, None, "203.0.113.9"),
        (None, "203.0.113.9", "10.0.0.2", "203.0.113.9"),
        (None, None, "203.0.113.9", "203.0.113.9"),
        ("", "", "", "desconocida"),
        (None, None, None, "desconocida"),
    ],
)
def test_client_ip_respeta_la_cadena_de_proxies(forwarded, real, peer, esperado):
    assert client_ip(forwarded_for=forwarded, real_ip=real, peer=peer) == esperado


def test_client_ip_nunca_devuelve_vacio():
    """Una clave vacía agruparía a todo el mundo bajo el mismo cupo."""
    assert client_ip(forwarded_for=" , , ", real_ip=None, peer=None) == "desconocida"


# --- Nivel 2: ciclo de vida por confianza ------------------------------------


def sql_de_caducidad(**overrides) -> str:
    """SQL que genera `expire_uncorroborated_citizen`, para poder auditarlo.

    Se inspecciona la consulta en vez de ejecutarla porque las condiciones —y no
    el resultado— son lo que hay que fijar: cada `WHERE` es una guarda contra un
    modo de fallo distinto, y perder cualquiera de ellas rompería la garantía sin
    que ningún test de integración lo notara necesariamente.
    """
    import asyncio
    from unittest.mock import MagicMock

    capturado: dict = {}

    async def execute(stmt, *a, **k):
        capturado["stmt"] = stmt
        resultado = MagicMock()
        resultado.rowcount = 0
        return resultado

    session = MagicMock()
    session.execute = execute
    repo = IncidentRepository(session)

    kwargs = {
        "older_than": AHORA - timedelta(minutes=5),
        "max_confidence": 0.40,
        **overrides,
    }
    asyncio.run(repo.expire_uncorroborated_citizen(**kwargs))
    return str(
        capturado["stmt"].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_la_caducidad_exige_que_no_haya_ninguna_fuente_ajena():
    """La guarda que impide tocar a las fuentes oficiales.

    `sources <@ ARRAY['citizen']` significa "todo lo que hay está contenido en".
    Basta un píxel de FIRMS, un incendio de CONAF o un reporte de Waze para que
    el incidente quede fuera de la consulta.
    """
    sql = sql_de_caducidad()
    assert "sources <@ ARRAY['citizen']" in sql


def test_la_caducidad_nunca_toca_lo_confirmado_en_terreno():
    """Segundo candado, independiente del primero."""
    sql = sql_de_caducidad()
    assert "is_official_confirmed IS false" in sql


def test_la_caducidad_mide_la_edad_desde_el_nacimiento():
    """`first_seen_at` y no `last_seen_at`, y la diferencia es el ataque.

    Un spammer que repite el mismo reporte cada cuatro minutos refrescaría
    `last_seen_at` indefinidamente y su incidente no moriría nunca. Con
    `first_seen_at` la ventana empieza a correr al nacer y no se reinicia.
    """
    sql = sql_de_caducidad()
    assert "first_seen_at <" in sql
    assert "last_seen_at" not in sql


def test_la_caducidad_respeta_el_umbral_de_confianza():
    sql = sql_de_caducidad(max_confidence=0.40)
    assert "confidence <= 0.4" in sql


def real(valor: float) -> float:
    """El valor tal como vuelve de una columna `REAL` de PostgreSQL.

    float4 tiene 24 bits de mantisa; el ida y vuelta por `struct` reproduce
    exactamente el redondeo que hace la base al guardar un float8.
    """
    return struct.unpack("f", struct.pack("f", valor))[0]


def umbral_del_sql(sql: str) -> float:
    coincidencia = re.search(r"confidence <= ([0-9.eE+-]+)", sql)
    assert coincidencia is not None, f"no hay guarda de confianza en:\n{sql}"
    return float(coincidencia.group(1))


def test_la_caducidad_tolera_la_precision_de_la_columna():
    """El bug de producción: los reportes no morían nunca.

    `incidents.confidence` es `REAL`. El motor escribe 0.40 en float8 y la
    columna devuelve 0.4000000059604645, **estrictamente mayor** que 0.40: el
    `confidence <= 0.40` literal no matcheaba con el mismo número que el motor
    acababa de escribir, el UPDATE afectaba 0 filas y no había error que mirar.

    Este test fija el fallo como número, no como cadena: si alguien vuelve a
    comparar contra el umbral pelado, falla acá y no en el mapa.
    """
    guardado = real(0.40)
    assert guardado > 0.40, "premisa del test: float4 redondea 0.40 hacia arriba"

    assert umbral_del_sql(sql_de_caducidad(max_confidence=0.40)) >= guardado


def test_la_tolerancia_no_afloja_la_regla():
    """La otra mitad: una tolerancia demasiado ancha descartaría reportes buenos.

    Con umbral 0.40, un incidente de 0.41 —ciudadano con foto más una segunda
    señal— tiene que sobrevivir. El margen vive muy por debajo de la resolución
    de la política, que redondea la confianza a 4 decimales.
    """
    umbral = umbral_del_sql(sql_de_caducidad(max_confidence=0.40))
    assert umbral < 0.4001
    assert real(0.41) > umbral, "0.41 quedaría dentro del descarte"


@pytest.mark.parametrize("valor", [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.60])
def test_el_umbral_siempre_alcanza_al_valor_que_se_escribio(valor):
    """Cualquier umbral tiene que incluir su propio valor, venga como venga.

    float4 redondea unas veces hacia arriba (0.40, 0.30, 0.60) y otras hacia
    abajo (0.35, 0.45). La guarda no puede depender de cuál le tocó.
    """
    assert umbral_del_sql(sql_de_caducidad(max_confidence=valor)) >= real(valor)


def test_la_caducidad_marca_dismissed_y_no_stale():
    """Son juicios distintos y confundirlos engaña al operador.

    `stale` = "dejaron de llegar señales" (un incendio real que el satélite ya no
    ve). `dismissed` = "nunca hubo evidencia suficiente".
    """
    sql = sql_de_caducidad()
    assert "SET status='DISMISSED'" in sql.upper().replace('"', "").replace(
        "SET STATUS=", "SET status="
    ) or "dismissed" in sql.lower()
    assert "stale" not in sql.lower()


def test_la_caducidad_solo_alcanza_incidentes_abiertos():
    """Un `merged` o un `dismissed` ya son decisiones tomadas."""
    sql = sql_de_caducidad()
    assert "status IN" in sql
    assert "ACTIVE" in sql.upper()


def test_las_fuentes_consideradas_ciudadanas_son_configurables():
    """Si mañana entra otra fuente sin verificar, se suma sin tocar el SQL."""
    sql = sql_de_caducidad(
        citizen_sources=[EventSource.CITIZEN, EventSource.SOCIAL_MEDIA]
    )
    assert "ARRAY['citizen', 'social_media']" in sql


def test_dismissed_no_es_un_estado_abierto():
    """Lo que hace que el incidente desaparezca del mapa.

    `/incidents` filtra por `OPEN_INCIDENT_STATUSES`; si `dismissed` estuviera
    ahí, el descarte no cambiaría nada de lo que ve el usuario.
    """
    from app.models.enums import OPEN_INCIDENT_STATUSES

    assert IncidentStatus.DISMISSED not in OPEN_INCIDENT_STATUSES
