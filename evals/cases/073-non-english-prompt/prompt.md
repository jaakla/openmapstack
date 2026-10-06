# Ligipääsetavuse sõel kandidaat-kinnistutele

Kasuta kaasasolevaid kinnistute, teede ja POI kihte kataloogis `data/source/`,
et leida kinnistud, mis võiksid sobida kavandatavaks kasutuseks. Kaasa
kinnistud, mis on vähemalt 8 000 ruutmeetrit, mille sihtotstarve on `ARIMAA`,
`MAATULUNDUSMAA` või `TOOTMISMAA`, ja mis asuvad kuni 2 000 meetri kaugusel
põhimaanteest. Deklareeri tulemus `project.yaml` jaotises `outputs.candidate_parcels`; vali sobiv ruumiandmete vorming ja failinimi.

Tarni selles kataloogis täielik openmapstack-project/v1 projekt koos
kanoonilise käivitatava töövoo ja valideerimistõenditega. Paigaldatud on
Python 3, DuckDB Spatial ja pakett openmapstack; käsurea jaoks kasuta käsku
`openmapstack` või `python3 -m openmapstack`. Ära eelda muid geoandmete
Pythoni pakette ega paigalda pakette.
