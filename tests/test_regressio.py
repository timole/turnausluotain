"""BDD-tyyliset regressiotestit turnausluotaimen nykyversiolle.

Testit hakevat oikeat sivut verkosta (kerran per testiajo) ja ajavat
analyysin haetulle HTML:lle.
"""

import os

import pytest

from turnausluotain import (
    EI_LOYTYNYT,
    analysoi,
    analysoi_llm,
    etsi_joukkueet,
    hae_sivu,
    muotoile,
    poimi_joukkueet_llm,
    tiivista_llm,
)

WOUDIT_URL = "https://www.woudit.fi/etusivu/saimaa-turnaus/"
WOUDIT_OTTELUOHJELMA_URL = "https://www.woudit.fi/etusivu/saimaa-turnaus/otteluohjelma/"
PALLOLIITTO_URL = (
    "https://www.palloliitto.fi/kilpailut/turnaukset-ja-lopputurnaukset/kki-lopputurnaukset"
)


@pytest.fixture(scope="session")
def woudit_tulos():
    return analysoi(hae_sivu(WOUDIT_URL))


@pytest.fixture(scope="session")
def palloliitto_tulos():
    return analysoi(hae_sivu(PALLOLIITTO_URL))


def test_woudit_turnaussivun_perustiedot(woudit_tulos):
    """Scenario: Woudit-turnaussivun perustiedot

    Given turnaussivu Saimaa-turnaus on julkaistu verkossa
      (https://www.woudit.fi/etusivu/saimaa-turnaus/)
    When manageri pyytää luotaimelta tiivistelmän
    Then tiivistelmä kertoo lajin "jääkiekko"
    And tiivistelmä sisältää ajankohdan ja paikkakunnan
    """
    tiivistelma = muotoile(woudit_tulos)

    assert woudit_tulos["laji"] == "jääkiekko"
    assert "jääkiekko" in tiivistelma

    assert woudit_tulos["ajankohta"] != EI_LOYTYNYT
    assert woudit_tulos["paikkakunta"] != EI_LOYTYNYT


def test_palloliiton_turnaussivun_perustiedot(palloliitto_tulos):
    """Scenario: Palloliiton turnaussivun perustiedot

    Given turnaussivu KKI-lopputurnaukset on julkaistu verkossa
      (https://www.palloliitto.fi/kilpailut/turnaukset-ja-lopputurnaukset/kki-lopputurnaukset)
    When manageri pyytää luotaimelta tiivistelmän
    Then tiivistelmä kertoo lajin "jalkapallo"
    And tiivistelmä sisältää ilmoittautumistiedot
    """
    tiivistelma = muotoile(palloliitto_tulos)

    assert palloliitto_tulos["laji"] == "jalkapallo"
    assert "jalkapallo" in tiivistelma

    assert palloliitto_tulos["ilmoittautuminen"] != [EI_LOYTYNYT]
    assert any(
        "ilmoittautu" in rivi.lower() or "@" in rivi
        for rivi in palloliitto_tulos["ilmoittautuminen"]
    )


def test_woudit_ilmoittautuneet_joukkueet():
    """Scenario: Woudit-turnauksen ilmoittautuneet joukkueet

    Given turnaussivu Saimaa-turnaus on julkaistu verkossa
      (https://www.woudit.fi/etusivu/saimaa-turnaus/)
    And ilmoittautuneet joukkueet on listattu Otteluohjelma-alasivulla
    When manageri pyytää luotaimelta ilmoittautuneet joukkueet
    Then joukkueista löytyy "Hiki-Hockey Seniors"
    """
    joukkueet = etsi_joukkueet(WOUDIT_URL)

    assert any("Hiki-Hockey Seniors" in joukkue for joukkue in joukkueet)


def test_palloliiton_ilmoittautuneet_joukkueet():
    """Scenario: Palloliiton turnauksen ilmoittautuneet joukkueet

    Given turnaussivu KKI-lopputurnaukset on julkaistu verkossa
      (https://www.palloliitto.fi/kilpailut/turnaukset-ja-lopputurnaukset/kki-lopputurnaukset)
    And ilmoittautuneet joukkueet on listattu ulkoisessa Taso-palvelussa
      (https://taso.palloliitto.fi/taso/ilmoittautuneet.php?turnaus=splkki26)
    When manageri pyytää luotaimelta ilmoittautuneet joukkueet
    Then joukkueista löytyy "Gnistan"
    """
    joukkueet = etsi_joukkueet(PALLOLIITTO_URL)

    assert any("Gnistan" in joukkue for joukkue in joukkueet)


@pytest.mark.llm
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY puuttuu ympäristöstä",
)
def test_llm_analysoi_palloliiton_turnaussivun_perustiedot():
    """Scenario: LLM poimii perustiedot sivulta, jolla heuristiikat erehtyvät

    Given turnaussivu KKI-lopputurnaukset, jolla jokaisella sarjalla on oma
      pelipäivänsä ja ikärajat on annettu syntymäpäivinä (esim. 31.12.1996)
    And heuristiikat poimivat ajankohdaksi ikärajapäivän eivätkä löydä sarjoja
    When manageri pyytää luotaimelta perustiedot LLM-poiminnalla
    Then laji on jalkapallo
    And ajankohta on turnauskesältä 2026, ei ikärajan syntymäpäivä
    And sarjoista löytyvät ainakin M35 ja M75
    """
    tulos = analysoi_llm(hae_sivu(PALLOLIITTO_URL))

    assert tulos["laji"] == "jalkapallo"
    assert "2026" in tulos["ajankohta"]
    assert "31.12.19" not in tulos["ajankohta"], "ikärajapäivä ei ole ajankohta"
    sarjat = " ".join(tulos["sarjat"])
    assert "M35" in sarjat and "M75" in sarjat


@pytest.mark.llm
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY puuttuu ympäristöstä",
)
def test_llm_poimii_ilmoittautuneet_joukkueet_sivulta():
    """Scenario: LLM poimii ilmoittautuneet joukkueet turnaussivulta

    Given Saimaa-turnauksen otteluohjelma-alasivu, jolla ilmoittautuneet
      joukkueet on listattu sarjoittain
      (https://www.woudit.fi/etusivu/saimaa-turnaus/otteluohjelma/)
    And Anthropic API -avain on asetettu ympäristöön (ANTHROPIC_API_KEY)
    When manageri pyytää luotaimelta joukkueet LLM-poiminnalla
    Then joukkueista löytyy "Hiki-Hockey Seniors" sarjasta 60+
    """
    joukkueet = poimi_joukkueet_llm(hae_sivu(WOUDIT_OTTELUOHJELMA_URL))

    hiki = [j for j in joukkueet if "Hiki-Hockey Seniors" in j["nimi"]]
    assert hiki, f"Hiki-Hockey Seniors puuttuu: {[j['nimi'] for j in joukkueet]}"
    assert "60" in hiki[0]["sarja"]


@pytest.mark.llm
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY puuttuu ympäristöstä",
)
def test_llm_tiivistelma_woudit_turnaussivusta():
    """Scenario: LLM-tiivistelmä Woudit-turnaussivusta

    Given turnaussivu Saimaa-turnaus on julkaistu verkossa
      (https://www.woudit.fi/etusivu/saimaa-turnaus/)
    And Anthropic API -avain on asetettu ympäristöön (ANTHROPIC_API_KEY)
    When manageri pyytää luotaimelta LLM-tiivistelmän sivusta
    Then tiivistelmä on parin lauseen mittainen suomenkielinen kuvaus
    And tiivistelmä kertoo, että kyse on turnauksesta
    """
    tiivistelma = tiivista_llm(hae_sivu(WOUDIT_URL))

    assert isinstance(tiivistelma, str)
    tiivistelma = tiivistelma.strip()
    assert 30 <= len(tiivistelma) <= 600, "parin lauseen mittainen"
    assert "turnau" in tiivistelma.lower() or "jääkiek" in tiivistelma.lower()
