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


def poimi_joukkueet(html: str) -> list[str]:
    """Poimii sivulta joukkuelistan: ensin tekstiriveistä, sitten tauluista.

    Sivu tulkitaan joukkuelistaksi vain, jos osumia on vähintään
    MIN_JOUKKUEITA – yksittäiset osumat ovat yleensä muuta sisältöä.
    """
    joukkueet = poimi_joukkueet_riveista(html) or poimi_joukkueet_tauluista(html)
    return joukkueet if len(joukkueet) >= MIN_JOUKKUEITA else []


def poimi_joukkueet_riveista(html: str) -> list[str]:
    """Poimii tekstistä joukkuerivit muodossa "Nimi (Paikkakunta)"."""
    _, rivit = poimi_teksti(html)
    joukkueet = []
    for rivi in rivit:
        rivi = re.sub(r"\s+", " ", rivi.replace("\xa0", " ")).strip()
        osuma = JOUKKUE_RIVI.match(rivi)
        if osuma and len(osuma.group(2).split()) <= 2:
            joukkueet.append(f"{osuma.group(1).strip()} ({osuma.group(2).strip()})")
    return joukkueet


def poimi_joukkueet_tauluista(html: str) -> list[str]:
    """Poimii joukkueet taulukkoriveistä ilmoittautumissivulta (esim. Taso).

    Taulukkosolut voivat olla mitä vain (aikatauluja, tuloksia), joten
    poiminta tehdään vain sivulta, joka otsikoi itsensä ilmoittautuneiden
    listaksi.
    """
    soup = BeautifulSoup(html, "html.parser")
    otsikko = soup.title.get_text() if soup.title else ""
    if "ilmoittautuneet" not in otsikko.lower():
        return []
    joukkueet = []
    for rivi in soup.find_all("tr"):
        solut = [td.get_text(strip=True) for td in rivi.find_all("td")]
        nimi = next((s for s in reversed(solut) if s), "")
        if nimi and nimi not in joukkueet:
            joukkueet.append(nimi)
    return joukkueet


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


def poimi_joukkueet_sivulta(html: str, malli: str | None = None) -> list[str]:
    """Poimii yhden sivun joukkueet: LLM:llä jos API-avain on asetettu,
    muuten heuristiikoilla. LLM:n sanakirjat muotoillaan "Nimi (sarja)"
    -riveiksi.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            joukkueet = poimi_joukkueet_llm(html, malli)
        except (anthropic.AnthropicError, RuntimeError):
            joukkueet = []
        if joukkueet:
            return [
                f"{j['nimi']} ({j['sarja']})" if j.get("sarja") else j["nimi"]
                for j in joukkueet
            ]
    return poimi_joukkueet(html)


def etsi_joukkueet(url: str, malli: str | None = None, html: str | None = None) -> list[str]:
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
    """Jäsentää rivimuotoisen joukkuelistan ("sarja | nimi") sanakirjoiksi.

    Rivit ilman |-erotinta ohitetaan, joten koodiaidat tai selitysteksti
    ("Sivulla ei ole joukkuelistaa.") eivät päädy joukkueiksi.
    """
    joukkueet = []
    for rivi in raaka.splitlines():
        sarja, erotin, nimi = rivi.partition("|")
        nimi = nimi.strip()
        if erotin and nimi:
            joukkueet.append({"nimi": nimi, "sarja": sarja.strip()})
    return joukkueet


def poimi_joukkueet_llm(html: str, malli: str | None = None) -> list[dict]:
    """Poimii sivulta ilmoittautuneet joukkueet LLM:llä.

    Palauttaa listan sanakirjoja {"nimi": ..., "sarja": ...}; sarja on tyhjä
    merkkijono, jos se ei näy sivulla. Tyhjä lista, jos sivulla ei ole
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
        "sarja | nimi\n"
        "esimerkiksi\n"
        "60+ | Hiki-Hockey Seniors\n"
        "Sarja on sarja tai lohko, johon joukkue kuuluu; jätä se tyhjäksi "
        "(rivi alkaa |-merkillä), jos se ei käy ilmi. Älä keksi joukkueita: "
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


def tiivista(url: str, malli: str | None = None) -> str:
    html = hae_sivu(url)
    osat = [muotoile(analysoi_sivu(html, malli))]
    joukkueet = etsi_joukkueet(url, malli, html=html)
    if joukkueet:
        osat.append(f"Ilmoittautuneet joukkueet ({len(joukkueet)}):")
        osat.extend(f"  - {j}" for j in joukkueet)
    else:
        osat.append(f"Ilmoittautuneet joukkueet: {EI_LOYTYNYT}")
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
    args = parser.parse_args()
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO,
        format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    lataa_env()
    try:
        print(tiivista(args.url, args.model))
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
