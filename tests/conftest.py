"""Fixtures partagées pour les tests TempoForecast."""

import os
import sys
import sqlite3
import pytest
from datetime import date, timedelta

# Ajouter le répertoire racine au path pour les imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def use_test_db(tmp_path, monkeypatch):
    """Utilise une base de données temporaire pour chaque test."""
    db_path = str(tmp_path / "test_tempo.db")
    monkeypatch.setattr("config.Config.DATABASE_PATH", db_path)
    # Disable start date filter in tests (test data uses arbitrary dates)
    monkeypatch.setattr("config.Config.PREDICTION_START_DATE", "2000-01-01")
    from database import init_db
    init_db()
    yield db_path


@pytest.fixture
def sample_weather():
    """Données météo de test pour 5 jours."""
    today = date.today()
    return [
        {
            "date": (today + timedelta(days=i)).isoformat(),
            "temp_min": -2 + i * 2,
            "temp_max": 3 + i * 2,
            "temp_moy": 0.5 + i * 2,
            "humidity": 75,
            "wind_speed": 15,
            "pressure": None,
            "description": "test forecast",
            "forecast_quality": "api",
        }
        for i in range(1, 6)
    ]


@pytest.fixture
def cold_weather():
    """Données météo très froides (vague de froid).
    Commence un lundi pour éviter les règles EDF weekends (R2/R3)."""
    today = date.today()
    # Trouver le prochain lundi (weekday=0)
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    start = today + timedelta(days=days_until_monday)
    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "temp_min": -6,
            "temp_max": -2,
            "temp_moy": -4,
            "humidity": 85,
            "wind_speed": 20,
            "pressure": None,
            "description": "grand froid",
            "forecast_quality": "api",
        }
        for i in range(5)
    ]


@pytest.fixture
def warm_weather():
    """Données météo douces (printemps)."""
    today = date.today()
    return [
        {
            "date": (today + timedelta(days=i)).isoformat(),
            "temp_min": 10,
            "temp_max": 18,
            "temp_moy": 14,
            "humidity": 55,
            "wind_speed": 8,
            "pressure": None,
            "description": "doux",
            "forecast_quality": "api",
        }
        for i in range(1, 6)
    ]


@pytest.fixture
def sample_remaining():
    """Quotas restants standard (milieu de saison)."""
    return {"ROUGE": 12, "BLANC": 25, "BLEU": 100}


@pytest.fixture
def low_remaining():
    """Quotas restants faibles (fin de saison)."""
    return {"ROUGE": 2, "BLANC": 5, "BLEU": 100}
