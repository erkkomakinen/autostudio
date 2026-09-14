# Testaus 14.9.2026

Kaikki toiminnot testattiin rajapinnan kautta (Python-skripti) ja käyttöliittymästä (Playwright, Chromium:
asiakas iPhone 13 -emulaatiossa, ylläpito 1440 px ja 400 px). Kaikki kulut ovat euroja.

| Osa-alue | Tarkistuksia | Tulos |
|---|---|---|
| Asiakkaan rajapinta (lataus, HEIC, nimeäminen, poisto, korjaus, alkuperäinen, ZIP) | 43 | kaikki läpi |
| Ylläpidon rajapinta (yleiskatsaus, yritykset, erät, versiot, palautteet, asetukset) | 26 | kaikki läpi |
| Tyylieditori (kopio, arvot, logo, lattia, esikatselut) | 17 | kaikki läpi |
| Kuvakatto | 4 | kaikki läpi |
| Varamalli | 2 | kaikki läpi |
| Käyttöliittymä (asiakas + ylläpito, vaakavieritys, konsolivirheet) | 40 | kaikki läpi korjausten jälkeen |
| Salasanasuojaus ja poistetut vanhat reitit | 13 | kaikki läpi |

## Testauksessa löytyneet ja korjatut viat

1. **Kuvausohje aukesi uudelleen**, jos sivu ladattiin heti ohjeen sulkemisen jälkeen. Ohje merkitään nyt nähdyksi jo avattaessa.
2. **Kuvakaton hälytys laski eri asiaa kuin katto**: hälytys laski kaikki kuvat, katto vain tekoälyllä käsitellyt. Nyt molemmat laskevat samoin, ja täysi katto näkyy kriittisenä hälytyksenä.
3. **Tyylieditorin esikatselu näytti saman valokuvan kahdesti**, kun sama kuva oli ladattu kahteen autoon. Nyt valitaan kaksi eri valokuvaa.
4. **Tyylieditori leveni puhelimella 518 px:iin**, koska lattiaosion painikerivi ei rivittynyt. Korjattu.
5. **Erän kuvarivin painikkeet ylittivät kortin reunan** 1440 px:n näytöllä. Korjattu.
6. **"Lataa oma kuva" -painikkeen fontti oli pienempi** kuin viereisen painikkeen. Korjattu.
7. **Hylätty lataus jätti tyhjän auton listaan**, jos yksikään tiedosto ei ollut kuva. Tyhjä auto poistetaan nyt.

## Mittaukset (RTX 3050 Ti, Gemini 3.1 Flash Image)

| Toiminto | Aika | Kulu |
|---|---|---|
| 4 kuvaa latauksesta valmiiksi (3 ulkokuvaa + sisäkuva, yksi HEIC) | 48 s | 0,18 € |
| Yksi ulkokuva vastaanotosta valmiiksi | 35–48 s | 0,059 € |
| Korjauspyyntö tekstillä | 18 s | 0,059 € |
| Viimeistelyn päivitys 5 kuvan erään (ei tekoälyä) | 9,6 s | 0 € |
| Tyylin ilmainen esikatselu (seinä, logo) | 4,8 s | 0 € |
| Tyylin tekoälyesikatselu | 18 s | 0,059 € / kuva |
| Uuden lattian generointi (Seedream 5 Pro) | 76 s | 0,077 € |
| Varamalli Seedream 5 Pro yhdelle kuvalle | 100 s | 0,083 € |

Testauksen tekoälykulut yhteensä noin 0,75 €.

## Havainnot ja suositukset

### Laatu
- **Oma lattiakuva vaatii oikeanlaisen kuvan.** Testissä lattiaksi ladattiin tavallinen autokuva. Lattiasta tuli vaalea ja auto näytti leijuvan, koska kosketusvarjo jäi heikoksi. Editoriin lisättiin ohje: suoraan ylhäältä kuvattu materiaali ilman saumoja ja heijastuksia. Generointi tekoälyllä toimi hyvin (tumma antrasiittigraniitti, saumat auton suuntaisesti).
- **Kuvakaton ylittyessä** uudet ulkokuvat tehdään lasketulla pohjalla ilman tekoälyä, joten laatu on selvästi heikompi. Ylläpito näkee merkinnän "Ilman tekoälyä: kuvakatto" ja saa hälytyksen. Asiakkaalle tästä ei kerrota. Kun kattoa nostetaan, kuvat pitää tehdä uudelleen ylläpidosta (ei tapahdu automaattisesti).
- **Tekoälyvirhe korjauksessa**: jos malli ja varamalli epäonnistuvat, kuvaan käytetään edellistä tekoälytaustaa. Silloinkin syntyy uusi versio, vaikka kuva ei muutu. Virhe näkyy ylläpidossa punaisena.
- **Varamalli on hidas**: Seedream 5 Pro kestää noin 100 s, kun Gemini kestää noin 11 s. Se sopii varajärjestelmäksi, mutta ei jatkuvaan käyttöön.

### Toiminta
- Jo käsitellyn kuvan korjaus ei kuluta kuvakattoa. Katto koskee vain uusia ulkokuvia, joita ei ole vielä käsitelty tekoälyllä.
- Asiakkaan poistamien kuvien kulut näkyvät edelleen raporteissa, koska ne on jo maksettu. Ylläpito voi palauttaa poistetun kuvan asiakkaalle.
- Virheellinen kuukausi osoitteessa (`?month=kissa`) näyttää kuluvan kuukauden. Tämä on tarkoituksellista.
- "Päivitä ulkoasu kaikkiin" erän sivulla odottaa, kunnes kaikki kuvat on käsitelty (noin 2 s/kuva). Yli 50 kuvan erässä pyyntö voi aikakatkaista, joten se kannattaa siirtää taustatehtäväksi ennen isoja asiakkaita. Tyylieditorin vastaava painike toimii jo taustalla.
- Pois käytöstä olevan yrityksen linkki näyttää vain viestin. Siihen voisi lisätä palveluntarjoajan yhteystiedot.

### Ei voitu testata tässä ympäristössä
- **Sähköposti-ilmoitukset**: SMTP-asetuksia ei ole asetettu, joten ilmoituksia ei lähetetty. Ne on asetettava palvelimelle ja testattava yhdellä korjauspyynnöllä.
- **Tallennus puhelimeen (Web Share)** ja **kotinäyttösovellus** testattiin vain emulaatiossa. Ne kannattaa kokeilla oikealla iPhonella ja Android-puhelimella: Tallenna kaikki → Tallenna kuviin.
- **Pitkä painallus alkuperäiseen kuvaan** testattiin hiirellä, ei oikealla kosketusnäytöllä.

### Ennen käyttöönottoa
- **Aseta `AUTOSTUDIO_USER` ja `AUTOSTUDIO_PASSWORD`.** Kehityspalvelimella ylläpito on nyt avoin. Suojaus testattiin erillisellä palvelimella: ylläpito, `/docs` ja `/openapi.json` vaativat tunnukset, asiakkaan linkki ei.
- **SF Tradingin asiakaslinkissä näkyy testiautoja** ("Audi A4 Avant OVK-766", "Kuvat 14.9. klo 22.18"). Poista ne tai vaihda linkki ennen kuin linkki annetaan asiakkaalle.
- HTTPS on pakollinen, koska linkki on asiakkaan ainoa tunniste.

## Tehdyt muutokset tässä vaiheessa
- Kulut tallennetaan euroina kutsuhetken kurssilla (`cost_eur`, `cost_total_eur`). Kurssi on Asetuksissa. Vanhat dollarikulut muunnettiin (`scripts/migrate_eur.py`, 1 erä).
- Asiakasnäkymä: autolista kansikuvineen, uusi auto nimellä, nimeäminen, edistymispalkki, lisää kuvia samaan autoon, kuvan poisto kahdella napautuksella, kuvausohje, kotinäyttösovellus, pois käytöstä -viesti.
- Ylläpito: tyylieditori (seinä ja logo ilmaisella esikatselulla, sijoittelu ja laatat, lattian generointi tai lataus, tekoälyesikatselu, kopiointi, päivitys yritysten kuviin), asetukset (kurssi, varamalli, tilat), kuvakaton seuranta, poistettujen kuvien palautus.
- Kuvakatto ja varamalli toteutettu käsittelyyn.
- Vanha näkymä poistettu (`/vanha`, `index.html`, `app.js`, `style.css`, `assistant.py` ja vanhat `/api/jobs`-reitit). Tiedostot siirrettiin talteen istunnon väliaikaiskansioon.
- Testidata (Testiautot Oy, Testityyli, UI-testiautot) siirrettiin pois `data/`- ja `assets/`-kansioista, ja testipalautteet kuitattiin.
