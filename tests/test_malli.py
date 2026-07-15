"""Yksikkötestit LLM-mallin ja -tarjoajan valinnalle. Ei verkkoa eikä API-kutsuja."""

import pytest

import turnausluotain
from turnausluotain import (
    OLETUSMALLI,
    kysy_llm,
    taydenna_anthropic,
    valitse_malli,
    valitse_taydentaja,
)


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


def test_oletustarjoaja_on_anthropic(monkeypatch):
    """Scenario: LLM-tarjoajaa ei ole konfiguroitu

    Given TURNAUSLUOTAIN_PROVIDER ei ole asetettu
    When tarjoaja valitaan
    Then käytetään Anthropic-toteutusta
    """
    monkeypatch.delenv("TURNAUSLUOTAIN_PROVIDER", raising=False)
    assert valitse_taydentaja() is taydenna_anthropic


def test_tuntematon_tarjoaja_antaa_selkean_virheen(monkeypatch):
    """Scenario: konfiguroitu LLM-tarjoaja on tuntematon

    Given TURNAUSLUOTAIN_PROVIDER=eiole
    When tarjoaja valitaan
    Then virheilmoitus nimeää tuntemattoman ja luettelee tuetut tarjoajat
    """
    monkeypatch.setenv("TURNAUSLUOTAIN_PROVIDER", "eiole")
    with pytest.raises(ValueError, match="eiole.*anthropic"):
        valitse_taydentaja()


def test_kysy_llm_kayttaa_valittua_tarjoajaa(monkeypatch):
    """Scenario: LLM-kutsu ohjautuu konfiguroidulle tarjoajalle

    Given tarjoajaksi on rekisteröity testikaksoisolento "fake"
    And TURNAUSLUOTAIN_PROVIDER=fake
    When kysy_llm kutsutaan
    Then kutsu ohjautuu testikaksoisolennolle sivun sisältö mukanaan
    """
    kutsut = {}

    def fake_taydenna(jarjestelma, sisalto, malli, max_tokens):
        kutsut.update(jarjestelma=jarjestelma, sisalto=sisalto,
                      malli=malli, max_tokens=max_tokens)
        return "fake-vastaus"

    monkeypatch.setitem(turnausluotain.TAYDENTAJAT, "fake", fake_taydenna)
    monkeypatch.setenv("TURNAUSLUOTAIN_PROVIDER", "fake")

    tulos = kysy_llm("järjestelmä", "kysymys",
                     "<html><body>Turnaussivu</body></html>")

    assert tulos == "fake-vastaus"
    assert kutsut["jarjestelma"] == "järjestelmä"
    assert "kysymys" in kutsut["sisalto"]
    assert "Turnaussivu" in kutsut["sisalto"]
