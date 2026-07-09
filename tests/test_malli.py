"""Yksikkötestit LLM-mallin valinnalle. Ei verkkoa eikä API-kutsuja."""

import turnausluotain
from turnausluotain import OLETUSMALLI, valitse_malli


def test_oletusmalli(monkeypatch):
    """Scenario: mallia ei ole konfiguroitu

    Given TURNAUSLUOTAIN_MODEL ei ole asetettu
    When mallia ei anneta komentorivilläkään
    Then käytetään koodin oletusmallia
    """
    monkeypatch.delenv("TURNAUSLUOTAIN_MODEL", raising=False)
    assert valitse_malli() == OLETUSMALLI


def test_ymparistomuuttuja_voittaa_oletuksen(monkeypatch):
    """Scenario: malli konfiguroitu ympäristömuuttujalla (.env)

    Given TURNAUSLUOTAIN_MODEL=claude-opus-4-8
    When mallia ei anneta komentorivillä
    Then käytetään ympäristömuuttujan mallia
    """
    monkeypatch.setenv("TURNAUSLUOTAIN_MODEL", "claude-opus-4-8")
    assert valitse_malli() == "claude-opus-4-8"


def test_komentorivi_voittaa_ymparistomuuttujan(monkeypatch):
    """Scenario: malli annettu sekä ympäristössä että komentorivillä

    Given TURNAUSLUOTAIN_MODEL=claude-opus-4-8
    When komentorivillä annetaan --model claude-sonnet-5
    Then komentorivin malli voittaa
    """
    monkeypatch.setenv("TURNAUSLUOTAIN_MODEL", "claude-opus-4-8")
    assert valitse_malli("claude-sonnet-5") == "claude-sonnet-5"


def test_tiivista_llm_kayttaa_valittua_mallia(monkeypatch):
    """Scenario: valittu malli päätyy API-kutsuun asti

    Given API-asiakas on korvattu testikaksoisolennolla
    When tiivista_llm kutsutaan mallilla claude-opus-4-8
    Then API-kutsun model-parametri on claude-opus-4-8
    """
    kutsut = {}

    class FakeVastaus:
        stop_reason = "end_turn"
        content = [type("Blokki", (), {"type": "text", "text": "Testiturnaus."})()]

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                kutsut.update(kwargs)
                return FakeVastaus()

    monkeypatch.setattr(turnausluotain.anthropic, "Anthropic", lambda: FakeClient())

    tulos = turnausluotain.tiivista_llm("<html><body>Turnaus</body></html>",
                                        malli="claude-opus-4-8")

    assert kutsut["model"] == "claude-opus-4-8"
    assert tulos == "Testiturnaus."
