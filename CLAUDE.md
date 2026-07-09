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
- Riippuvuudet: `requests`, `beautifulsoup4` (ks. `requirements.txt`)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Ajaminen

```bash
.venv/bin/python turnausluotain.py <turnauksen-url>
```

## Arkkitehtuuri (MVP)

Kaikki on yhdessä tiedostossa `turnausluotain.py`:

1. **Haku** – sivu haetaan `requests`illa (selain-User-Agent, timeout).
2. **Tekstin poiminta** – `BeautifulSoup` riisuu script/style/nav-elementit ja
   tuottaa rivipohjaisen tekstin sivun `<main>`/`<body>`-osasta.
3. **Heuristinen analyysi** – tekstistä poimitaan:
   - laji avainsanalistalla (jääkiekko, salibandy, jalkapallo, ...)
   - ajankohta päivämäärä-regexeillä (esim. `30.7 – 02.08.2026`)
   - paikkakunta vertaamalla tekstiä suomalaisten kaupunkien listaan
   - sarjat "Sarjat"-otsikon jälkeisistä riveistä
   - ilmoittautumistiedot (yhteyshenkilö, sähköposti, puhelin, maksu)
4. **Tulostus** – suomenkielinen tiivistelmä stdoutiin.

Analyysi on erotettu hausta: `analysoi(html)` palauttaa tiedot sanakirjana ja
`muotoile(tulos)` tuottaa tekstin, joten analyysiä voi testata ilman verkkoa.

## Testit

BDD-tyyliset regressiotestit (`tests/test_regressio.py`, skenaariot
docstringeissä Given/When/Then-muodossa) hakevat oikeat sivut verkosta:

```bash
.venv/bin/python -m pytest -v
```

## Vaihe 2: Ilmoittautuneet joukkueet

Tavoite: luotain etsii turnaussivulta myös ilmoittautuneet joukkueet,
tarvittaessa seuraamalla linkkejä alasivuille tai ulkoisiin palveluihin
(esim. Palloliiton Taso-järjestelmä).

Hyväksymiskriteerit:

1. `https://www.woudit.fi/etusivu/saimaa-turnaus/` → löytää
   ilmoittautuneista joukkueen "Hiki-Hockey Seniors".
2. `https://www.palloliitto.fi/kilpailut/turnaukset-ja-lopputurnaukset/kki-lopputurnaukset`
   → löytää joukkueen "Gnistan" ilmoittautumissivulta
   `https://taso.palloliitto.fi/taso/ilmoittautuneet.php?turnaus=splkki26`.

Testihavainnot Palloliitto-sivulta (korjattavaa nykyisissä heuristiikoissa):

- Ajankohta-regex poimi ikärajapäivämäärän (31.12.1996) turnauspäivän sijaan.
- Sarjat (M35–M75) jäivät löytymättä.
- Ilmoittautumisosio keräsi liikaa sisältöä.

## Huomioita

- MVP ei käytä LLM:ää; tiivistelmä syntyy heuristiikoilla. Jatkossa analyysin
  voi korvata Claude API -kutsulla, jolloin vapaamuotoisemmatkin sivut
  jäsentyvät.
- Heuristiikat on viritetty tyypillisiä suomalaisia turnaussivuja vasten;
  puuttuva tieto raportoidaan arvolla "ei löytynyt sivulta", ei kaadeta ajoa.
- JavaScript-renderöityjä sivuja MVP ei tue (vain palvelimen palauttama HTML).
