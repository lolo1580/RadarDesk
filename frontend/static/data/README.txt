Airport data
============

Source : OurAirports (https://ourairports.com/data/)
Fichier source : airports.csv
Licence : domaine public (CC0)

Extraction utilisée par RadarDesk :
- aéroports de type large_airport ou medium_airport ;
- avec un code IATA ou un service régulier (scheduled_service=yes) ;
- dédupliqués par code OACI ;
- format compact [icao, iata, nom, type, latitude, longitude].

Le fichier airports.json est embarqué dans le conteneur frontend et filtré
côté navigateur selon le centre et la portée d'affichage du radar.


Runway data
===========

Source : OurAirports (https://ourairports.com/data/)
Fichier source : runways.csv
Licence : domaine public (CC0)

Extraction utilisée par RadarDesk :
- pistes non fermées des aéroports large_airport / medium_airport ;
- coordonnées des deux seuils, longueur, largeur et désignations ;
- format { "ICAO": [[lat1, lon1, lat2, lon2, length_ft, width_ft, ident1, ident2], ...] }.

Le fichier runways.json est filtré côté navigateur et dessiné uniquement pour
les aéroports présents dans la portée d'affichage.
