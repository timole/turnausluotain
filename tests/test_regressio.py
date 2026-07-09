"""BDD-tyyliset regressiotestit turnausluotaimen nykyversiolle.

Testit hakevat oikeat sivut verkosta (kerran per testiajo) ja ajavat
analyysin haetulle HTML:lle.
"""

import pytest

from turnausluotain import EI_LOYTYNYT, analysoi, etsi_joukkueet, hae_sivu, muotoile

WOUDIT_URL = "https://www.woudit.fi/etusivu/saimaa-turnaus/"
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
