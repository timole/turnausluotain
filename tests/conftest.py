"""Lataa projektin juuren .env-tiedoston ympäristömuuttujiksi ennen testejä."""

import pytest

from turnausluotain import lataa_env

lataa_env()


@pytest.fixture(autouse=True)
def ilman_api_avainta(request, monkeypatch):
    """Poistaa API-avaimen testeiltä, joita ei ole merkitty llm-markerilla.

    Näin `pytest -m "not llm"` ei koskaan kutsu Anthropic-API:a ja
    heuristinen varapolku pysyy testattuna, vaikka avain olisi .env:ssä.
    """
    if "llm" not in request.keywords:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
