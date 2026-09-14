# Autostudio – yhtenäiset autokuvat autoliikkeille

Autoliike kuvaa autot puhelimella ja lähettää kuvat omalla linkillään. Palvelu vaihtaa ulkokuvien
taustaksi liikkeen studion (lattia, seinä, logo), myös ikkunoiden läpi näkyvän taustan. Auton pikselit
säilyvät alkuperäisinä. Sisäkuvat jätetään ennalleen. Kaikki kulut tallennetaan euroina.

Testauksen havainnot: [TESTAUS.md](TESTAUS.md).

## Miten kuva syntyy

```
Kuva ──► 1. ANALYYSI (GPU, kerran per kuva)
          • BiRefNet → auton maski      • Grounding DINO + SAM 2.1 → ikkunat ja pyörät
          • GeoCalib → kameran kallistus ja polttoväli
          • luokittelu: ulkokuva / sisä- tai yksityiskohtakuva

     ──► 2. LASKETTU POHJA (CPU, ilmainen)
          • 3D-lattia kameran mukaan, saumat auton suuntaisesti pyörien kosketuspisteistä
          • tyylin lattiamateriaali, pehmeä kosketusvarjo, tumma seinä

     ──► 3. TEKOÄLYVIIMEISTELY (OpenRouter, n. 0,06 € / kuva)
          • kuvamalli tekee pohjasta valokuvamaisen, tyylin lattianäyte ohjeena
          • jos malli ei vastaa → varamalli (Asetukset) → muuten laskettu pohja

     ──► 4. KOKOAMINEN (CPU, ilmainen, toistettavissa)
          • alkuperäinen auto päälle pikselintarkasti, ikkunoiden taustan vaihto
          • seinä lasketaan aina itse (sama sävy kaikissa kuvissa ja malleissa)
          • logo
```

Tekoälyn raakakuva tallennetaan, joten seinän ja logon muutokset voi päivittää kaikkiin kuviin ilman
uusia tekoälykutsuja. Jokaisesta tuloksesta jää versio talteen.

## Näkymät

- **Asiakas** `/d/<linkki>`: autolista, uusi auto (nimi + kuvat), galleria, paina pohjassa = alkuperäinen,
  Korjaa-pyyntö tekstillä, alkuperäisen kuvan valinta, kuvan poisto, Tallenna kaikki (jako puhelimeen tai ZIP),
  kuvausohje, kotinäyttösovellus. Ei salasanaa: linkki on tunniste, ja sen voi vaihtaa ylläpidossa.
- **Ylläpito** `/admin`: yleiskatsaus (liikevaihto, kulut, kate, korjausprosentti, hälytykset, päiväkaavio),
  yritykset (hinta, kuvakatto, tyyli, linkki, kuukausihistoria), erät (versiot, uudelleenteko, tyypin pakotus),
  palautteet, tyylieditori (seinä, logo, sijoittelu, lattian generointi tai lataus, esikatselu), asetukset.

## Asennus (kehitys, Windows)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8765
```

Avaa http://127.0.0.1:8765/admin. Mallit (~3 Gt) latautuvat `models/`-kansioon ensimmäisellä kerralla.

## Ympäristömuuttujat

| Muuttuja | Kuvaus |
|---|---|
| `OPENROUTER_API_KEY` | Pakollinen tekoälyviimeistelyyn ja lattian generointiin |
| `AUTOSTUDIO_USER` / `AUTOSTUDIO_PASSWORD` | Ylläpidon kirjautuminen. **Aseta aina palvelimella**, muuten ylläpito on avoin |
| `AUTOSTUDIO_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_FROM` | Sähköposti-ilmoitukset |
| `AUTOSTUDIO_ADMIN_EMAIL` | Ilmoitusten vastaanottaja (korjauspyynnöt, kuvakatto) |
| `AUTOSTUDIO_DATA` | Datakansio, oletus `./data` |
| `AUTOSTUDIO_FLIP_TTA` | `0` = nopeampi rajaus ilman peilikuvakeskiarvoa |

Dollarikurssi ja varamalli ovat ylläpidon Asetuksissa (`data/settings.json`).
Vanhat dollarikulut muunnetaan kerran: `.venv/Scripts/python scripts/migrate_eur.py 0.86`.

## Rakenne

- `app/models.py`, `app/analysis.py` – tunnistusmallit ja analyysi
- `app/scene.py`, `app/textures.py` – laskettu 3D-lattia ja saumattomat tekstuurit
- `app/ai_background.py` – pohjakuva, tekoälykutsu, viimeistely, seinä ja logo
- `app/pipeline.py` – kuvan käsittely, versiot, kuvakatto, varamalli, eurokulut
- `app/worker.py` – GPU-jono ja rinnakkaiset tekoälykutsut
- `app/dealer_api.py` – asiakkaan rajapinta
- `app/admin_api.py`, `app/admin_styles.py`, `app/materials.py`, `app/settings.py` – ylläpito
- `app/storage.py`, `app/notify.py` – JSON-tallennus ja sähköposti
- `assets/styles/<tyyli>/` – tyyliprofiili (style.json, logo, lattianäyte, seinätekstuuri)
- `assets/floors/materials/` – lattiamateriaalit

## Tuotantoon

- GPU analyysiin (4 Gt riittää yhdelle jonolle), tekoälyviimeistely on ulkoinen kutsu.
- HTTPS (Nginx/Caddy eteen, `client_max_body_size 200M`) – linkki on asiakkaan ainoa tunniste.
- Lukitse BiRefNetin `trust_remote_code`-revisio, jotta mallikoodi ei vaihdu yllättäen.
- Varmuuskopioi `data/` ja `assets/styles/`.
