"""Yksikkötestit harjoitusvastustajien päivälogiikalle. Ei verkkoa eikä API-kutsuja."""

import pytest

from turnausluotain import harjoitusvastustajat, joukkue, pelipaivat


@pytest.mark.parametrize(
    "merkinta, odotus",
    [
        ("to-pe", ["to", "pe"]),
        ("la-su", ["la", "su"]),
        ("to-la", ["to", "pe", "la"]),  # väli laajenee kaikkiin päiviin
        ("pe", ["pe"]),
        ("", []),
        ("sarjamuotoinen", []),  # ei viikonpäiviä -> ei arvausta
    ],
)
def test_pelipaivat_tulkitsee_valimerkinnan(merkinta, odotus):
    """Scenario: pelipäivämerkinnän tulkinta

    Given joukkueen pelipäivät on merkitty välinä (esim. "to-pe")
    When luotain tulkitsee merkinnän
    Then tuloksena on kaikki välin päivät
    """
    assert pelipaivat(joukkue("Testi", paivat=merkinta)) == odotus


def test_harjoitusvastustajat_jakaa_varmoihin_ja_mahdollisiin():
    """Scenario: harjoitusvastustajien etsintä torstaille

    Given turnaukseen on ilmoittautunut joukkueita eri pelipäivillä
    When manageri etsii harjoitusvastustajia torstaille
    Then torstaina pelaavat ovat varmasti paikalla
    And perjantaina pelaavat ovat mahdollisesti paikalla jo torstai-iltana
    And vasta lauantaina pelaavat eivät ole ehdokkaita
    """
    joukkueet = [
        joukkue("Torstain pelaaja", "60+", "B", "to-pe"),
        joukkue("Perjantain pelaaja", "40+", "A", "pe-la"),
        joukkue("Lauantain pelaaja", "60+", "A1", "la-su"),
        joukkue("Tuntematon", "50+", "B", ""),
    ]

    varmat, mahdolliset = harjoitusvastustajat(joukkueet, "to")

    assert [j["nimi"] for j in varmat] == ["Torstain pelaaja"]
    assert [j["nimi"] for j in mahdolliset] == ["Perjantain pelaaja"]


def test_harjoitusvastustajat_viikon_viimeisena_paivana():
    """Scenario: sunnuntaille ei ole seuraavaa päivää

    Given joukkueita, jotka pelaavat lauantaista sunnuntaihin
    When manageri etsii harjoitusvastustajia sunnuntaille
    Then mahdollisesti paikalla olevien lista on tyhjä eikä ajo kaadu
    """
    varmat, mahdolliset = harjoitusvastustajat(
        [joukkue("Viikonlopun pelaaja", "60+", "A1", "la-su")], "su"
    )

    assert [j["nimi"] for j in varmat] == ["Viikonlopun pelaaja"]
    assert mahdolliset == []
