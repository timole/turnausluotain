#!/usr/bin/env python3
"""Turnausluotain – tiivistää harrasteturnauksen www-sivun suomeksi.

Käyttö: python turnausluotain.py <turnauksen-url>
"""

import re
import sys

import requests
from bs4 import BeautifulSoup

EI_LOYTYNYT = "ei löytynyt sivulta"

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


def hae_sivu(url: str) -> str:
    vastaus = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) turnausluotain/0.1"},
        timeout=30,
    )
    vastaus.raise_for_status()
    return vastaus.text


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


def tiivista(url: str) -> str:
    return muotoile(analysoi(hae_sivu(url)))


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Käyttö: {sys.argv[0]} <turnauksen-url>", file=sys.stderr)
        return 2
    try:
        print(tiivista(sys.argv[1]))
    except requests.RequestException as virhe:
        print(f"Sivun haku epäonnistui: {virhe}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
