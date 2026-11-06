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
