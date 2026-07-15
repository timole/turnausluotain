# Turnausluotain

Agentti, joka ottaa parametrina harrasteturnauksen www-sivun URL:in ja tuottaa
suomenkielisen tiivistelmän turnauksesta: laji, sarjat, ajankohta, paikkakunta
ja ilmoittautumistiedot.

Esimerkkisyöte: https://www.woudit.fi/etusivu/saimaa-turnaus/

## Työskentelytapa

- Työskentelemme pala kerrallaan: yksi selkeästi rajattu tehtävä per pyyntö.
- Ehdota aina tehtävälista (3–5 pientä askelta), toteuta niistä yksi, ja kysy
  haluanko jatkaa seuraavaan.
- Jokaisen onnistuneen askeleen jälkeen: commit.
- Kerro aina lopuksi yhdellä lauseella: mitä tehtiin, mikä on seuraava askel.
- Jos tehtävä paisuu, pysähdy ja ehdota pilkkomista.

## Ympäristö

- Python 3.14, virtuaaliympäristö kansiossa `.venv/`
- Riippuvuudet: `requests`, `beautifulsoup4`, `anthropic`, `pytest`
  (ks. `requirements.txt`)
- Konfiguraatio tiedostossa `.env` (ei versionhallinnassa): kopioi pohjaksi
  `.env.example` ja täytä `ANTHROPIC_API_KEY`; valinnainen
  `TURNAUSLUOTAIN_MODEL` vaihtaa LLM-mallin (oletus `claude-haiku-4-5`) ja
  `TURNAUSLUOTAIN_PROVIDER` LLM-tarjoajan (toistaiseksi vain `anthropic`)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Ajaminen

```bash
.venv/bin/python turnausluotain.py <turnauksen-url>
.venv/bin/python turnausluotain.py --model claude-opus-4-8 <turnauksen-url>
```

Mallin etusijajärjestys: `--model` > `TURNAUSLUOTAIN_MODEL` (shell-ympäristö
voittaa `.env`-tiedoston) > oletus `claude-haiku-4-5`.

## Arkkitehtuuri (MVP)

Kaikki on yhdessä tiedostossa `turnausluotain.py`:

1. **Haku** – sivu haetaan `requests`illa (selain-User-Agent, timeout).
2. **Tekstin poiminta** – `BeautifulSoup` riisuu script/style/nav-elementit ja
   tuottaa rivipohjaisen tekstin sivun `<main>`/`<body>`-osasta.
3. **Perustietojen analyysi** – `analysoi_sivu(html, malli)` poimii laji-,
   ajankohta-, paikkakunta-, sarja- ja ilmoittautumistiedot LLM:llä
   (`analysoi_llm`, JSON-olio); ilman API-avainta tai LLM:n epäonnistuessa
   varapolkuna heuristinen `analysoi(html)`, joka poimii:
   - laji avainsanalistalla (jääkiekko, salibandy, jalkapallo, ...)
   - ajankohta päivämäärä-regexeillä (esim. `30.7 – 02.08.2026`)
   - paikkakunta vertaamalla tekstiä suomalaisten kaupunkien listaan
   - sarjat "Sarjat"-otsikon jälkeisistä riveistä
   - ilmoittautumistiedot (yhteyshenkilö, sähköposti, puhelin, maksu)
4. **LLM-tiivistelmä** – `tiivista_llm(html, malli)` pyytää Anthropicin
   mallilta (ks. Ajaminen; oletus `claude-haiku-4-5`) parin lauseen
   suomenkielisen tiivistelmän sivusta. Vaatii `ANTHROPIC_API_KEY`:n (luetaan
   `.env`-tiedostosta); ilman avainta CLI toimii pelkillä heuristiikoilla.
5. **Ilmoittautuneet joukkueet** – `etsi_joukkueet(url, malli, html)` seuraa
   tarvittaessa linkkejä alasivuille/ulkoisiin palveluihin ja poimii joukkueet
   LLM:llä (`poimi_joukkueet_llm`); ilman API-avainta varapolkuna heuristiikat.
6. **Tulostus** – suomenkielinen tiivistelmä stdoutiin.

Analyysi on erotettu hausta: `analysoi(html)` palauttaa tiedot sanakirjana ja
`muotoile(tulos)` tuottaa tekstin, joten analyysiä voi testata ilman verkkoa.

## Testit

BDD-tyyliset regressiotestit (`tests/test_regressio.py`, skenaariot
docstringeissä Given/When/Then-muodossa) hakevat oikeat sivut verkosta:

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m pytest -m "not llm"   # ilman API-kutsuja (heuristiikkapolku)
```

Conftest poistaa API-avaimen testeiltä, joita ei ole merkitty
`llm`-markerilla, joten ne testaavat aina heuristista varapolkua.

## Vaihe 2: Ilmoittautuneet joukkueet

Tavoite: luotain etsii turnaussivulta myös ilmoittautuneet joukkueet,
tarvittaessa seuraamalla linkkejä alasivuille tai ulkoisiin palveluihin
(esim. Palloliiton Taso-järjestelmä).

Linjaus: joukkueiden poiminta tehdään LLM-pohjaisesti (ei kovakoodattuja
selektoreita), jotta luotain yleistyy eri turnaussivuille.
`poimi_joukkueet_llm(html, malli)` palauttaa listan
`{"nimi": ..., "sarja": ...}` -sanakirjoja. LLM-testit on merkitty
pytest-markerilla `llm` (aja ilman niitä: `pytest -m "not llm"`).

Hyväksymiskriteerit:

1. `https://www.woudit.fi/etusivu/saimaa-turnaus/` → löytää
   ilmoittautuneista joukkueen "Hiki-Hockey Seniors".
2. `https://www.palloliitto.fi/kilpailut/turnaukset-ja-lopputurnaukset/kki-lopputurnaukset`
   → löytää joukkueen "Gnistan" ilmoittautumissivulta
   `https://taso.palloliitto.fi/taso/ilmoittautuneet.php?turnaus=splkki26`.

Testihavainnot Palloliitto-sivulta (heuristiikkojen puutteet, jotka
LLM-pohjainen `analysoi_llm` korjaa; regressiotesti varmistaa):

- Ajankohta-regex poimi ikärajapäivämäärän (31.12.1996) turnauspäivän sijaan.
- Sarjat (M35–M75) jäivät löytymättä.
- Ilmoittautumisosio keräsi liikaa sisältöä.

## Backlog

Kun käyttäjä sanoo "backlogille: X", lisää X tähän listaan yhdellä rivillä
kysymättä lisää.

- Ollama-tarjoaja (esim. paikallinen Gemma): toteutus `TAYDENTAJAT`-rekisteriin.
  (Rajapinta on jo abstrahoitu: `valitse_taydentaja` +
  `TURNAUSLUOTAIN_PROVIDER`; Anthropic-mallin vaihto: `--model` /
  `TURNAUSLUOTAIN_MODEL`.) Lykätty 7/2026: kehityskoneen muisti ei riitä
  paikalliseen malliin.
- Joukkuepoiminnan tehostus: joukkuelistan LLM-kutsu on ajon hitain ja kallein
  osa; tiiviimpi vastausmuoto (esim. pelkät nimirivit) puolittaisi kulut.
- LLM-tulosten laadun vakautus: Haiku lipsuu pehmeissä kentissä (ajankohdan
  tarkenne, paikkakunta vaihtelee ajokerroittain); promptin tarkennus tai
  isompi malli näille kentille.
- Monta turnausta samalla sivulla: Palloliiton sivu on 12 turnauksen sarja,
  mutta tuloste litistää sen yhdeksi; rakenne voisi tukea turnauslistaa.
- JavaScript-renderöidyt sivut: esim. Playwright-haku varapoluksi, kun
  palvelimen HTML ei sisällä sisältöä.

## Huomioita

- Rakenteiset kentät (laji, ajankohta, sarjat, ...) ja joukkueet poimitaan
  ensisijaisesti LLM:llä; heuristiikat ovat varapolku, jolla CLI toimii myös
  ilman API-avainta.
- Heuristiikat on viritetty tyypillisiä suomalaisia turnaussivuja vasten;
  puuttuva tieto raportoidaan arvolla "ei löytynyt sivulta", ei kaadeta ajoa.
- JavaScript-renderöityjä sivuja MVP ei tue (vain palvelimen palauttama HTML).
