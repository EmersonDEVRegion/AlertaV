"""Tests del Paso B — correlación por comuna.

Las fixtures salen de las capas reales verificadas en la Fase 2: SENAPRED lista
varias comunas en un solo campo de texto y CONAF trae `comuna` estructurada. Ese
desajuste ES el problema que resuelve este módulo.
"""

from __future__ import annotations

from app.models.enums import IncidentType
from app.services.correlation.communes import (
    LINK_CONFIDENCE_CONTAINED,
    LINK_CONFIDENCE_EXACT,
    LINK_CONFIDENCE_REGIONAL,
    IncidentView,
    build_alert_view,
    extract_commune,
    extract_province,
    is_whole_area,
    match_alerts_to_incidents,
    phenomenon_family,
    split_communes,
)

# Payload real de la capa de alertas vigentes (esquema verificado en 2026-08).
ALERTA_VINA = {
    "Region": "Valparaíso",
    "Alerta": "Alerta Roja",
    "Razon": "Incendio forestal",
    "Comunas": "Viña del Mar",
    "Ambito": "Comunal",
    "Evento": "Incendio Forestal",
    "_alert_level": "roja",
    "_national": False,
}

ALERTA_DOS_COMUNAS = {
    **ALERTA_VINA,
    "Comunas": "Quilpué, Villa Alemana",
    "Razon": "Evacuación preventiva por incendio forestal",
}

ALERTA_NACIONAL = {
    "Region": "Nacional",
    "Alerta": "Alerta Temprana Preventiva",
    "Razon": "Temporada de incendios forestales",
    "Comunas": "Todo el país",
    "Ambito": "Nacional",
    "Evento": "Incendio Forestal",
    "_alert_level": "temprana_preventiva",
    "_national": True,
}

ALERTA_CRECIDA = {
    "Region": "Valparaíso",
    "Alerta": "Alerta Amarilla",
    "Razon": "Crecida",
    "Comunas": "Viña del Mar",
    "Ambito": "Comunal",
    "Evento": "Crecida de río",
    "_alert_level": "amarilla",
    "_national": False,
}

INCENDIO_VINA = IncidentView(
    incident_id=1, commune="Viña del Mar", type=IncidentType.WILDFIRE
)
INCENDIO_VINA_2 = IncidentView(
    incident_id=2, commune="Viña del Mar", type=IncidentType.POSSIBLE_FIRE
)
INCENDIO_QUILPUE = IncidentView(
    incident_id=3, commune="Quilpué", type=IncidentType.WILDFIRE
)
SIN_COMUNA = IncidentView(incident_id=4, commune=None, type=IncidentType.POSSIBLE_FIRE)


def alerta(raw: dict, *, event_id: int = 100, text: str | None = None):
    return build_alert_view(event_id=event_id, raw_data=raw, text=text)


class TestNormalizacionDeCampos:
    def test_separa_varias_comunas_de_un_solo_campo(self) -> None:
        assert split_communes("Quilpué, Villa Alemana") == ("Quilpué", "Villa Alemana")
        assert split_communes("Limache y Olmué") == ("Limache", "Olmué")
        assert split_communes("Casablanca; Curacaví") == ("Casablanca", "Curacaví")
        assert split_communes(None) == ()

    def test_reconoce_los_ambitos_completos(self) -> None:
        assert is_whole_area("Toda la region") is True
        assert is_whole_area("Todo el país") is True
        assert is_whole_area("Viña del Mar") is False

    def test_familia_del_fenomeno(self) -> None:
        assert phenomenon_family("Incendio Forestal") == "fire"
        assert phenomenon_family("Crecida de río") == "hydro"
        assert phenomenon_family("Corte de energía") == "unknown"


class TestExtraccionDeComuna:
    def test_prefiere_la_columna_enriquecida(self) -> None:
        assert (
            extract_commune(
                commune="Casablanca", raw_data={"comuna": "Otra"}, text=None
            )
            == "Casablanca"
        )

    def test_usa_el_campo_estructurado_de_conaf(self) -> None:
        assert (
            extract_commune(commune=None, raw_data={"comuna": "Limache"}, text=None)
            == "Limache"
        )

    def test_cae_al_texto_generado_por_el_collector(self) -> None:
        texto = (
            'Incendio forestal "Cerro Alegre" — estado: En Combate. '
            "Ubicación: Valparaíso, Valparaíso. Reporte oficial de CONAF."
        )
        assert extract_commune(commune=None, raw_data={}, text=texto) == "Valparaíso"

    def test_no_inventa_comuna_cuando_no_la_hay(self) -> None:
        """FIRMS y los reportes ciudadanos no traen comuna.

        Devolver `None` deja al incidente fuera del Paso B y lo cuenta como
        métrica. Adivinar sería peor: un vínculo falso con una alerta roja es
        exactamente el error que este sistema no puede permitirse.
        """
        assert extract_commune(commune=None, raw_data={"lat": -33.0}, text="Humo") is None

    def test_ignora_los_ambitos_completos(self) -> None:
        assert (
            extract_commune(commune=None, raw_data={"Comunas": "Toda la region"}, text=None)
            is None
        )

    def test_provincia(self) -> None:
        assert (
            extract_province(province=None, raw_data={"provincia": "Marga Marga"})
            == "Marga Marga"
        )
        assert extract_province(province="Quillota", raw_data={}) == "Quillota"
        assert extract_province(province=None, raw_data={}) is None


class TestVistaDeAlerta:
    def test_alerta_comunal(self) -> None:
        view = alerta(ALERTA_VINA)
        assert view.communes == ("Viña del Mar",)
        assert view.is_regional is False
        assert view.level == "roja"
        assert view.family == "fire"

    def test_alerta_de_varias_comunas(self) -> None:
        assert alerta(ALERTA_DOS_COMUNAS).communes == ("Quilpué", "Villa Alemana")

    def test_alerta_nacional_no_tiene_comunas_concretas(self) -> None:
        view = alerta(ALERTA_NACIONAL)
        assert view.is_regional is True
        assert view.communes == ()


class TestCoincidencia:
    def test_une_la_alerta_al_incendio_de_su_comuna(self) -> None:
        matches = match_alerts_to_incidents([alerta(ALERTA_VINA)], [INCENDIO_VINA])
        assert len(matches) == 1
        assert matches[0].incident_id == 1
        assert matches[0].matched_commune == "Viña del Mar"
        assert matches[0].link_confidence == LINK_CONFIDENCE_EXACT

    def test_ignora_tildes_y_mayusculas(self) -> None:
        incidente = IncidentView(
            incident_id=9, commune="VINA DEL MAR", type=IncidentType.WILDFIRE
        )
        assert match_alerts_to_incidents([alerta(ALERTA_VINA)], [incidente])

    def test_tolera_sufijos_por_inclusion(self) -> None:
        incidente = IncidentView(
            incident_id=9,
            commune="Viña del Mar, Valparaíso",
            type=IncidentType.WILDFIRE,
        )
        matches = match_alerts_to_incidents([alerta(ALERTA_VINA)], [incidente])
        assert matches[0].link_confidence == LINK_CONFIDENCE_CONTAINED

    def test_una_alerta_comunal_cubre_varios_incidentes(self) -> None:
        """Cardinalidad deliberada del Paso B.

        Una alerta roja para Viña del Mar rige de verdad sobre todos los
        incendios activos de Viña del Mar. Forzarla a elegir uno inventaría una
        precisión que el acto administrativo no tiene.
        """
        matches = match_alerts_to_incidents(
            [alerta(ALERTA_VINA)], [INCENDIO_VINA, INCENDIO_VINA_2, INCENDIO_QUILPUE]
        )
        assert {match.incident_id for match in matches} == {1, 2}

    def test_una_alerta_multicomuna_solo_alcanza_a_las_suyas(self) -> None:
        """La alerta cubre Quilpué y Villa Alemana; Viña del Mar no es asunto suyo."""
        matches = match_alerts_to_incidents(
            [alerta(ALERTA_DOS_COMUNAS)], [INCENDIO_VINA, INCENDIO_QUILPUE]
        )
        assert {match.incident_id for match in matches} == {3}
        assert matches[0].matched_commune == "Quilpué"

    def test_no_mezcla_familias_de_fenomeno(self) -> None:
        """Una alerta amarilla por crecida no es una alerta sobre este incendio,
        aunque compartan comuna."""
        assert match_alerts_to_incidents([alerta(ALERTA_CRECIDA)], [INCENDIO_VINA]) == []

    def test_un_incidente_sin_comuna_queda_fuera_del_alcance(self) -> None:
        assert match_alerts_to_incidents([alerta(ALERTA_VINA)], [SIN_COMUNA]) == []

    def test_las_alertas_nacionales_no_tiñen_el_mapa_por_defecto(self) -> None:
        """Una alerta temprana preventiva nacional está vigente toda la
        temporada: adosarla a cada incidente no diría nada sobre ninguno."""
        assert (
            match_alerts_to_incidents([alerta(ALERTA_NACIONAL)], [INCENDIO_VINA]) == []
        )

    def test_pero_se_pueden_adosar_si_se_pide(self) -> None:
        matches = match_alerts_to_incidents(
            [alerta(ALERTA_NACIONAL)], [INCENDIO_VINA], attach_regional=True
        )
        assert len(matches) == 1
        assert matches[0].link_confidence == LINK_CONFIDENCE_REGIONAL

    def test_una_alerta_sin_fenomeno_declarado_se_une_pero_penalizada(self) -> None:
        sin_evento = {**ALERTA_VINA, "Razon": "Corte de suministro", "Evento": None}
        matches = match_alerts_to_incidents([alerta(sin_evento)], [INCENDIO_VINA])
        assert len(matches) == 1
        assert matches[0].link_confidence < LINK_CONFIDENCE_EXACT
        assert "no declara el fenómeno" in matches[0].note

    def test_es_determinista(self) -> None:
        alerts = [alerta(ALERTA_VINA), alerta(ALERTA_DOS_COMUNAS, event_id=101)]
        incidents = [INCENDIO_VINA, INCENDIO_VINA_2, INCENDIO_QUILPUE, SIN_COMUNA]
        primera = match_alerts_to_incidents(alerts, incidents)
        segunda = match_alerts_to_incidents(alerts, incidents)
        assert primera == segunda
