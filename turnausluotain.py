#!/usr/bin/env python3
"""Turnausluotain – tiivistää harrasteturnauksen www-sivun suomeksi.

Käyttö: python turnausluotain.py <turnauksen-url>
"""

import argparse
import atexit
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

from urllib.parse import urljoin

import anthropic
from bs4 import BeautifulSoup
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


class HakuVirhe(Exception):
    """Sivun hakeminen tai renderöinti epäonnistui."""


EI_LOYTYNYT = "ei löytynyt sivulta"
OLETUSMALLI = "claude-haiku-4-5"

LOKI = logging.getLogger("turnausluotain")

# Listahinnat $/miljoona tokenia (syöte, tuloste), platform.claude.com 2026-07.
# Huom: Sonnet 5:llä on tutustumishinta 2/10 $ 31.8.2026 asti; tässä listahinta.
HINNAT = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-4-8": (5.00, 25.00),
}

# LLM-käytön kertymä tämän ajon ajalta (kysy_llm päivittää)
KAYTTO = {"syote": 0, "tuloste": 0, "usd": 0.0}


def valitse_malli(cli_malli: str | None = None) -> str:
    """Valitsee LLM-mallin: --model > TURNAUSLUOTAIN_MODEL > oletus."""
    return cli_malli or os.environ.get("TURNAUSLUOTAIN_MODEL") or OLETUSMALLI


def lataa_env() -> None:
    """Lataa projektin juuren .env-tiedoston ympäristömuuttujiksi.

    Jo asetettuja ympäristömuuttujia ei ylikirjoiteta.
    """
    env_tiedosto = Path(__file__).resolve().parent / ".env"
    if not env_tiedosto.exists():
        return
    for rivi in env_tiedosto.read_text(encoding="utf-8").splitlines():
        rivi = rivi.strip()
        if not rivi or rivi.startswith("#") or "=" not in rivi:
            continue
        avain, _, arvo = rivi.partition("=")
        os.environ.setdefault(avain.strip(), arvo.strip())

LAJIT = {
    "jääkiekko": ["jääkiekko", "jäähalli", "kiekko", "kaukalo"],
    "salibandy": ["salibandy", "sähly"],
    "jalkapallo": ["jalkapallo", "futis"],
    "futsal": ["futsal"],
    "lentopallo": ["lentopallo", "beach volley"],
    "koripallo": ["koripallo"],
    "pesäpallo": ["pesäpallo"],
    "käsipallo": ["käsipallo"],
    "sulkapallo": ["sulkapallo"],
    "ringette": ["ringette"],
    "kaukalopallo": ["kaukalopallo"],
}

KAUPUNGIT = [
    "Helsinki", "Espoo", "Tampere", "Vantaa", "Oulu", "Turku", "Jyväskylä",
    "Kuopio", "Lahti", "Pori", "Kouvola", "Joensuu", "Lappeenranta",
    "Hämeenlinna", "Vaasa", "Seinäjoki", "Rovaniemi", "Mikkeli", "Kotka",
    "Salo", "Porvoo", "Kokkola", "Hyvinkää", "Lohja", "Järvenpää",
    "Rauma", "Kajaani", "Kerava", "Savonlinna", "Nokia", "Kaarina",
    "Ylöjärvi", "Kangasala", "Riihimäki", "Raseborg", "Imatra", "Raisio",
    "Raahe", "Sastamala", "Tornio", "Iisalmi", "Varkaus", "Kemi",
    "Valkeakoski", "Hamina", "Heinola", "Pieksämäki", "Forssa", "Jämsä",
    "Uusikaupunki", "Kuusamo", "Ylivieska", "Kirkkonummi", "Tuusula",
    "Nurmijärvi", "Vihti", "Sipoo", "Mäntsälä", "Naantali", "Laukaa",
    "Rantasalmi", "Punkaharju", "Sotkamo", "Vierumäki", "Pajulahti",
    "Kalajoki", "Kittilä", "Levi", "Ruka", "Himos", "Tahko", "Vuokatti",
]

OSIO_OTSIKOT = {
    "sarjat", "ottelumäärät", "pelijärjestelmä", "säännöt", "pelipaikat",
    "majoitus", "ilmoittautuminen", "pelaajapankki", "aikataulu",
    "osallistumismaksu", "turnausmaksu", "yhteystiedot", "ajankohta",
}

PVM_VALI = re.compile(
    r"\d{1,2}\s*\.\s*\d{1,2}\s*(?:\.\s*\d{4})?\s*[–—-]\s*\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{4}"
)
PVM = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PUHELIN = re.compile(r"(?:\+358|0)\s?\d{1,3}(?:[\s-]?\d{2,4}){2,3}")
MAKSU = re.compile(r"[^.]*maksu[^.]*?\d[\d\s.,]*\s*(?:€|euroa|eur)[^.]*\.?", re.IGNORECASE)

# Joukkuerivi: "Nimi (Paikkakunta) ..."; sulkujen sisältö enintään kaksi sanaa
# eikä numeroita, jottei ikäraja- tai hallirivejä lueta joukkueiksi.
JOUKKUE_RIVI = re.compile(r"^([^()]{3,50}?)\s*\(([^()\d]{2,30})\)")
# tärkeysjärjestyksessä: varmin vihje ensin
JOUKKUELINKKI_SANAT = ("ilmoittautuneet", "otteluohjelma", "osallistujat", "joukkueet")
MIN_JOUKKUEITA = 5

# linkit edellisten vuosien tulospalveluun (esim. "Live Seuranta 2025")
TULOSLINKKI_SANAT = ("gameresult", "tulokset", "tulospalvelu", "seuranta", "live")


_SELAIN = {"playwright": None, "selain": None}


def hae_selain():
    """Palauttaa jaetun headless-Chromiumin; käynnistää sen ensimmäisellä kutsulla.

    Selain on jaettu, koska käynnistys maksaa noin sekunnin ja yksi ajo hakee
    useita sivuja. Suljetaan atexitissä.
    """
    if _SELAIN["selain"] is None:
        alku = time.monotonic()
        _SELAIN["playwright"] = sync_playwright().start()
        _SELAIN["selain"] = _SELAIN["playwright"].chromium.launch()
        atexit.register(sulje_selain)
        LOKI.info("selain käynnistetty (%.1f s)", time.monotonic() - alku)
    return _SELAIN["selain"]


def sulje_selain() -> None:
    if _SELAIN["selain"] is not None:
        _SELAIN["selain"].close()
        _SELAIN["selain"] = None
    if _SELAIN["playwright"] is not None:
        _SELAIN["playwright"].stop()
        _SELAIN["playwright"] = None


def hae_sivu(url: str) -> str:
    """Hakee sivun ja palauttaa renderöidyn HTML:n.

    Haku tehdään selaimella, jotta myös JavaScriptillä rakennetut sivut
    (esim. GameResults-tulospalvelu) saadaan luettua.
    """
    alku = time.monotonic()
    sivu = hae_selain().new_page()
    try:
        vastaus = sivu.goto(url, wait_until="networkidle", timeout=45000)
        # goto ei kaadu HTTP-virheeseen, joten status on tarkistettava itse
        # (muuten esim. 404-sivu analysoitaisiin turnaussivuna).
        if vastaus is not None and vastaus.status >= 400:
            raise HakuVirhe(f"{url}: HTTP {vastaus.status}")
        html = sivu.content()
    except PlaywrightError as virhe:
        raise HakuVirhe(f"{url}: {virhe.message.splitlines()[0]}") from virhe
    finally:
        sivu.close()
    LOKI.info(
        "haettu %s (%.1f s, %d kt)", url, time.monotonic() - alku, len(html) // 1024,
    )
    return html


def poimi_teksti(html: str) -> tuple[str, list[str]]:
    """Palauttaa (sivun otsikko, sisältörivit)."""
    soup = BeautifulSoup(html, "html.parser")
    otsikko = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    juuri = soup.find("main") or soup.body or soup
    rivit = [r.strip() for r in juuri.get_text("\n").split("\n") if r.strip()]
    return otsikko, rivit


def tunnista_laji(teksti: str) -> str:
    pienella = teksti.lower()
    osumat = {
        laji: sum(pienella.count(sana) for sana in sanat)
        for laji, sanat in LAJIT.items()
    }
    paras = max(osumat, key=osumat.get)
    return paras if osumat[paras] > 0 else EI_LOYTYNYT


def tunnista_ajankohta(teksti: str) -> str:
    vali = PVM_VALI.search(teksti)
    if vali:
        return re.sub(r"\s+", "", vali.group()).replace("–", " – ").replace("—", " – ")
    yksittaiset = PVM.findall(teksti)
    return yksittaiset[0] if yksittaiset else EI_LOYTYNYT


def tunnista_paikkakunnat(teksti: str) -> str:
    loydetyt = []
    for kaupunki in KAUPUNGIT:
        # taivutusmuodotkin osuvat, kun haetaan vartalolla (Savonlinna -> Savonlinnan)
        vartalo = kaupunki[:-1] if kaupunki.endswith("i") else kaupunki
        if re.search(r"\b" + re.escape(vartalo), teksti, re.IGNORECASE):
            loydetyt.append(kaupunki)
    return ", ".join(loydetyt) if loydetyt else EI_LOYTYNYT


def poimi_osio(rivit: list[str], otsikko: str, max_rivit: int = 15) -> list[str]:
    """Kerää rivit annetun osio-otsikon jälkeen seuraavaan otsikkoon asti."""
    osio: list[str] = []
    keraa = False
    for rivi in rivit:
        avain = rivi.lower().rstrip(":")
        if avain == otsikko:
            keraa = True
            continue
        if keraa:
            if avain in OSIO_OTSIKOT or avain.startswith("terveisin") or len(osio) >= max_rivit:
                break
            osio.append(rivi)
    return osio


def poimi_ilmoittautuminen(rivit: list[str], teksti: str) -> list[str]:
    tiedot = poimi_osio(rivit, "ilmoittautuminen", max_rivit=6)
    emailit = EMAIL.findall(teksti)
    puhelimet = PUHELIN.findall(teksti)
    maksu = MAKSU.search(teksti)
    if maksu:
        tiedot.append(re.sub(r"\s+", " ", maksu.group()).strip())
    if emailit and not any("@" in t for t in tiedot):
        tiedot.append(f"Sähköposti: {emailit[0]}")
    if puhelimet and not any(any(c.isdigit() for c in t) for t in tiedot):
        tiedot.append(f"Puhelin: {puhelimet[0].strip()}")
    return tiedot or [EI_LOYTYNYT]


def analysoi(html: str) -> dict:
    """Analysoi turnaussivun HTML:n ja palauttaa poimitut tiedot sanakirjana."""
    otsikko, rivit = poimi_teksti(html)
    teksti = "\n".join(rivit)
    return {
        "otsikko": otsikko or (rivit[0] if rivit else EI_LOYTYNYT),
        "laji": tunnista_laji(teksti),
        "ajankohta": tunnista_ajankohta(teksti),
        "paikkakunta": tunnista_paikkakunnat(teksti),
        "sarjat": poimi_osio(rivit, "sarjat") or [EI_LOYTYNYT],
        "ilmoittautuminen": poimi_ilmoittautuminen(rivit, teksti),
    }


def muotoile(tulos: dict) -> str:
    osat = [
        f"TURNAUS: {tulos['otsikko']}",
        f"Laji:        {tulos['laji']}",
        f"Ajankohta:   {tulos['ajankohta']}",
        f"Paikkakunta: {tulos['paikkakunta']}",
        "Sarjat:",
        *(f"  - {s}" for s in tulos["sarjat"]),
        "Ilmoittautuminen:",
        *(f"  {r}" for r in tulos["ilmoittautuminen"]),
    ]
    return "\n".join(osat)


def joukkue(nimi: str, sarja: str = "", taso: str = "", paivat: str = "") -> dict:
    """Luo joukkuetietueen. Heuristiikoilla saadaan vain nimi; LLM täyttää muut."""
    return {"nimi": nimi, "sarja": sarja, "taso": taso, "paivat": paivat}


def muotoile_joukkue(j: dict) -> str:
    """Muotoilee joukkueen riviksi: "Nimi (sarja, taso, päivät)" tiedossa olevin osin."""
    lisat = [k for k in (j["sarja"], j["taso"], j["paivat"]) if k]
    return f"{j['nimi']} ({', '.join(lisat)})" if lisat else j["nimi"]


def poimi_joukkueet(html: str) -> list[dict]:
    """Poimii sivulta joukkuelistan: ensin tekstiriveistä, sitten tauluista.

    Sivu tulkitaan joukkuelistaksi vain, jos osumia on vähintään
    MIN_JOUKKUEITA – yksittäiset osumat ovat yleensä muuta sisältöä.
    """
    joukkueet = poimi_joukkueet_riveista(html) or poimi_joukkueet_tauluista(html)
    return joukkueet if len(joukkueet) >= MIN_JOUKKUEITA else []


def poimi_joukkueet_riveista(html: str) -> list[dict]:
    """Poimii tekstistä joukkuerivit muodossa "Nimi (Paikkakunta)"."""
    _, rivit = poimi_teksti(html)
    joukkueet = []
    for rivi in rivit:
        rivi = re.sub(r"\s+", " ", rivi.replace("\xa0", " ")).strip()
        osuma = JOUKKUE_RIVI.match(rivi)
        if osuma and len(osuma.group(2).split()) <= 2:
            joukkueet.append(
                joukkue(f"{osuma.group(1).strip()} ({osuma.group(2).strip()})")
            )
    return joukkueet


def poimi_joukkueet_tauluista(html: str) -> list[dict]:
    """Poimii joukkueet taulukkoriveistä ilmoittautumissivulta (esim. Taso).

    Taulukkosolut voivat olla mitä vain (aikatauluja, tuloksia), joten
    poiminta tehdään vain sivulta, joka otsikoi itsensä ilmoittautuneiden
    listaksi.
    """
    soup = BeautifulSoup(html, "html.parser")
    otsikko = soup.title.get_text() if soup.title else ""
    if "ilmoittautuneet" not in otsikko.lower():
        return []
    joukkueet, nimet = [], set()
    for rivi in soup.find_all("tr"):
        solut = [td.get_text(strip=True) for td in rivi.find_all("td")]
        nimi = next((s for s in reversed(solut) if s), "")
        if nimi and nimi not in nimet:
            nimet.add(nimi)
            joukkueet.append(joukkue(nimi))
    return joukkueet


VIIKONPAIVAT = ["ma", "ti", "ke", "to", "pe", "la", "su"]


def pelipaivat(j: dict) -> list[str]:
    """Joukkueen pelipäivät listana; "to-pe" tarkoittaa sekä torstaita että
    perjantaita ja kaikkea siltä väliltä.
    """
    paat = [p for p in re.split(r"[^a-zä]+", j["paivat"].lower()) if p in VIIKONPAIVAT]
    if not paat:
        return []
    alku, loppu = VIIKONPAIVAT.index(paat[0]), VIIKONPAIVAT.index(paat[-1])
    return VIIKONPAIVAT[alku:loppu + 1]


def harjoitusvastustajat(joukkueet: list[dict], paiva: str) -> tuple[list, list]:
    """Jakaa joukkueet harjoitusottelun kannalta kahteen ryhmään.

    Palauttaa (varmat, mahdolliset): varmat pelaavat kyseisenä päivänä,
    mahdolliset vasta seuraavana – he saattavat silti saapua paikalle jo
    edellisiltana, mikä riittää iltaotteluun.
    """
    seuraava = VIIKONPAIVAT[VIIKONPAIVAT.index(paiva) + 1:][:1]
    varmat = [j for j in joukkueet if paiva in pelipaivat(j)]
    mahdolliset = [
        j for j in joukkueet
        if j not in varmat and seuraava and seuraava[0] in pelipaivat(j)
    ]
    return varmat, mahdolliset


def etsi_sija(ranking: list[dict], nimi: str) -> str:
    """Etsii joukkueen sijan edellisvuoden järjestyksestä nimen perusteella.

    Nimet vaihtelevat vuosittain (paikkakunta mukana tai ei), joten vertailu
    tehdään väljästi.
    """
    for r in ranking:
        if r["nimi"].lower() in nimi.lower():
            return f"{r['lohko']} {r['sija']}."
    return ""


def muotoile_harjoitusvastustajat(
    joukkueet: list[dict],
    paiva: str,
    oma_nimi: str | None,
    ranking: list[dict] | None = None,
    ranking_sarja: str = "",
) -> str:
    """Muotoilee harjoitusvastustajaehdokkaat; oma sarja ensin, jos oma
    joukkue on annettu. Edellisvuoden sija näytetään, jos ranking on haettu.
    """
    ranking = ranking or []
    oma = next(
        (j for j in joukkueet if oma_nimi and oma_nimi.lower() in j["nimi"].lower()),
        None,
    )

    def rivi(j: dict) -> str:
        oma_sarja = bool(oma) and j["sarja"] == oma["sarja"]
        # Sija haetaan vain oman sarjan joukkueille: ranking koskee yhtä sarjaa,
        # ja eri sarjoissa on samannimisiä joukkueita (Susipapat 50 / 60).
        sija = etsi_sija(ranking, j["nimi"]) if ranking and oma_sarja else ""
        merkki = "*" if oma_sarja else " "
        return (
            f"  {merkki} {muotoile_joukkue(j)}"
            + (f"  [{ranking_sarja} {sija}]" if sija else "")
        )

    osat = [f"HARJOITUSVASTUSTAJAT (paikalla {paiva})"]
    if oma_nimi:
        oma_sija = etsi_sija(ranking, oma["nimi"]) if oma and ranking else ""
        osat.append(
            f"Oma joukkue: {muotoile_joukkue(oma)}"
            + (f"  [{ranking_sarja} {oma_sija}]" if oma_sija else "")
            if oma else f"Oma joukkue: {oma_nimi} – {EI_LOYTYNYT}"
        )
    varmat, mahdolliset = harjoitusvastustajat(joukkueet, paiva)
    for otsikko, ryhma in (
        (f"Varmasti paikalla (pelaa {paiva})", varmat),
        ("Mahdollisesti paikalla (pelaa vasta seuraavana päivänä)", mahdolliset),
    ):
        osat.append(f"\n{otsikko}: {len(ryhma)}")
        if not ryhma:
            osat.append("  (ei ehdokkaita)")
            continue
        # oma sarja ensin: sen tasoja voi verrata suoraan omaan joukkueeseen
        for j in sorted(
            ryhma,
            key=lambda j: (not (oma and j["sarja"] == oma["sarja"]), j["sarja"], j["taso"]),
        ):
            if j is not oma:
                osat.append(rivi(j))
    if oma:
        osat.append("\n* = sama sarja kuin omalla joukkueella (tasot vertailukelpoisia)")
    if ranking:
        osat.append(
            f"[{ranking_sarja} …] = sija edellisessä turnauksessa; vain oman "
            "sarjan sijat haetaan, eivätkä eri sarjojen sijat ole vertailukelpoisia"
        )
    return "\n".join(osat)


def muotoile_ranking(sarja: str, ranking: list[dict]) -> str:
    """Muotoilee edellisen turnauksen paremmuusjärjestyksen."""
    if not ranking:
        return f"Edellisen turnauksen sarjataulukko: {EI_LOYTYNYT}"
    osat = [f"EDELLISEN TURNAUKSEN PARHAUSJÄRJESTYS ({sarja}):"]
    lohko = None
    for r in ranking:
        if r["lohko"] != lohko:
            lohko = r["lohko"]
            osat.append(f"  {lohko}-taso:")
        osat.append(f"    {r['sija']:2}. {r['nimi']}")
    return "\n".join(osat)


def etsi_joukkuelinkit(html: str, url: str) -> list[str]:
    """Etsii sivulta linkit, jotka todennäköisesti vievät joukkuelistaan."""
    soup = BeautifulSoup(html, "html.parser")
    linkit: dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        kohde = urljoin(url, a["href"])
        haettava = (a.get_text() + " " + kohde).lower()
        if kohde.startswith("http") and kohde != url:
            for arvo, sana in enumerate(JOUKKUELINKKI_SANAT):
                if sana in haettava:
                    linkit[kohde] = min(arvo, linkit.get(kohde, arvo))
                    break
    return sorted(linkit, key=linkit.get)


def etsi_sarjalinkit(html: str, url: str) -> dict[str, str]:
    """Tulospalvelun sarjat: sarjan nimi -> lohkotilanteiden osoite.

    Valikko on litteä linkkilista, jossa sarjan nimi (esim. "Miehet 60+")
    edeltää sen alilinkkejä, joten nimi luetaan viimeisimmästä otsikosta.
    """
    soup = BeautifulSoup(html, "html.parser")
    sarjat: dict[str, str] = {}
    nykyinen = None
    for a in soup.find_all("a", href=True):
        teksti = a.get_text(strip=True)
        if a["href"].startswith("javascript") and teksti:
            nykyinen = teksti
        elif a["href"].rstrip("/").endswith("/groups") and nykyinen:
            sarjat.setdefault(nykyinen, urljoin(url, a["href"]))
    return sarjat


def etsi_tulospalvelu(html: str, url: str, syvyys: int = 2) -> dict[str, str]:
    """Etsii edellisen turnauksen tulospalvelun sarjalinkit.

    Turnaussivulta tulospalveluun voi olla useampi hyppy (esim. Woudit ->
    "Live Seuranta 2025" -> GameResults), joten linkkejä seurataan syvyyteen
    asti avainsanojen perusteella.
    """
    sarjat = etsi_sarjalinkit(html, url)
    if sarjat or syvyys <= 0:
        return sarjat
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        kohde = urljoin(url, a["href"])
        haettava = (a.get_text() + " " + kohde).lower()
        if not kohde.startswith("http") or kohde == url:
            continue
        if not any(sana in haettava for sana in TULOSLINKKI_SANAT):
            continue
        LOKI.info("etsitään tulospalvelua linkistä %s", kohde)
        try:
            sarjat = etsi_tulospalvelu(hae_sivu(kohde), kohde, syvyys - 1)
        except HakuVirhe:
            continue
        if sarjat:
            return sarjat
    return {}


def poimi_ranking_llm(html: str, malli: str | None = None) -> list[dict]:
    """Poimii sarjan lohkotilanteista joukkueiden lopullisen järjestyksen.

    Palauttaa listan {"sija", "lohko", "nimi"}. Järjestys luetaan sivulta
    (lohkotaulukot ja sijoitusottelut); sitä ei arvata.
    """
    LOKI.info("LLM-kysely: edellisvuoden sarjataulukko (%s)", valitse_malli(malli))
    raaka = kysy_llm(
        "Luet turnaussarjan lohkotilanteita ja sijoitusotteluita. Vastaat aina "
        "pelkkänä rivilistana ilman selityksiä tai koodiaitoja.",
        "Sivulla on yhden sarjan lohkotaulukot ja mahdolliset sijoitusottelut. "
        "Muodosta joukkueiden lopullinen paremmuusjärjestys, yksi joukkue "
        "riviä kohti, muodossa\n"
        "sija | lohko | nimi\n"
        "esimerkiksi\n"
        "1 | A | Wanhat Ketterät 60\n"
        "- Käytä sijoitusotteluiden tuloksia: ottelun 'sijat 1-2' voittaja on "
        "sija 1 ja häviäjä sija 2, ja niin edelleen.\n"
        "- Jos sijoitusotteluita ei ole, käytä lohkotaulukon järjestystä.\n"
        "- lohko: tasoryhmä, jossa joukkue pelasi (esim. A1, A2, B). Numeroi "
        "eri tasoryhmät erikseen: A-tason joukkueet ensin sijoiltaan 1..n, "
        "sitten B-tason joukkueet omalta sijaltaan 1..n.\n"
        "Älä keksi joukkueita äläkä arvaa sijoja: jos sivulla ei ole "
        "taulukoita, älä palauta yhtään riviä.",
        html,
        malli,
        max_tokens=2000,
    )
    tulos = []
    for rivi in raaka.splitlines():
        if "|" not in rivi:
            continue
        *alku, nimi = (o.strip() for o in rivi.split("|"))
        sija, lohko = (alku + ["", ""])[:2]
        if nimi and sija.isdigit():
            tulos.append({"sija": int(sija), "lohko": lohko, "nimi": nimi})
    return tulos


def hae_ranking(
    url: str, sarja: str, malli: str | None = None, html: str | None = None
) -> tuple[str, list[dict]]:
    """Hakee edellisen turnauksen paremmuusjärjestyksen annetulle sarjalle.

    Palauttaa (sarjan nimi tulospalvelussa, joukkueet järjestyksessä).
    """
    if html is None:
        html = hae_sivu(url)
    sarjat = etsi_tulospalvelu(html, url)
    if not sarjat:
        return "", []
    # "60+" osuu tulospalvelun sarjaan "Miehet 60+"
    nimi = next((n for n in sarjat if sarja and sarja in n), "")
    if not nimi:
        LOKI.info("sarjaa %r ei löytynyt tulospalvelusta: %s", sarja, list(sarjat))
        return "", []
    try:
        return nimi, poimi_ranking_llm(hae_sivu(sarjat[nimi]), malli)
    except (HakuVirhe, anthropic.AnthropicError, RuntimeError) as virhe:
        LOKI.info("sarjataulukon haku epäonnistui: %s", virhe)
        return nimi, []


def poimi_joukkueet_sivulta(html: str, malli: str | None = None) -> list[dict]:
    """Poimii yhden sivun joukkueet: LLM:llä jos API-avain on asetettu,
    muuten heuristiikoilla (jolloin vain nimi täyttyy).
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            joukkueet = poimi_joukkueet_llm(html, malli)
        except (anthropic.AnthropicError, RuntimeError):
            joukkueet = []
        if joukkueet:
            return joukkueet
    return poimi_joukkueet(html)


def etsi_joukkueet(url: str, malli: str | None = None, html: str | None = None) -> list[dict]:
    """Vaihe 2: etsii turnaussivulta ilmoittautuneet joukkueet.

    Katsoo ensin itse turnaussivun ja seuraa sitten linkkejä alasivuille
    tai ulkoisiin palveluihin, kunnes joukkuelista löytyy. Valmiiksi haetun
    turnaussivun voi antaa html-parametrina.
    """
    if html is None:
        html = hae_sivu(url)
    joukkueet = poimi_joukkueet_sivulta(html, malli)
    if joukkueet:
        return joukkueet
    for linkki in etsi_joukkuelinkit(html, url):
        LOKI.info("joukkueita ei pääsivulla, seurataan linkkiä %s", linkki)
        try:
            joukkueet = poimi_joukkueet_sivulta(hae_sivu(linkki), malli)
        except HakuVirhe:
            continue
        if joukkueet:
            return joukkueet
    return []


def taydenna_anthropic(
    jarjestelma: str, sisalto: str, malli: str | None, max_tokens: int
) -> str:
    """Anthropic-toteutus LLM-rajapinnalle. Vaatii ANTHROPIC_API_KEY:n."""
    client = anthropic.Anthropic()
    alku = time.monotonic()
    vastaus = client.messages.create(
        model=valitse_malli(malli),
        max_tokens=max_tokens,
        system=jarjestelma,
        messages=[{"role": "user", "content": sisalto}],
    )
    kirjaa_kaytto(valitse_malli(malli), time.monotonic() - alku,
                  getattr(vastaus, "usage", None))
    if vastaus.stop_reason == "refusal":
        raise RuntimeError("LLM kieltäytyi vastaamasta")
    return "".join(b.text for b in vastaus.content if b.type == "text").strip()


def kirjaa_kaytto(malli: str, kesto: float, usage) -> None:
    """Lokittaa yhden LLM-kutsun keston, tokenit ja hinnan; kasvattaa kertymää."""
    if usage is None:
        return
    syote, tuloste = usage.input_tokens, usage.output_tokens
    KAYTTO["syote"] += syote
    KAYTTO["tuloste"] += tuloste
    hinta = HINNAT.get(malli)
    if hinta:
        usd = syote * hinta[0] / 1e6 + tuloste * hinta[1] / 1e6
        KAYTTO["usd"] += usd
        LOKI.info("  vastaus %.1f s, %d + %d tokenia, %.4f $", kesto, syote, tuloste, usd)
    else:
        LOKI.info("  vastaus %.1f s, %d + %d tokenia (hinnasto ei tunne mallia %s)",
                  kesto, syote, tuloste, malli)


# LLM-tarjoajat: nimi -> täydennysfunktio(jarjestelma, sisalto, malli, max_tokens).
# Uusi tarjoaja (esim. paikallinen Gemma Ollamalla) lisätään tähän.
TAYDENTAJAT = {"anthropic": taydenna_anthropic}


def valitse_taydentaja():
    """Valitsee LLM-tarjoajan TURNAUSLUOTAIN_PROVIDER-muuttujalla (oletus anthropic)."""
    nimi = os.environ.get("TURNAUSLUOTAIN_PROVIDER", "anthropic")
    try:
        return TAYDENTAJAT[nimi]
    except KeyError:
        raise ValueError(
            f"Tuntematon LLM-tarjoaja {nimi!r}; tuetut: {', '.join(sorted(TAYDENTAJAT))}"
        ) from None


def kysy_llm(
    jarjestelma: str,
    kysymys: str,
    html: str,
    malli: str | None = None,
    max_tokens: int = 1000,
) -> str:
    """Kysyy konfiguroidulta LLM-tarjoajalta kysymyksen sivun sisällöstä."""
    otsikko, rivit = poimi_teksti(html)
    teksti = "\n".join(rivit).replace("\xa0", " ")
    sisalto = (
        f"{kysymys}\n\n"
        f"Sivun otsikko: {otsikko}\n\n"
        f"Sivun sisältö:\n{teksti}"
    )
    return valitse_taydentaja()(jarjestelma, sisalto, malli, max_tokens)


def jasenna_json(raaka: str):
    """Jäsentää LLM:n vastauksen JSONiksi; sietää koodiaidat (```json ... ```)."""
    return json.loads(re.sub(r"^```\w*\s*|\s*```$", "", raaka))


def jasenna_joukkuerivit(raaka: str) -> list[dict]:
    """Jäsentää rivimuotoisen joukkuelistan ("sarja | taso | päivät | nimi").

    Rivit ilman |-erotinta ohitetaan, joten koodiaidat tai selitysteksti
    ("Sivulla ei ole joukkuelistaa.") eivät päädy joukkueiksi. Nimi luetaan
    viimeisestä kentästä, joten puuttuvat kentät eivät siirrä sitä.
    """
    joukkueet = []
    for rivi in raaka.splitlines():
        if "|" not in rivi:
            continue
        *alku, nimi = (o.strip() for o in rivi.split("|"))
        sarja, taso, paivat = (alku + ["", "", ""])[:3]
        if nimi:
            joukkueet.append(joukkue(nimi, sarja, taso, paivat))
    return joukkueet


def poimi_joukkueet_llm(html: str, malli: str | None = None) -> list[dict]:
    """Poimii sivulta ilmoittautuneet joukkueet LLM:llä.

    Palauttaa listan sanakirjoja {"nimi", "sarja", "taso", "paivat"}; kentät
    ovat tyhjiä, jos ne eivät näy sivulla. Tyhjä lista, jos sivulla ei ole
    joukkuelistaa.

    Vastaus pyydetään riveinä eikä JSONina: sama tieto vie noin kolmasosan
    tulostetokeneista, ja tämä on ajon kallein kutsu.
    """
    LOKI.info("LLM-kysely: ilmoittautuneet joukkueet (%s)", valitse_malli(malli))
    raaka = kysy_llm(
        "Poimit harrasteturnausten www-sivuilta turnaukseen ilmoittautuneet "
        "joukkueet. Vastaat aina pelkkänä rivilistana ilman selityksiä, "
        "numerointia tai koodiaitoja.",
        "Poimi sivulta turnaukseen ilmoittautuneet tai osallistuvat "
        "joukkueet, yksi joukkue riviä kohti, muodossa\n"
        "sarja | taso | päivät | nimi\n"
        "esimerkiksi\n"
        "60+ | A1 | la-su | Hiki-Hockey Seniors\n"
        "M40 SM | | | IF Gnistan\n"
        "- sarja: sarja tai ikäluokka, johon joukkue kuuluu (esim. 60+)\n"
        "- taso: sarjan sisäinen tasoryhmä tai lohko, jos sellainen on "
        "merkitty joukkueen kohdalle (esim. A1, A2, B, B1)\n"
        "- päivät: viikonpäivät, joina joukkue pelaa, jos ne on merkitty "
        "(esim. to-pe, la-su)\n"
        "- nimi: joukkueen nimi, aina viimeisenä\n"
        "Jätä kenttä tyhjäksi, jos tieto ei käy ilmi; älä arvaa. Kirjoita "
        "|-merkit myös tyhjien kenttien ympärille. Älä keksi joukkueita: "
        "jos sivulla ei ole joukkuelistaa, älä palauta yhtään riviä.",
        html,
        malli,
        max_tokens=4000,
    )
    return jasenna_joukkuerivit(raaka)


def analysoi_llm(html: str, malli: str | None = None) -> dict:
    """Poimii turnauksen perustiedot LLM:llä; palauttaa saman muotoisen
    sanakirjan kuin analysoi(), joten muotoile() toimii kummankin tuloksella.
    """
    LOKI.info("LLM-kysely: perustiedot (%s)", valitse_malli(malli))
    raaka = kysy_llm(
        "Poimit harrasteturnausten www-sivuilta turnauksen perustiedot. "
        "Vastaat aina pelkällä JSON-oliolla ilman selityksiä tai koodiaitoja.",
        "Poimi sivulta turnauksen perustiedot JSON-oliona, jonka muoto on\n"
        '{"laji": "...", "ajankohta": "...", "paikkakunta": "...", '
        '"sarjat": ["..."], "ilmoittautuminen": ["..."]}\n'
        "- laji pienellä alkukirjaimella (esim. jalkapallo, jääkiekko)\n"
        "- ajankohta on turnauksen pelipäivät, EI pelaajien ikärajoihin "
        "liittyviä syntymäpäiviä; jos sarjoilla on eri pelipäivät, anna "
        "kokonaisväli tai lyhyt kuvaus\n"
        "- sarjat: sarjojen tai ikäluokkien nimet listana\n"
        "- ilmoittautuminen: enintään viisi tiivistä riviä (miten ja mihin "
        "mennessä ilmoittaudutaan, yhteystieto, maksu)\n"
        "- jos tieto ei käy ilmi sivulta, käytä tyhjää merkkijonoa tai "
        "tyhjää listaa; älä keksi mitään",
        html,
        malli,
        max_tokens=2000,
    )
    tiedot = jasenna_json(raaka)
    otsikko, rivit = poimi_teksti(html)
    return {
        "otsikko": otsikko or (rivit[0] if rivit else EI_LOYTYNYT),
        "laji": tiedot.get("laji") or EI_LOYTYNYT,
        "ajankohta": tiedot.get("ajankohta") or EI_LOYTYNYT,
        "paikkakunta": tiedot.get("paikkakunta") or EI_LOYTYNYT,
        "sarjat": tiedot.get("sarjat") or [EI_LOYTYNYT],
        "ilmoittautuminen": tiedot.get("ilmoittautuminen") or [EI_LOYTYNYT],
    }


def tiivista_llm(html: str, malli: str | None = None) -> str:
    """Tuottaa sivusta parin lauseen suomenkielisen tiivistelmän LLM:llä."""
    LOKI.info("LLM-kysely: vapaamuotoinen tiivistelmä (%s)", valitse_malli(malli))
    return kysy_llm(
        "Tiivistät harrasteturnausten www-sivuja suomeksi. Vastaat aina "
        "pelkällä tiivistelmällä ilman johdantoa tai jälkisanoja.",
        "Tiivistä parilla lauseella, millainen turnaus tällä "
        "sivulla kuvataan: laji, ajankohta, paikkakunta ja "
        "kenelle turnaus on suunnattu.",
        html,
        malli,
    )


def analysoi_sivu(html: str, malli: str | None = None) -> dict:
    """Analysoi sivun perustiedot: LLM:llä jos API-avain on asetettu,
    muuten heuristiikoilla. LLM:n epäonnistuessa pudotaan heuristiikkoihin.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return analysoi_llm(html, malli)
        except (anthropic.AnthropicError, json.JSONDecodeError, RuntimeError):
            pass
    return analysoi(html)


def tiivista(
    url: str,
    malli: str | None = None,
    paiva: str | None = None,
    oma_joukkue: str | None = None,
    hae_sijat: bool = False,
) -> str:
    html = hae_sivu(url)
    osat = [muotoile(analysoi_sivu(html, malli))]
    joukkueet = etsi_joukkueet(url, malli, html=html)

    sarja_nimi, ranking = "", []
    if hae_sijat and joukkueet:
        oma = next(
            (j for j in joukkueet if oma_joukkue.lower() in j["nimi"].lower()), None
        )
        if oma:
            sarja_nimi, ranking = hae_ranking(url, oma["sarja"], malli, html=html)

    if not joukkueet:
        osat.append(f"Ilmoittautuneet joukkueet: {EI_LOYTYNYT}")
    elif paiva:
        osat.append(
            muotoile_harjoitusvastustajat(
                joukkueet, paiva, oma_joukkue, ranking, sarja_nimi
            )
        )
    else:
        osat.append(f"Ilmoittautuneet joukkueet ({len(joukkueet)}):")
        osat.extend(f"  - {muotoile_joukkue(j)}" for j in joukkueet)
    if hae_sijat:
        osat.append(muotoile_ranking(sarja_nimi, ranking))
    osat.append(f"LLM-tiivistelmä ({valitse_malli(malli)}):")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        osat.append("  ei käytettävissä (ANTHROPIC_API_KEY puuttuu)")
    else:
        try:
            osat.append(f"  {tiivista_llm(html, malli)}")
        except (anthropic.APIError, RuntimeError) as virhe:
            osat.append(f"  epäonnistui: {virhe}")
    return "\n".join(osat)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tuottaa suomenkielisen tiivistelmän harrasteturnauksen www-sivusta."
    )
    parser.add_argument("url", help="turnaussivun osoite")
    parser.add_argument(
        "--model",
        help=f"LLM-malli (oletus TURNAUSLUOTAIN_MODEL tai {OLETUSMALLI})",
    )
    parser.add_argument(
        "--paikalla",
        metavar="PÄIVÄ",
        choices=VIIKONPAIVAT,
        help="listaa joukkueluettelon sijaan harjoitusvastustajaehdokkaat, "
             "jotka ovat paikalla annettuna päivänä (esim. to)",
    )
    parser.add_argument(
        "--joukkue",
        metavar="NIMI",
        help="oma joukkue; sen sarja nostetaan --paikalla-listassa ensimmäiseksi",
    )
    parser.add_argument(
        "--sijat",
        action="store_true",
        help="hae oman sarjan parhausjärjestys edellisestä turnauksesta "
             "(vaatii --joukkue)",
    )
    args = parser.parse_args()
    if args.joukkue and not (args.paikalla or args.sijat):
        parser.error("--joukkue toimii vain --paikalla- tai --sijat-lipun kanssa")
    if args.sijat and not args.joukkue:
        parser.error("--sijat tarvitsee --joukkue-lipun tietääkseen sarjan")
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO,
        format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    lataa_env()
    try:
        print(tiivista(args.url, args.model, args.paikalla, args.joukkue, args.sijat))
    except HakuVirhe as virhe:
        print(f"Sivun haku epäonnistui: {virhe}", file=sys.stderr)
        return 1
    if KAYTTO["syote"]:
        LOKI.info(
            "LLM-käyttö yhteensä: %d syöte- ja %d tulostetokenia, ~%.4f $",
            KAYTTO["syote"], KAYTTO["tuloste"], KAYTTO["usd"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
