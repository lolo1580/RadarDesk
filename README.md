# RadarDesk — Radar ADS-B Docker

**RadarDesk** est un scope radar militaire / CRT en HTML5 Canvas, avec un
backend FastAPI, un frontend mobile statique servi par Nginx, une interface
desktop standard et un service d'administration séparé, le tout orchestré par
Docker Compose.

Dépôt : [github.com/lolo1580/RadarDesk](https://github.com/lolo1580/RadarDesk)

- Fond noir pur `#000000` adapté aux dalles AMOLED.
- Balayage phosphore vert `#00ff66`, rotation complète en **4 s**.
- Rémanence angulaire : chaque cible s'illumine au passage du faisceau puis
  s'atténue progressivement.
- Traînées d'échos historiques et vecteur vitesse sur 30 s.
- Grain phosphore, réflexion de verre et balayage CRT très lent.
- Anneaux de distance, couronne de boussole graduée, réticule central.
- Cibles orientées selon leur cap, étiquettes callsign / altitude / vitesse.
- Aéroports majeurs et moyens en cyan, avec codes IATA/OACI, filtrés selon la portée.
- Tracé des pistes réelles (seuils, orientation, largeur, désignations) sous
  forme de bandes, visible dès 5 NM.
- Météo aviation : badges METAR VFR/MVFR/IFR/LIFR et fiche détaillée au clic.
- Couche de précipitations animée RainViewer, filtrée selon la portée.
- Boutons `−` / `+` pour ajuster la distance d'affichage sans nouvel appel API.
  En portrait, le réglage est intégré à l'en-tête des contacts prioritaires.
- Gestion visuelle des squawks `7500`, `7600`, `7700` et indices militaires.
- Console : horloge UTC, compteur de cibles, 5 contacts prioritaires.
- Panneau « aéroport le plus proche » basé sur les coordonnées GPS configurées,
  avec bascule vers les contacts prioritaires.
- Administration web séparée : état du flux et configuration runtime à chaud.
- Réception locale possible depuis un récepteur readsb / dump1090-fa sur une
  autre VM (`aircraft.json` HTTP).
- Modes paysage 18:9 et portrait compact, sans défilement.
- Trois styles de radar : phosphore vert, monochrome et ATC moderne.
- Interface desktop sur `8083` : carte **tar1090** complète (filtres,
  recherche, table, calques, traces de session) et vue statistiques
  RadarDesk personnalisable.
- Statistiques de réception persistantes en SQLite, échantillonnées en continu
  par le backend, avec export InfluxDB v2 configurable dans l'administration.
- API compacte `/api/widgets/homepage` pour intégrer les compteurs dans
  Homepage ou un autre dashboard.
- Cache serveur de 4 s, polling frontend de 3 s.
- Le frontend mobile n'utilise aucune ressource JavaScript/CSS externe :
  Leaflet est embarqué localement, compatible Morph Browser / QtWebEngine.

> Outil de visualisation uniquement. Ne pas utiliser pour la navigation aérienne
> ou une décision opérationnelle.

## Architecture RadarDesk

```text
Navigateur mobile / Morph Browser          Navigateur desktop
          │ HTTP :8000                              │ HTTP :8083
          ▼                                         ▼
┌───────────────────────────┐          ┌──────────────────────────────┐
│ frontend                  │          │ desktop                      │
│ Nginx :80                 │          │ Nginx :80                    │
│  /        → radar mobile  │          │  /          → app RadarDesk  │
│  /api/*   → backend       │          │  /tar1090/  → tar1090        │
└─────────────┬─────────────┘          └──────────────┬───────────────┘
              │                                       │
              ▼                                       ▼
┌───────────────────────────┐          ┌──────────────────────────────┐
│ backend                   │          │ tar1090                      │
│ FastAPI/Uvicorn :8000     │          │ Nginx/UI :8080 → hôte 8084   │
│ cache 4 s + normalisation │          │ lit aircraft.json            │
└─────────────┬─────────────┘          └──────────────────────────────┘
              │ HTTPS
      ┌───────┴────────┐
      ▼                ▼
Airplanes.live     adsb.lol
(source primaire)  (secours)
```

Le frontend mobile (`8081`), l'administration (`8082`), l'interface desktop
(`8083`) et tar1090 (`8084`) sont exposés sur la machine hôte. Le backend radar
n'est pas accessible directement depuis l'extérieur du réseau Docker.

La vue `CARTE` du desktop est une iframe tar1090 servie sous la même origine,
via le proxy `/tar1090/` de Nginx. L'interface tar1090 est donc disponible à la
fois dans le desktop et directement sur `8084`. La vue `STATISTIQUES` reste
l'application RadarDesk servie par le Nginx desktop.

Le backend écrit en continu `aircraft.json` et `receiver.json` au format
dump1090/readsb dans le volume Docker `tar1090_data`. Le conteneur tar1090 les
lit et les expose sous `/data/`. Le flux tar1090 est **temps réel uniquement**
(`history: 0`) : l'historique longue durée reste géré par SQLite et la vue
statistiques RadarDesk.

Le backend écrit également un historique de réception dans une base SQLite
locale montée sur le volume Docker `radardesk_stats_data`. Le collecteur
échantillonne l'antenne en continu, même sans navigateur ouvert, et peut
exporter les mêmes métriques vers InfluxDB v2. Le token InfluxDB reste dans le
volume d'administration et n'est jamais renvoyé au frontend.

## Arborescence

```text
RadarDesk/
├── docker-compose.yml
├── .env.example
├── .gitignore
├── README.md
├── backend/
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── requirements.txt
│   └── server.py
├── admin/
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── requirements.txt
│   ├── server.py
│   └── static/
│       ├── index.html
│       ├── style.css
│       ├── admin.js
│       └── data/
│           └── airports.json
├── frontend/
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── nginx.conf
│   ├── index.html
│   └── static/
│       ├── style.css
│       ├── radar.js
│       ├── icon.svg
│       ├── manifest.webmanifest
│       └── data/
│           ├── airports.json
│           ├── runways.json
│           └── README.txt
├── desktop/
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── nginx.conf
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   ├── favicon.svg
│   └── vendor/
│       └── leaflet/
│           ├── leaflet.js
│           ├── leaflet.css
│           ├── LICENSE
│           └── images/
└── tar1090/
    ├── config.js
    └── db2/
        ├── ranges.js
        └── 0.js … F.js
```

## Démarrage rapide

Prérequis : Docker Engine et le plugin Docker Compose.

```bash
git clone https://github.com/lolo1580/RadarDesk.git
cd RadarDesk

# Administration : identifiants par défaut admin / admin.
cp .env.example .env
# Remplacer impérativement ADSB_ADMIN_PASSWORD avant exposition réseau.

# Construire et démarrer tous les services
docker compose up -d --build

# Vérifier l'état
docker compose ps
```

Ouvrir le radar :

```text
http://localhost:8000
```

Ouvrir l'administration :

```text
http://localhost:8082
```

Ouvrir l'interface desktop :

```text
http://localhost:8083
```

La vue `CARTE` y affiche tar1090 ; `STATS` affiche la page de statistiques
RadarDesk. tar1090 est aussi accessible directement :

```text
http://localhost:8084
```

Identifiant et mot de passe par défaut : `admin` / `admin`.
Les valeurs personnalisées se trouvent dans `ADSB_ADMIN_USER` et
`ADSB_ADMIN_PASSWORD` du fichier `.env`.

> **Sécurité** : les interfaces radar, desktop et tar1090 (`8084`) n'ont pas
> d'authentification. Les exposer uniquement sur un réseau de confiance,
> derrière un reverse proxy HTTPS et/ou un filtrage pare-feu.
> L'administration `8082` est protégée par
> HTTP Basic Auth : changer impérativement `admin/admin`. Le token InfluxDB
> reste dans le volume d'administration et n'est jamais renvoyé au frontend.
> L'API Homepage est publique mais ne contient aucun secret. Le réglage
> `ADSB_CORS_ORIGINS=*` est prévu pour un usage local ; le restreindre en cas
> d'exposition réseau.

Vérifier le flux :

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/aircraft
```

Journaux :

```bash
docker compose logs -f
docker compose logs -f backend
docker compose logs -f frontend
```

Arrêt :

```bash
docker compose down
```

Mise à jour après modification du code :

```bash
docker compose up -d --build
```

### Changer le port exposé

Si le port `8000` est déjà utilisé :

```bash
ADSB_HTTP_PORT=8080 docker compose up -d
```

ou dans `.env` :

```dotenv
ADSB_HTTP_PORT=8080
```

## Configuration

Docker Compose lit automatiquement le fichier `.env` à la racine. Les valeurs
par défaut sont déjà présentes dans `docker-compose.yml`.

| Variable | Défaut | Rôle |
|---|---:|---|
| `ADSB_HTTP_PORT` | `8000` | Port hôte exposé par Nginx |
| `ADSB_LAT` | `46.8687` | Latitude du centre du scope |
| `ADSB_LON` | `6.9416` | Longitude du centre du scope |
| `ADSB_RADIUS_NM` | `50` | Rayon d'affichage par défaut en NM |
| `ADSB_MAX_RADIUS_NM` | `150` | Portée maximale récupérée en amont |
| `ADSB_REFRESH_MS` | `3000` | Intervalle de polling frontend |
| `ADSB_CACHE_TTL` | `4.0` | Durée du cache serveur (s) |
| `ADSB_STALE_TTL` | `120` | Durée maximale du cache de secours (s) |
| `ADSB_FAILURE_BACKOFF` | `5.0` | Pause après échec upstream (s) |
| `ADSB_TIMEOUT` | `4.0` | Timeout d'un appel upstream (s) |
| `ADSB_PRIMARY_PROVIDER` | `airplanes_live` | Source primaire |
| `ADSB_FALLBACK_PROVIDER` | `adsb_lol` | Secours, ou `none` |
| `ADSB_PRIMARY_COOLDOWN` | `600` | Pause après un 403 primaire (s) |
| `ADSB_RECEPTION_MODE` | `online` | `online` ou `local` (aircraft.json) |
| `ADSB_LOCAL_JSON_URL` | — | URL aircraft.json du récepteur local |
| `ADSB_LOG_LEVEL` | `INFO` | Niveau de journalisation |
| `ADSB_CORS_ORIGINS` | `*` | Origines CORS autorisées |
| `ADSB_ADMIN_PORT` | `8082` | Port hôte de l'interface d'administration |
| `ADSB_DESKTOP_PORT` | `8083` | Port hôte du desktop (stats + proxy tar1090) |
| `ADSB_TAR1090_PORT` | `8084` | Port hôte de tar1090 (accès direct) |
| `ADSB_TAR1090_WRITE_INTERVAL_S` | `10` | Intervalle d'écriture des données tar1090 (s) |
| `ADSB_ADMIN_USER` | `admin` | Utilisateur Basic Auth admin |
| `ADSB_ADMIN_PASSWORD` | `admin` | Mot de passe admin (à changer) |
| `ADSB_RADAR_STYLE` | `phosphor` | Style : `phosphor`, `mono` ou `atc` |
| `ADSB_LOCATION_MODE` | `coords` | Localisation : `coords` ou `airport` |
| `ADSB_AIRPORT_ICAO` | — | Code OACI si `ADSB_LOCATION_MODE=airport` |
| `ADSB_METAR_PROVIDER` | `aviationweather` | Source METAR : `aviationweather`, `metar_taf` ou `checkwx` |
| `METAR_TAF_API_KEY` | — | Clé API metar-taf.com |
| `CHECKWX_API_KEY` | — | Clé API CheckWX |
| `ADSB_STATS_ENABLED` | `true` | Collecte SQLite des statistiques |
| `ADSB_STATS_DB_PATH` | `/stats/adsb-stats.db` | Base SQLite dans le volume `stats_data` |
| `ADSB_STATS_SAMPLE_SECONDS` | `30` | Intervalle d'échantillonnage du collecteur (s) |
| `ADSB_STATS_RETENTION_DAYS` | `180` | Conservation de l'historique (jours) |
| `ADSB_STATS_PASS_GAP_SECONDS` | `1800` | Délai (s) au-delà duquel un retour compte comme un nouveau passage |
| `ADSB_INFLUX_ENABLED` | `false` | Active l'export InfluxDB v2 |
| `ADSB_INFLUX_URL` | — | URL de base InfluxDB, ex. `http://influxdb:8086` |
| `ADSB_INFLUX_ORG` | — | Organisation InfluxDB |
| `ADSB_INFLUX_BUCKET` | — | Bucket InfluxDB |
| `ADSB_INFLUX_TOKEN` | — | Token API InfluxDB |
| `ADSB_INFLUX_MEASUREMENT` | `adsb_stats` | Nom de la mesure InfluxDB |
| `ADSB_INFLUX_INTERVAL_S` | `30` | Intervalle minimal entre deux exports (s) |
| `ADSB_INTERNAL_TOKEN` | — | Jeton interne backend ↔ admin, obligatoire |

Appliquer une modification :

```bash
docker compose up -d --force-recreate backend
```

## À propos d'Airplanes.live

`api.airplanes.live` est la source primaire. Depuis 2026, elle répond toutefois
`HTTP 403` aux adresses non déclarées comme feeders :

```json
{"error": "Please contact us at contact@airplanes.live..."}
```

Un secours vers `api.adsb.lol` (même schéma `/v2/point`) est donc activé par
défaut pour que l'application reste fonctionnelle. Le backend tente toujours
Airplanes.live en premier, met la source primaire en pause 10 minutes après un
`403`, puis affiche la source réellement utilisée. Le frontend l'indique avec le
badge `SECOURS` et la ligne source en bas de console.

Pour imposer Airplanes.live seul :

```dotenv
ADSB_FALLBACK_PROVIDER=none
```

Puis :

```bash
docker compose up -d --force-recreate backend
```

## API interne

### `GET /api/aircraft`

Retourne un tableau JSON de cibles normalisées, filtré selon la distance
d'affichage demandée :

```text
GET /api/aircraft?radius_nm=50
```

Sans paramètre, le rayon par défaut `ADSB_RADIUS_NM` est utilisé. La portée
maximale est `ADSB_MAX_RADIUS_NM` (150 NM par défaut, 250 NM maximum). Le
frontend récupère la portée maximale une seule fois par cycle de cache, puis
filtre localement les boutons `−` / `+` pour un changement instantané.

```json
[
  {
    "hex": "344297",
    "callsign": "AEA1672",
    "registration": "EC-MOO",
    "lat": 46.788397,
    "lon": 6.962036,
    "alt_baro": 28700,
    "gs": 467.4,
    "track": 237.2,
    "squawk": "1000",
    "type": "B738",
    "on_ground": false
  }
]
```

Métadonnées dans les en-têtes de réponse :

- `X-Data-Stale`
- `X-Upstream`
- `X-Fallback`
- `X-Cache-Age`
- `X-Last-Success`
- `X-Range-NM`
- `X-Max-Range-NM`
- `X-Default-Range-NM`
- `X-Center-Lat`
- `X-Center-Lon`

En cas d'échec upstream avec un cache valide de moins de 2 minutes, l'API
renvoie `200` avec `X-Data-Stale: 1` pour éviter de vider le scope.

### `GET /api/config`

Configuration publique consommée par le frontend : centre, rayon, intervalle de
rafraîchissement et sources.

### `GET /api/health`

État du cache sans appel upstream. Utilisé aussi par le healthcheck Docker du
backend.

### `GET /api/weather/metar?ids=LSGG,LSZH`

Proxy METAR avec cache par station. Retourne les observations normalisées des
aéroports demandés.

### `GET /api/weather/radar`

Métadonnées des dernières frames RainViewer (host, frames, horodatage), avec
cache court côté backend.

### `GET /api/stats/summary?range_nm=50&days=7&minutes=60`

Résumé des statistiques SQLite pour le jour UTC courant et le centre d'antenne
actif. `range_nm` sélectionne la portée du comptage ; `days` contrôle
l'historique journalier (1–90 jours), `minutes` la fenêtre du graphique et
`registration_days` la période de cumul des immatriculations (1–180 jours par
défaut, selon la rétention).

La réponse contient notamment :

- `samples`, `observations`, `peak`, `average`, `unique` ;
- `registrations` : immatriculations distinctes connues aujourd'hui ;
- `registrations_period`, `registration_passes` et `registration_days` :
  total sur la période de cumul demandée ;
- `top_registrations` : immatriculations les plus fréquentes sur la période,
  avec le nombre de passages et de jours actifs ;
- `recent` : agrégats par minute sur la fenêtre demandée ;
- `hours` : 24 agrégats horaires ;
- `daily` : uniques et pics des derniers jours ;
- `last` : dernier échantillon connu.

Les comptages sont séparés par centre d'antenne et par portée. Un aéronef
compte comme unique pour une portée s'il a été vu au moins une fois dans cette
portée pendant la journée. Un **passage** est compté lorsqu'un aéronef
réapparaît après au moins `ADSB_STATS_PASS_GAP_SECONDS` sans réception
(30 minutes par défaut) ; les échantillons successifs d'une même visite ne
gonflent donc pas le compteur.

### `GET /api/widgets/homepage?range_nm=50`

Endpoint public compact, prévu pour un widget **Homepage** (gethomepage.dev) ou
tout autre dashboard. Il ne déclenche pas d'appel upstream supplémentaire : il
utilise le cache radar et les statistiques SQLite déjà présentes.

L'alias `GET /api/public/summary` renvoie le même JSON. Aucun token InfluxDB
ni secret n'est exposé.

Voir la section **Intégration Homepage** pour la configuration complète, les
mappings et le dépannage.

### `POST /internal/stats/reset`

Endpoint interne, protégé par `X-Internal-Token`. Efface tout l'historique
SQLite. Appelé par le bouton `EFFACER L'HISTORIQUE` de l'administration.

### `POST /internal/influx/test`

Endpoint interne, protégé par `X-Internal-Token`. Envoie un point de test vers
InfluxDB avec la configuration active.

L'export InfluxDB réutilise les échantillons du collecteur SQLite : laisser
`ADSB_STATS_ENABLED=true` pour que l'export fonctionne. Chaque point contient
les comptages de la portée par défaut configurée, avec les tags `source`,
`location` et `range_nm`.

### `POST /internal/reload`

Endpoint interne non exposé par Nginx. Il recharge le fichier
`/data/runtime.json` dans le backend. Il exige l'en-tête `X-Internal-Token`
correspondant à `ADSB_INTERNAL_TOKEN`. Le service d'administration l'appelle
après chaque modification.

## Interface desktop, tar1090 et statistiques

Le service `desktop` est une interface dédiée sur `8083`, avec un sélecteur
`CARTE / STATS`.

### Vue CARTE : tar1090

La vue `CARTE` affiche l'interface [tar1090](https://github.com/wiedehopf/tar1090)
dans une iframe servie sous la même origine (`/tar1090/`). Elle apporte :

- carte OpenFreeMap Dark, anneaux de portée et marqueurs orientés ;
- table des aéronefs avec indicatif, immatriculation, type, squawk, altitude,
  vitesse, distance et RSSI ;
- recherche, filtres, colonnes configurables, tri et sélection ;
- calques et réglages tar1090, étiquettes et mode plein écran ;
- accès direct à tar1090 sur `8084`, ou via `8083/tar1090/`.

Le backend alimente tar1090 en écrivant `aircraft.json` et `receiver.json` au
format dump1090/readsb dans le volume Docker `tar1090_data`. Le conteneur
tar1090 les sert sous `/data/`. Le flux est temps réel uniquement
(`history: 0`) ; l'historique longue durée reste dans SQLite pour la vue
`STATS`.

La configuration de la carte se trouve dans `tar1090/config.js`. Le dossier
`tar1090/db2/` fournit des réponses minimales pour la base de métadonnées
tar1090 ; les informations principales (immatriculation, type, indicatif)
viennent directement de RadarDesk. tar1090 est un projet GPL-2.0-or-later
distribué via l'image `blackoutsecure/tar1090`.

### Vue STATS

La vue `STATISTIQUES` est l'interface RadarDesk :

- comptage temps réel : avions dans la zone, en vol / au sol, urgences ;
- compteurs du jour : avions uniques, immatriculations, pic simultané,
  contacts cumulés, nombre de relevés et moyenne par relevé ;
- graphique d'activité sur les 60 dernières minutes et activité horaire ;
- historique des 7 derniers jours en avions uniques ;
- répartition des contacts par distance, par altitude et par type d'appareil ;
- panneau antenne : centre, portée courante, portée max, surface couverte et
  source de données ;
- top des immatriculations avec cumul jour / semaine / mois / 180 jours ;
- statistiques persistées côté serveur en SQLite, séparées par centre d'antenne
  et par portée ;
- bouton **PERSONNALISER** pour afficher ou masquer chaque module ; la grille se
  réorganise automatiquement et les préférences restent dans le navigateur
  (`localStorage`).

La portée choisie dans la barre supérieure s'applique à la vue `STATS` et aux
compteurs RadarDesk. tar1090 possède ses propres filtres de portée.

Le collecteur backend échantillonne l'antenne même lorsque le navigateur est
fermé, et conserve l'historique dans la base SQLite du volume Docker
`radardesk_stats_data`. Le panneau des immatriculations affiche les 200
immatriculations les plus fréquentes sur la période sélectionnée, avec le
nombre de passages ; la liste est défilable. Sur les grands écrans, la vue
statistiques occupe toute la hauteur disponible ; sur les résolutions plus
basses, elle défile verticalement sans compresser les panneaux.

Les ports sont configurables avec `ADSB_DESKTOP_PORT` (8083) et
`ADSB_TAR1090_PORT` (8084).

## Administration

Le service `admin` est séparé du backend radar :

- interface web moderne sombre et responsive sur `http://<hôte>:8082`,
  organisée en trois tâches distinctes accessibles depuis le menu latéral :
  **Vue d'ensemble**, **Récepteur** et **Statistiques & InfluxDB** ;
- HTTP Basic Auth, avec identifiants par défaut `admin` / `admin` ;
- **Vue d'ensemble** : état du radar (liaison, source, cache, cibles,
  configuration active, base SQLite, InfluxDB), raccourcis vers les tâches et
  actions globales **Recharger** / **Réinitialiser** ;
- **Récepteur** : formulaire dédié à la position (coordonnées GPS ou code
  OACI), aux portées, au cache, à la cadence, aux sources ADS-B et à la source
  METAR, avec un bouton **Appliquer** qui ne modifie pas l'export InfluxDB.
  Le mode de réception peut être **en ligne** (Airplanes.live / adsb.lol) ou
  **local** (URL `aircraft.json` readsb / dump1090), avec un bouton
  **Tester la source**.
  En mode OACI, le HUD radar affiche le code de l'aéroport (`· LSGG`) et
  l'aéroport le plus proche passe à `0.0 NM` ;
- **Statistiques & InfluxDB** : formulaire d'export séparé (URL, organisation,
  bucket, token, mesure et intervalle), boutons **Appliquer l'export** et
  **Tester InfluxDB**, plus une carte de maintenance **Effacer l'historique**.
  Appliquer l'export ne modifie pas la configuration du récepteur ;
- les valeurs non appliquées d'un formulaire sont conservées lorsqu'on
  enregistre l'autre tâche ; les actions globales restent explicites dans la
  vue d'ensemble ;
- le panneau du radar affiche par défaut l'aéroport le plus proche des
  coordonnées configurées, avec bascule CONTACTS / AÉROPORT ;
- persistance dans le volume Docker `radardesk_admin_data` ;
- l'export InfluxDB v2 utilise la portée par défaut configurée pour l'antenne ;
- le bouton **EFFACER L'HISTORIQUE** vide uniquement la base SQLite des
  statistiques, sans toucher aux données déjà écrites dans InfluxDB.

Les modifications sont appliquées sans redémarrage du backend. En cas de
redémarrage du backend, la configuration runtime est relue depuis le volume.

> Les identifiants par défaut `admin` / `admin` sont destinés à un usage local.
> Les changer impérativement avant toute exposition réseau, et ne pas exposer
> le service admin sur Internet sans reverse proxy HTTPS.

Le mot de passe admin et le jeton interne sont dans `.env`. Pour générer des
valeurs fortes :

```bash
python3 - <<'PY'
import secrets
print("ADSB_ADMIN_PASSWORD=" + secrets.token_urlsafe(24))
print("ADSB_INTERNAL_TOKEN=" + secrets.token_urlsafe(32))
PY
```

Puis :

```bash
docker compose up -d --force-recreate backend admin
```

## Réception locale (readsb / dump1090)

RadarDesk peut remplacer les fournisseurs en ligne par une réception ADS-B
locale. Le backend lit directement un fichier `aircraft.json` au format
dump1090/readsb exposé par une autre machine du réseau.

### Depuis l'administration

1. Ouvrir **Récepteur**.
2. Choisir **Mode de réception → Réception locale (JSON HTTP)**.
3. Renseigner l'URL, par exemple :
   ```text
   http://192.168.30.25:8080/data/aircraft.json
   ```
4. Vérifier que la position de l'antenne (latitude / longitude) correspond au
   récepteur local.
5. **Appliquer**, puis **Tester la source**.

Le bouton de test utilise la configuration active : appliquer avant de tester.
L'URL doit être joignable depuis le conteneur `backend`, donc utiliser l'adresse
IP LAN du récepteur, pas `localhost`.

### Sources compatibles

- `dump1090-fa` avec son interface web : généralement
  `http://<ip>:8080/data/aircraft.json` ;
- `readsb` + tar1090 : `http://<ip>:8080/data/aircraft.json` ;
- toute API renvoyant un objet `{"aircraft": [...]}` ou `{"ac": [...]}`, ou
  directement un tableau d'aéronefs au format dump1090.

En mode local, les sources en ligne et le cooldown primaire sont ignorés.
Repasser en mode **En ligne** pour réutiliser Airplanes.live / adsb.lol.

### Configuration par environnement

```dotenv
ADSB_RECEPTION_MODE=local
ADSB_LOCAL_JSON_URL=http://192.168.30.25:8080/data/aircraft.json
```

Puis :

```bash
docker compose up -d --force-recreate backend
```

L'administration reste la méthode recommandée : elle applique la configuration
à chaud et permet de tester la source. Si une configuration runtime existe déjà
dans le volume `radardesk_admin_data`, elle reste prioritaire sur `.env` :
utiliser l'administration ou le bouton **Réinitialiser**.

## Intégration Homepage (gethomepage.dev)

RadarDesk expose une API JSON compacte utilisable directement dans le widget
**Custom API** de [Homepage](https://gethomepage.dev/), souvent appelé
« homepage.io ».

### Endpoint

```text
GET /api/widgets/homepage?range_nm=50
```

Alias :

```text
GET /api/public/summary
```

Utiliser de préférence le service desktop :

```text
http://<hote>:8083/api/widgets/homepage?range_nm=50
```

Il est aussi accessible via le frontend mobile :

```text
http://<hote>:8081/api/widgets/homepage?range_nm=50
```

L'endpoint est public, sans authentification, et ne nécessite aucun token.
Ne pas utiliser l'admin sur `8082` : il est protégé par Basic Auth et ne sert
pas les widgets.

### Fonctionnement

- `range_nm` est optionnel ; sans valeur, la portée par défaut configurée est
  utilisée.
- Les compteurs sont calculés pour la portée demandée (les portées standard
  sont privilégiées : 5, 10, 25, 50, 75, 100, 125, 150…).
- Aucun appel ADS-B supplémentaire n'est déclenché.
- Les valeurs SQLite peuvent avoir jusqu'à `stats_sample_seconds` de retard
  (30 s par défaut).
- `online` vaut `false` si le cache radar est absent ou trop ancien ;
  `status` vaut alors `starting` ou `degraded`.

### Exemple de réponse

```json
{
  "version": 1,
  "status": "ok",
  "online": true,
  "source": "adsb.lol",
  "fallback_used": true,
  "cache_age_s": 1.2,
  "last_update": "2026-11-07T21:12:49Z",
  "center_lat": 46.8432,
  "center_lon": 6.91506,
  "range_nm": 50,
  "aircraft": 18,
  "airborne": 17,
  "ground": 1,
  "emergency": 0,
  "stats_enabled": true,
  "unique_today": 63,
  "unique_registrations_today": 59,
  "peak_today": 26,
  "contacts_today": 1102,
  "samples_today": 50,
  "average_today": 22.04,
  "last_sample_at": "2026-11-07T21:12:49Z",
  "influx_enabled": false
}
```

### Champs disponibles

| Champ | Type | Description |
| --- | --- | --- |
| `status` | texte | `ok`, `degraded` ou `starting` |
| `online` | booléen | Cache radar considéré comme valide |
| `source` | texte | Source ADS-B réellement utilisée |
| `fallback_used` | booléen | Source de secours utilisée |
| `cache_age_s` | nombre | Âge du cache radar en secondes |
| `last_update` | texte ISO | Dernière récupération ADS-B |
| `center_lat`, `center_lon` | nombres | Centre de l'antenne |
| `range_nm` | nombre | Portée effective du comptage |
| `aircraft` | nombre | Avions actuellement dans la zone |
| `airborne` | nombre | Avions en vol |
| `ground` | nombre | Avions au sol |
| `emergency` | nombre | Squawks 7500/7600/7700 |
| `unique_today` | nombre | Adresses ICAO uniques vues aujourd'hui |
| `unique_registrations_today` | nombre | Immatriculations distinctes connues aujourd'hui |
| `peak_today` | nombre | Pic simultané du jour |
| `contacts_today` | nombre | Contacts cumulés |
| `samples_today` | nombre | Nombre de relevés SQLite |
| `average_today` | nombre | Moyenne de contacts par relevé |
| `last_sample_at` | texte ISO | Dernier échantillon SQLite |
| `stats_enabled` | booléen | Collecte SQLite active |
| `influx_enabled` | booléen | Export InfluxDB activé |
| `generated_at` | texte ISO | Génération de la réponse |

### Configuration Homepage

Exemple de `services.yaml` :

```yaml
- Surveillance aérienne:
    - RadarDesk:
        icon: mdi-radar
        href: http://<hote>:8083/
        description: Récepteur ADS-B local
        widget:
          type: customapi
          url: http://<hote>:8083/api/widgets/homepage?range_nm=50
          refreshInterval: 10000 # ms
          mappings:
            - field: aircraft
              label: Avions
              format: number
            - field: airborne
              label: En vol
              format: number
            - field: ground
              label: Au sol
              format: number
            - field: unique_today
              label: Uniques jour
              format: number
            - field: unique_registrations_today
              label: Immat. jour
              format: number
            - field: peak_today
              label: Pic
              format: number
            - field: status
              label: État
              format: text
              remap:
                - value: ok
                  to: En ligne
                - value: degraded
                  to: Différé
                - any: true
                  to: Démarrage
            - field: source
              label: Source
              format: text
            - field: cache_age_s
              label: Cache
              format: number
              suffix: " s"
            - field: last_update
              label: MAJ
              format: relativeDate
              locale: fr
              style: short
              numeric: auto
```

Variante compacte en liste :

```yaml
- RadarDesk:
    icon: mdi-radar
    href: http://<hote>:8083/
    widget:
      type: customapi
      url: http://<hote>:8083/api/widgets/homepage?range_nm=50
      refreshInterval: 10000
      display: list
      mappings:
        - field: aircraft
          label: Avions
          format: number
          additionalField:
            field: source
            format: text
        - field: unique_today
          label: Uniques jour
          format: number
        - field: unique_registrations_today
          label: Immat. jour
          format: number
        - field: peak_today
          label: Pic
          format: number
        - field: status
          label: État
          format: text
```

Les formats `number`, `text`, `relativeDate`, `remap`, `suffix` et
`additionalField` sont ceux du widget Custom API de Homepage.

### Réseau Docker

Le conteneur Homepage ne doit pas utiliser `localhost` : `localhost` désigne le
conteneur Homepage lui-même. Utiliser :

- l'adresse IP LAN de la machine Docker : `http://192.168.1.42:8083/...` ;
- ou, si Homepage est sur le même réseau Docker que RadarDesk,
  `http://desktop/api/widgets/homepage?range_nm=50` ;
- sur Docker Desktop, `http://host.docker.internal:8083/...` fonctionne
  généralement ;
- sur Docker Linux, pour utiliser `host.docker.internal`, ajouter au service
  Homepage :

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

### Vérification et dépannage

Depuis la machine hôte :

```bash
curl -s http://127.0.0.1:8083/api/widgets/homepage | python3 -m json.tool
```

Ou depuis le conteneur Homepage, si `curl` y est disponible :

```bash
docker exec -it homepage \
  curl -fsS http://host.docker.internal:8083/api/widgets/homepage
```

Points de contrôle :

- `status: ok` et `online: true` : flux radar valide ;
- `status: degraded` : cache radar ancien, le widget continue d'afficher la
  dernière valeur ;
- `unique_today` ou `peak_today` à `0` : vérifier que la collecte SQLite est
  active dans l'admin et qu'un échantillon a déjà été enregistré ;
- `range_nm` : utiliser une portée standard pour éviter toute conversion vers
  la portée statistique supérieure ;
- si les champs ne s'affichent pas dans Homepage, vérifier que le `field`
  correspond exactement au nom JSON et que le widget est de type `customapi`.

## Chargement dans Morph Browser

1. Relever l'adresse IP LAN de la machine Docker :

   ```bash
   hostname -I
   # exemple : 192.168.1.42
   ```

2. Vérifier l'accès depuis le téléphone :

   ```bash
   curl http://192.168.1.42:8000/api/health
   ```

3. Dans Morph Browser, ouvrir :

   ```text
   http://192.168.1.42:8000
   ```

4. Appuyer sur le bouton plein écran en haut à droite de la console. Le layout
   s'adapte automatiquement au paysage 18:9 ou au portrait.

5. Pour masquer complètement la barre d'adresse, créer une **Web App** depuis
   Morph Browser (menu → ajout à l'écran d'accueil / application web). Le
   manifeste fourni demande déjà `display: fullscreen` et accepte les deux
   orientations.

6. Désactiver la mise en veille de l'écran dans les réglages Ubuntu Touch.
   L'application tente également d'activer l'API Wake Lock au premier appui.

## Développement sans Docker

Le backend peut servir directement le frontend pour itérer rapidement :

```bash
cd backend

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

ADSB_STATIC_DIR=../frontend python server.py
```

Puis ouvrir `http://localhost:8000`. Dans ce mode, l'API et les fichiers
statiques sont servis par FastAPI ; en Docker, Nginx assure ce rôle.

## Dépannage

- **Le téléphone ne charge pas la page** : PC et téléphone doivent être sur le
  même réseau. Autoriser le port hôte, par exemple `sudo ufw allow 8000/tcp`.
- **`/api/health` renvoie `starting`** : aucune donnée n'a encore été reçue.
  Vérifier l'accès sortant HTTPS vers Airplanes.live ou adsb.lol.
- **Le scope affiche `HORS LIGNE`** : les dernières cibles connues restent
  affichées ; le polling reprend automatiquement.
- **Port 8000 déjà utilisé** : `ADSB_HTTP_PORT=8080 docker compose up -d`.
- **Portée maximale trop faible** : augmenter `ADSB_MAX_RADIUS_NM` (max 250)
  puis recréer le backend.
- **Le conteneur backend ne démarre pas** : `docker compose logs backend`.
- **Écran noir, page chargée** : vérifier `/static/radar.js` et la console du
  navigateur.
- **Changement de coordonnées non pris en compte** : appliquer la configuration
  depuis `/admin` puis cliquer sur **Recharger**, ou recréer le backend.
- **Administration en 401** : vérifier `ADSB_ADMIN_USER` /
  `ADSB_ADMIN_PASSWORD` dans `.env`, puis recréer le service `admin`.
- **Port 8082 occupé** : modifier `ADSB_ADMIN_PORT` dans `.env`, puis
  `docker compose up -d --force-recreate admin`.
- **tar1090 vide ou sans avions** : vérifier `docker compose logs tar1090` et
  la présence de `aircraft.json` dans le volume `tar1090_data` :
  `docker compose exec backend ls -l /tar1090_data`. Vérifier aussi
  `http://localhost:8084/` et `http://localhost:8083/tar1090/`.
- **Fond de carte tar1090 absent** : OpenFreeMap nécessite un accès Internet
  depuis le navigateur. Les données ADS-B restent servies localement.
- **Port 8084 occupé** : modifier `ADSB_TAR1090_PORT` dans `.env`, puis
  `docker compose up -d --force-recreate tar1090 desktop`.
- **Erreur 502 sur `/tar1090/`** : vérifier que le conteneur `tar1090` est
  `healthy` (`docker compose ps tar1090`) et recréer le desktop si nécessaire.

## Aéroports

Sur le radar mobile, les aéroports sont embarqués localement dans
`frontend/static/data/airports.json`. Ils sont affichés en cyan avec les codes
IATA/OACI dans la portée d'affichage courante. Les gros aéroports restent
étiquetés à grande portée ; les aéroports moyens sont étiquetés en priorité à
courte portée pour éviter la surcharge.

Source : [OurAirports](https://ourairports.com/data/), données du domaine public.
Les pistes proviennent de `runways.json` : seuils réels, longueur, largeur,
orientation et désignations. Les taxiways, terminaux et bâtiments ne sont pas
inclus dans ce jeu de données.

La génération et le filtrage du jeu de données sont décrits dans
`frontend/static/data/README.txt`.

## Styles de radar

Trois ambiances sont disponibles, sélectionnables dans
**Administration → AFFICHAGE → STYLE RADAR** :

- `phosphor` : phosphore vert historique ;
- `mono` : monochrome blanc cassé ;
- `atc` : affichage ATC moderne, fond ardoise, lignes fines, blocs de données et
  suppression des effets CRT.

Le style est appliqué à chaud à tous les clients au prochain polling, sans
redémarrage. Le fond, le balayage, la grille et les cibles changent de palette ;
les alertes rouges, les aéroports cyan et les précipitations bleues conservent
leur code couleur fonctionnel.

## Météo

Deux couches météo sont disponibles :

- **Aéroport le plus proche** : distance, nom, code IATA/OACI, catégorie de vol,
  vent, visibilité, plafond, température et METAR brut, recalculés à partir des
  coordonnées GPS configurées.
- **METAR aéroports** avec deux fournisseurs sélectionnables depuis
  l'administration :
  - [Aviation Weather Center / NOAA](https://aviationweather.gov/data/api/) par défaut,
    sans clé API ;
  - [metar-taf.com](https://metar-taf.com/docs/) via `METAR_TAF_API_KEY`
    (un crédit API par station interrogée) ;
  - [CheckWX](https://www.checkwxapi.com/) via `CHECKWX_API_KEY`, avec requêtes
    groupées par 25 stations.
  Catégorie de vol VFR/MVFR/IFR/LIFR, vent, visibilité, plafond, température,
  QNH et message brut. Cliquer sur un aéroport ouvre la fiche météo.
- **Précipitations animées** via
  [RainViewer](https://www.rainviewer.com/api.html) : dernières images radar,
  filtrées pour ne conserver que les échos de précipitations significatifs.
  La couche est recadrée sur le scope et suit la portée et le centre courants.

Le backend met en cache les METAR 10 minutes et les métadonnées RainViewer
2 minutes. Les tuiles radar sont chargées directement par le navigateur.

Pour activer metar-taf.com ou CheckWX, ajouter la clé correspondante :

```dotenv
METAR_TAF_API_KEY=votre_cle_metar_taf
CHECKWX_API_KEY=votre_cle_checkwx
```

Puis :

```bash
docker compose up -d --force-recreate backend
```

Les fournisseurs disposant d'une clé deviennent sélectionnables dans
**SOURCE MÉTÉO** de l'administration. NOAA reste le fournisseur par défaut sans
clé.

## Notes de performance

- Le fond radar (anneaux, compas, réticule) est dessiné une seule fois dans un
  canvas hors écran puis recopié à chaque frame.
- Le cône de balayage est pré-calculé dans un sprite offscreen et simplement
  tourné à 60 FPS.
- Les positions et étiquettes des cibles sont recalculées uniquement lors du
  polling, pas à chaque frame.
- Le DPR Canvas est plafonné à 2 pour préserver la fluidité.
- La boucle `requestAnimationFrame` et le polling sont suspendus quand l'onglet
  passe en arrière-plan.
- Le backend tourne en utilisateur non-root, avec système de fichiers en
  lecture seule et `no-new-privileges`.
