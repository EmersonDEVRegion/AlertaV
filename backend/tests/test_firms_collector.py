"""Tests del collector de NASA FIRMS.

`normalize()` es una función pura, así que el mapeo se prueba con CSV real sin
tocar la red ni la base de datos.
"""

from __future__ import annotations

from datetime import UTC

import pytest

from app.collectors.firms.client import FirmsClient
from app.collectors.firms.collector import (
    FirmsCollector,
    build_external_id,
    map_confidence,
    parse_acquisition,
)
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType

VIIRS_CSV = """latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight
-33.02500,-71.52000,340.5,0.42,0.38,2026-08-16,1832,N,VIIRS,n,2.0NRT,295.1,12.34,D
-32.98100,-71.48700,367.2,0.40,0.36,2026-08-16,1832,N,VIIRS,h,2.0NRT,301.8,45.60,D
-33.11000,-71.60000,310.0,0.45,0.40,2026-08-16,0024,N,VIIRS,l,2.0NRT,288.4,3.20,N
"""

MODIS_CSV = """latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight
-33.05000,-71.55000,325.7,1.1,1.0,2026-08-16,1420,Terra,MODIS,78,6.1NRT,290.2,22.10,D
"""


class TestParsers:
    def test_acq_time_sin_ceros_a_la_izquierda(self) -> None:
        """FIRMS entrega "24" para las 00:24 UTC."""
        assert parse_acquisition("2026-08-16", "24").hour == 0
        assert parse_acquisition("2026-08-16", "24").minute == 24
        assert parse_acquisition("2026-08-16", "1832").hour == 18

    def test_acq_time_siempre_utc(self) -> None:
        assert parse_acquisition("2026-08-16", "1832").tzinfo == UTC

    def test_acq_time_invalido(self) -> None:
        with pytest.raises(ValueError):
            parse_acquisition("2026-08-16", "9999")
        with pytest.raises(ValueError):
            parse_acquisition("2026-08-16", "abc")

    def test_confianza_viirs_categorica(self) -> None:
        assert map_confidence("l") == pytest.approx(0.30)
        assert map_confidence("n") == pytest.approx(0.55)
        assert map_confidence("h") == pytest.approx(0.80)

    def test_confianza_modis_porcentual(self) -> None:
        assert map_confidence("78") == pytest.approx(0.78)
        assert map_confidence("0") == pytest.approx(0.10)   # piso
        assert map_confidence("100") == pytest.approx(0.85)  # techo

    def test_confianza_desconocida_usa_neutro(self) -> None:
        assert map_confidence(None) == pytest.approx(0.5)
        assert map_confidence("???") == pytest.approx(0.5)

    def test_external_id_es_determinista(self) -> None:
        record = {
            "_sensor": "VIIRS_SNPP_NRT",
            "satellite": "N",
            "instrument": "VIIRS",
            "acq_date": "2026-08-16",
            "acq_time": "1832",
        }
        first = build_external_id(record, lat=-33.025, lon=-71.52)
        second = build_external_id(dict(record), lat=-33.025, lon=-71.52)
        assert first == second
        assert first.startswith("firms:")

    def test_external_id_distingue_detecciones(self) -> None:
        base = {
            "_sensor": "VIIRS_SNPP_NRT",
            "satellite": "N",
            "instrument": "VIIRS",
            "acq_date": "2026-08-16",
            "acq_time": "1832",
        }
        assert build_external_id(base, lat=-33.025, lon=-71.52) != build_external_id(
            base, lat=-33.030, lon=-71.52
        )


class TestCsvParsing:
    def test_parsea_viirs(self) -> None:
        rows = FirmsClient.parse_csv(VIIRS_CSV, sensor="VIIRS_SNPP_NRT")
        assert len(rows) == 3
        assert rows[0]["latitude"] == "-33.02500"

    def test_csv_vacio(self) -> None:
        assert FirmsClient.parse_csv("", sensor="VIIRS_SNPP_NRT") == []

    def test_detecta_error_enmascarado_como_200(self) -> None:
        """FIRMS responde 200 con texto plano cuando la MAP_KEY es inválida.

        Tratarlo como 'cero detecciones' haría que el collector fallara en
        silencio durante días, que es el peor modo de falla posible aquí.
        """
        with pytest.raises(CollectorError, match="error"):
            FirmsClient.parse_csv("Invalid MAP_KEY.", sensor="VIIRS_SNPP_NRT")


class TestNormalize:
    def _collector(self) -> FirmsCollector:
        # `normalize` es pura: no necesita sesión ni cliente HTTP configurado.
        return FirmsCollector.__new__(FirmsCollector)

    def _rows(self, csv_text: str, sensor: str) -> list[dict]:
        rows = FirmsClient.parse_csv(csv_text, sensor=sensor)
        for row in rows:
            row["_sensor"] = sensor
        return rows

    def test_mapea_viirs_a_anomalia_termica(self) -> None:
        events = self._collector().normalize(
            self._rows(VIIRS_CSV, "VIIRS_SNPP_NRT")
        )
        assert len(events) == 3
        assert all(event.source is EventSource.NASA_FIRMS for event in events)
        # Regla del proyecto: FIRMS NUNCA produce 'wildfire' por sí solo.
        assert all(event.type is EventType.THERMAL_ANOMALY for event in events)
        assert all(event.type is not EventType.WILDFIRE for event in events)

    def test_coordenadas_y_confianza(self) -> None:
        events = self._collector().normalize(self._rows(VIIRS_CSV, "VIIRS_SNPP_NRT"))
        primero = events[0]
        assert primero.lat == pytest.approx(-33.025)
        assert primero.lon == pytest.approx(-71.52)
        assert primero.confidence == pytest.approx(0.55)  # 'n' → nominal
        assert primero.in_region is True

    def test_conserva_payload_original(self) -> None:
        events = self._collector().normalize(self._rows(MODIS_CSV, "MODIS_NRT"))
        raw = events[0].raw_data
        assert raw["brightness"] == "325.7"
        assert raw["frp"] == "22.10"
        assert raw["sensor"] == "MODIS_NRT"
        assert "_sensor" not in raw  # las claves internas no se filtran

    def test_external_id_unico_por_evento(self) -> None:
        events = self._collector().normalize(self._rows(VIIRS_CSV, "VIIRS_SNPP_NRT"))
        ids = {event.external_id for event in events}
        assert len(ids) == 3

    def test_descarta_filas_sin_coordenadas(self) -> None:
        rows = self._rows(VIIRS_CSV, "VIIRS_SNPP_NRT")
        rows.append({"_sensor": "VIIRS_SNPP_NRT", "latitude": "", "longitude": ""})
        rows.append({"_sensor": "VIIRS_SNPP_NRT", "latitude": "x", "longitude": "y",
                     "acq_date": "2026-08-16", "acq_time": "1200"})
        assert len(self._collector().normalize(rows)) == 3

    def test_descarta_filas_con_fecha_invalida(self) -> None:
        rows = self._rows(VIIRS_CSV, "VIIRS_SNPP_NRT")
        rows.append({
            "_sensor": "VIIRS_SNPP_NRT",
            "latitude": "-33.0",
            "longitude": "-71.5",
            "acq_date": "no-es-fecha",
            "acq_time": "1200",
        })
        assert len(self._collector().normalize(rows)) == 3

    def test_texto_advierte_que_no_esta_confirmado(self) -> None:
        events = self._collector().normalize(self._rows(VIIRS_CSV, "VIIRS_SNPP_NRT"))
        assert "sin confirmar" in events[0].text
