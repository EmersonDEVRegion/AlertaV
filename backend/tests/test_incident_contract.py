"""Contrato de salida de `/incidents`: `family` y etiquetas por familia.

Estos tests existen porque el frontend tuvo que trabajar alrededor de dos
defectos, y ninguno de los dos fallaba: la API simplemente no mandaba `family`
—obligando a replicar la tabla— y rotulaba "Incendio confirmado" cualquier cosa
confirmada, incluido un choque en la Ruta 68. Un contrato que se puede violar sin
que nada se rompa es un contrato que hay que fijar con tests.

La regla que ordena el archivo: **la etiqueta nunca puede nombrar un fenómeno
distinto del que ocurrió**. Puede ser vaga ("Emergencia confirmada") cuando la
familia es desconocida; no puede ser falsa.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.enums import (
    CONFIRMED_LABEL_BY_FAMILY,
    INCIDENT_FAMILY,
    ConfidenceLevel,
    IncidentStatus,
    IncidentType,
    family_of_incident,
    level_for,
    style_for,
)
from app.schemas.incident import IncidentRead
from app.services.correlation.confidence import score
from app.services.incident_service import IncidentService

T0 = datetime(2026, 8, 19, 14, 0, tzinfo=UTC)


class FakeIncident:
    """Lo mínimo que `IncidentRead` y `to_geojson` leen de un incidente.

    Evita depender de la base: lo que se verifica es el contrato de salida, no
    el ORM.
    """

    def __init__(
        self,
        incident_type: IncidentType,
        *,
        confidence: float = 0.90,
        breakdown: dict | None = None,
        is_official_confirmed: bool = False,
    ) -> None:
        self.code = "INC-2026-00142"
        self.public_id = uuid4()
        self.type = incident_type
        self.status = IncidentStatus.ACTIVE
        self.lat = -33.0458
        self.lon = -71.6197
        self.confidence = confidence
        self.is_official_confirmed = is_official_confirmed
        self.alert_confidence = 0.0
        self.alert_level = None
        self.title = "Título"
        self.commune = "Valparaíso"
        self.province = "Valparaíso"
        self.event_count = 3
        self.source_count = 2
        self.sources = ["waze", "bomberos"]
        self.first_seen_at = T0
        self.last_seen_at = T0
        self.resolved_at = None
        self.correlated_at = T0
        self.confidence_breakdown = breakdown if breakdown is not None else {}


def leer(incident_type: IncidentType, **kwargs) -> IncidentRead:
    return IncidentRead.model_validate(FakeIncident(incident_type, **kwargs))


# --- 1. `family` en la respuesta ---------------------------------------------


@pytest.mark.parametrize(
    ("incident_type", "familia"),
    [
        (IncidentType.WILDFIRE, "fire"),
        (IncidentType.STRUCTURAL_FIRE, "fire"),
        (IncidentType.POSSIBLE_FIRE, "fire"),
        (IncidentType.ACCIDENT, "traffic"),
        (IncidentType.FLOOD, "hydro"),
        (IncidentType.LANDSLIDE, "hydro"),
        (IncidentType.RESCUE, "other"),
        (IncidentType.OTHER, "other"),
    ],
)
def test_incident_read_expone_family(incident_type, familia):
    assert leer(incident_type).family == familia


def test_family_cubre_todos_los_tipos_de_incidente():
    """Ningún `IncidentType` puede salir sin familia.

    Si alguien agrega un tipo y olvida mapearlo, el cliente recibiría `other` y
    lo pintaría en la capa equivocada sin que nada avise. Esto avisa.
    """
    for incident_type in IncidentType:
        assert incident_type in INCIDENT_FAMILY, (
            f"{incident_type.value} no está en INCIDENT_FAMILY"
        )
        assert leer(incident_type).family == INCIDENT_FAMILY[incident_type]


def test_family_viaja_en_el_json_serializado():
    """No basta con que sea una propiedad: tiene que salir en el payload."""
    payload = leer(IncidentType.ACCIDENT).model_dump(mode="json")
    assert payload["family"] == "traffic"
    assert payload["level_label"] == "Accidente confirmado"


# --- 2. Etiquetas dinámicas --------------------------------------------------


@pytest.mark.parametrize(
    ("incident_type", "etiqueta"),
    [
        (IncidentType.WILDFIRE, "Incendio confirmado"),
        (IncidentType.STRUCTURAL_FIRE, "Incendio confirmado"),
        (IncidentType.POSSIBLE_FIRE, "Incendio confirmado"),
        (IncidentType.ACCIDENT, "Accidente confirmado"),
        (IncidentType.FLOOD, "Emergencia confirmada"),
        (IncidentType.LANDSLIDE, "Emergencia confirmada"),
        (IncidentType.RESCUE, "Emergencia confirmada"),
        (IncidentType.OTHER, "Emergencia confirmada"),
    ],
)
def test_level_label_confirmado_usa_el_sustantivo_de_su_familia(
    incident_type, etiqueta
):
    assert leer(incident_type, confidence=0.90).level_label == etiqueta


def test_un_accidente_confirmado_jamas_se_rotula_incendio():
    """El defecto exacto que reportó el equipo de frontend."""
    etiqueta = leer(IncidentType.ACCIDENT, confidence=0.95).level_label
    assert "Incendio" not in etiqueta
    assert etiqueta == "Accidente confirmado"


@pytest.mark.parametrize(
    "incident_type", [IncidentType.WILDFIRE, IncidentType.ACCIDENT, IncidentType.FLOOD]
)
def test_los_tramos_bajos_son_neutros_en_todas_las_familias(incident_type):
    """`UNSAFE` y `POSSIBLE` no nombran el fenómeno, y está bien así.

    Cuando no sabemos si hubo algo, tampoco corresponde afirmar de qué se trata.
    """
    assert leer(incident_type, confidence=0.10).level_label == "Baja confianza"
    assert leer(incident_type, confidence=0.45).level_label == "Posible emergencia"


def test_el_significado_de_possible_deja_de_hablar_de_fuego():
    """El mismo defecto que la etiqueta, en el campo de al lado."""
    assert "fuego" in style_for(ConfidenceLevel.POSSIBLE, "fire").meaning
    assert "fuego" not in style_for(ConfidenceLevel.POSSIBLE, "traffic").meaning
    assert "accidente" in style_for(ConfidenceLevel.POSSIBLE, "traffic").meaning


def test_una_familia_desconocida_degrada_a_generico_no_a_falso():
    """El modo de fallo apunta a lo impreciso, nunca a lo incorrecto."""
    estilo = style_for(ConfidenceLevel.CONFIRMED, "familia_que_no_existe")
    assert estilo.label == "Emergencia confirmada"


def test_el_color_no_depende_de_la_familia():
    """El color comunica certeza; la etiqueta comunica fenómeno.

    Si el color cambiara por familia, el operador necesitaría leer una leyenda
    para saber si algo es urgente.
    """
    colores = {
        style_for(ConfidenceLevel.CONFIRMED, familia).color
        for familia in CONFIRMED_LABEL_BY_FAMILY
    }
    assert len(colores) == 1


def test_level_label_es_coherente_con_confidence_level():
    """Las dos claves tienen que describir el mismo tramo."""
    for confianza in (0.10, 0.29, 0.30, 0.60, 0.61, 0.95):
        leido = leer(IncidentType.ACCIDENT, confidence=confianza)
        assert leido.confidence_level is level_for(confianza)
        assert leido.level_label == style_for(level_for(confianza), "traffic").label


# --- 3. El breakdown y los incidentes antiguos -------------------------------


def test_el_breakdown_nuevo_nace_con_la_familia_correcta():
    from app.models.enums import EventSource, EventType
    from app.services.correlation.confidence import SignalView

    señales = [
        SignalView(
            source=EventSource.BOMBEROS,
            type=EventType.ACCIDENT,
            confidence=1.0,
            timestamp=T0,
        )
    ]
    resultado = score(señales, family="traffic")

    assert resultado.breakdown["family"] == "traffic"
    assert resultado.breakdown["level_label"] == "Accidente confirmado"


def test_score_sin_familia_no_afirma_un_fenomeno_falso():
    from app.models.enums import EventSource, EventType
    from app.services.correlation.confidence import SignalView

    resultado = score(
        [
            SignalView(
                source=EventSource.BOMBEROS,
                type=EventType.ACCIDENT,
                confidence=1.0,
                timestamp=T0,
            )
        ]
    )
    assert resultado.breakdown["level_label"] == "Emergencia confirmada"


def test_la_familia_no_altera_el_numero():
    """Sólo rotula. La aritmética de la confianza es la misma para todos."""
    from app.models.enums import EventSource, EventType
    from app.services.correlation.confidence import SignalView

    señales = [
        SignalView(
            source=EventSource.WAZE,
            type=EventType.ACCIDENT,
            confidence=0.40,
            timestamp=T0,
        )
    ]
    assert score(señales, family="fire").confidence == pytest.approx(
        score(señales, family="traffic").confidence
    )


def test_se_corrige_la_etiqueta_grabada_en_incidentes_antiguos():
    """Los incidentes correlacionados antes del cambio llevan la etiqueta vieja.

    Sin reconciliar, la misma respuesta se contradiría: `level_label` correcto
    arriba y "Incendio confirmado" dentro del breakdown. El cliente no sabría a
    cuál creerle, que es peor que el problema original.
    """
    viejo = {
        "policy_version": "2.0.0",
        "signals": 2,
        "combined": 0.9,
        "level": "confirmed",
        "level_label": "Incendio confirmado",
        "by_source": {"waze": {"signals": 2}},
    }
    leido = leer(IncidentType.ACCIDENT, confidence=0.90, breakdown=viejo)

    assert leido.confidence_breakdown["level_label"] == "Accidente confirmado"
    assert leido.confidence_breakdown["family"] == "traffic"


def test_la_reconciliacion_no_toca_la_auditoria_del_calculo():
    """Se reescribe la presentación; los números son historia y no se falsifican."""
    viejo = {
        "policy_version": "1.0.0",
        "signals": 2,
        "combined": 0.55,
        "ceiling_applied": "unconfirmed_ceiling",
        "combination": "noisy_or",
        "by_source": {"waze": {"signals": 2, "contribution": 0.4}},
        "level_label": "Incendio confirmado",
    }
    leido = leer(IncidentType.ACCIDENT, confidence=0.90, breakdown=viejo)
    reconciliado = leido.confidence_breakdown

    assert reconciliado["policy_version"] == "1.0.0"
    assert reconciliado["combined"] == 0.55
    assert reconciliado["combination"] == "noisy_or"
    assert reconciliado["ceiling_applied"] == "unconfirmed_ceiling"
    assert reconciliado["by_source"] == {"waze": {"signals": 2, "contribution": 0.4}}


def test_la_reconciliacion_no_muta_el_objeto_de_origen():
    """Mutar el dict del ORM marcaría la fila sucia: serializar dispararía UPDATE."""
    original = {"level_label": "Incendio confirmado", "signals": 1}
    incidente = FakeIncident(IncidentType.ACCIDENT, confidence=0.9, breakdown=original)

    leido = IncidentRead.model_validate(incidente)

    assert leido.confidence_breakdown["level_label"] == "Accidente confirmado"
    assert original["level_label"] == "Incendio confirmado", "se mutó el dict de origen"
    assert incidente.confidence_breakdown is original


def test_un_breakdown_vacio_no_se_inventa():
    assert leer(IncidentType.ACCIDENT, breakdown={}).confidence_breakdown == {}


# --- 4. El GeoJSON refleja lo mismo ------------------------------------------


def test_el_geojson_lleva_family_y_level_label():
    coleccion = IncidentService.to_geojson(
        [
            FakeIncident(IncidentType.ACCIDENT, confidence=0.90),
            FakeIncident(IncidentType.WILDFIRE, confidence=0.90),
            FakeIncident(IncidentType.FLOOD, confidence=0.45),
        ]
    )
    propiedades = [feature.properties for feature in coleccion.features]

    assert propiedades[0]["family"] == "traffic"
    assert propiedades[0]["level_label"] == "Accidente confirmado"
    assert propiedades[1]["family"] == "fire"
    assert propiedades[1]["level_label"] == "Incendio confirmado"
    assert propiedades[2]["family"] == "hydro"
    assert propiedades[2]["level_label"] == "Posible emergencia"


def test_json_y_geojson_no_pueden_discrepar():
    """Las dos salidas describen el mismo incidente: deben coincidir.

    Son rutas de código distintas —`IncidentRead` y `to_geojson`— y nada obliga a
    que se mantengan sincronizadas salvo esta comprobación.
    """
    for incident_type in IncidentType:
        for confianza in (0.15, 0.50, 0.95):
            incidente = FakeIncident(incident_type, confidence=confianza)
            leido = IncidentRead.model_validate(incidente)
            propiedades = IncidentService.to_geojson([incidente]).features[0].properties

            assert propiedades["family"] == leido.family
            assert propiedades["level_label"] == leido.level_label
            assert propiedades["confidence_level"] == leido.confidence_level.value


def test_el_geojson_sigue_marcando_la_confirmacion_institucional():
    """Regresión: `family` no puede haber desplazado el flag que ya existía."""
    coleccion = IncidentService.to_geojson(
        [FakeIncident(IncidentType.ACCIDENT, is_official_confirmed=True)]
    )
    assert coleccion.features[0].properties["is_confirmed_incident"] is True


# --- 5. La tabla de familias es única ----------------------------------------


def test_la_api_es_la_unica_fuente_de_la_familia():
    """Lo que expone la API tiene que ser exactamente `INCIDENT_FAMILY`.

    Es la garantía que permite al frontend borrar su copia: si esta salida se
    desviara de la tabla del backend, el cliente que confía en `family` pintaría
    distinto que el motor que correlaciona.
    """
    for incident_type in IncidentType:
        assert leer(incident_type).family == family_of_incident(incident_type)
