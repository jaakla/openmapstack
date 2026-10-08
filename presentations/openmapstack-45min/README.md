# OpenMapStack — 45 minuti demo ja esitlus

Eesti- ja ingliskeelne esitlus tehnilise tooteettevõtte GIS-ekspertidele: 24 slaidi,
45 minuti ajastus, juhitud demo ja iga slaidi esinejamärkmed.

## Ava ja esitle

Ava `index.html` brauseris. See on üks iseseisev HTML-fail: stiilid, JavaScript,
graafika ja viis tegelikku dashboardi ekraanipilti on faili sees. Slaidid ja
interaktiivne näidis töötavad ilma serveri või võrguühenduseta.

- **ET / EN jaluses:** slaidide, esinejamärkmete, graafikute ja juhtnuppude keel.
  Valik säilib järgmisel avamisel, kui brauser lubab kohalikku salvestust.
  Keelevahetus säilitab slaidi ja interaktiivse demo seaded. Päris dashboardi
  ekraanipildid ja väline demorakendus jäävad eestikeelseks.
- **Edasi / ← → / PageDown / PageUp:** järgmine või eelmine slaid.
- **Vali slaid / G:** slaidivalik koos ajakavaga.
- **Täisekraan / F:** brauseri täisekraan. Piiratud eelvaaturis ava fail tavalises brauseris.
- **Märkmed / N:** esinejamärkmed ja slaidi kavandatud aeg; paneel on samas aknas.
- **Home / End:** esimene või viimane slaid.
- **Klõps ekraanipildil:** pildi suurendus; Escape sulgeb.

## 45 minuti läbimäng

| Slaidid | Aeg | Teema |
|---|---:|---|
| 1–10 | 0–16 min | Demoküsimus, agent, skill, andmetöö ja projektileping |
| 11–16 | 16–31 min | Võrgupõhine katvus, kaalud, dashboard, päritolu, stsenaarium |
| 17–23 | 31–43 min | Iteratsioon, brauser/QGIS, kontrollid, kulu, privaatandmed |
| 24 | 43–45 min | Arutelu ja piloodi valik |

Demo kolm käiku:

1. Slaid 11: muuda jalutusaega 15 → 10 → 20 minutit ja lisa näidisvõrku sild.
2. Slaid 12: võrdle elumajade arvu ja eluruumidega kaalutud katvust.
3. Slaid 16: võrdle päris rakenduses Tuglast, Marjat ja mõlemat silda koos.

Slaidilt 14 saab avada [Tartu Emajõe sillaanalüüsi](https://tartu-sillad.jaak-laineste.workers.dev/).
Live-demo viis vaadet on Kõik kohad, Tuglase vs Marja, Teekonnad, Kaart ja
Päritolu. Võrdle sillakohti, jalgsi/rattaga liikumist, mõjumõõdikuid ja
tundlikkuse parameetreid. Poolestusaeg (gravitatsioonipõhine ligipääsetavus) ja
ajalävi (kumulatiivne ligipääsetavus) on eri parameetrid. Rakendus vajab võrku;
katkestuse korral kasuta sisse ehitatud ekraanipilte ja näidisvõrku.

## Sisu ja tõendite piir

Algkoolide 15 minuti küsimus jääb algse analüüsi lähtejuhtumiks; selle
katvusprotsenti pole välja mõeldud. Live-demo ja viis ekraanipilti näitavad
kasutaja antud Tartu sillaanalüüsi. Pildid jäädvustati 08.10.2026; rakenduse
Päritolu vaates on käivitus `run-20261002-181138`. Kaardi omistused
Maa- ja Ruumiametile, OpenStreetMapi panustajatele ja Overture Mapsile on
ekraanipiltidel säilitatud. See on rakenduse tulemuste esitlus; analüüsi ei ole
esitluse koostamisel sõltumatult uuesti arvutatud.

Interaktiivne võrk ja katvuse graafik kasutavad selgelt märgitud sünteetilisi
andmeid: servade ajakulud, 12 elumaja ja 304 eluruumi. Arvutus on Dijkstra
lühim tee suunamata võrgus. Katvuse graafik tuleneb samast arvutusest ning
hoonete kaaludest; selle joon pole käsitsi joonistatud trend. Sünteetiline sild
lisab võrguserva. Ka päris sillaanalüüs kirjeldab Päritolu vaates sildade
lisamist jalgsi- ja rattavõrku; selle arvutus ei ole sama mis esitlusskeemi oma.

Autori antud ajakulu, tokenikulu, iteratsioonid ja ajaloolised versioonid on
märgitud demokogemusena. Mudelite üldist paremusjärjestust ega muutuvat
hinnakirja ei esitata kontrollitud faktina. Tehniliste väidete aluseks on hoidla
README, CLI parser, hoidla projekti näide ja sillarakenduse Päritolu vaade.
[skills.sh](https://skills.sh/) on kataloogi näide, mitte kvaliteedigarantii.

## Muutmine

- `content.js`: eestikeelsed slaidid, ajad ja esinejamärkmed.
- `content-en.js`: samade slaidide ja märkmete ingliskeelsed tõlked.
- `i18n.js`: keelevalik ning vaaturi tekstid.
  Sisu muutmisel uuenda mõlemat keeleversiooni; slaidide järjekord ja ajad peavad kattuma.
- `app.js`: vaatur ja interaktiivse näite arvutus.
- `styles.css`: kujundus; siht on arvuti esitlusvaade.
- `viewer.html`: HTML-i karkass.
- `assets/`: originaalsed dashboardi ekraanipildid.

Pärast muutmist loo uuesti iseseisev fail:

```bash
python3 presentations/openmapstack-45min/build.py
```

Koostaja kasutab ainult Pythoni standardteeki. Vaatamiseks pole Node’i,
Pythoni, API võtme ega muu runtime’i vajadust.
