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
- Modes paysage 18:9 et portrait compact, sans défilement.
- Trois styles de radar : phosphore vert, monochrome et ATC moderne.
- Interface desktop standard sur `8083` : carte Leaflet, sans balayage ni
  effets CRT, avec table de flotte, filtres, tri, export CSV et fiches cibles.
- Cache serveur de 4 s, polling frontend de 3 s.
- Aucune ressource JavaScript/CSS frontend externe : Leaflet est embarqué
  localement, compatible Morph Browser / QtWebEngine.

> Outil de visualisation uniquement. Ne pas utiliser pour la navigation aérienne
> ou une décision opérationnelle.

## Architecture RadarDesk

```text
Navigateur / Morph Browser
          │
          │ HTTP :8000
          ▼
┌───────────────────────────┐
│ frontend                  │
│ Nginx :80                 │
│  /        → index/static  │
│  /api/*   → backend       │
└─────────────┬─────────────┘
              │ réseau Docker interne
              ▼
┌───────────────────────────┐
│ backend                   │
│ FastAPI/Uvicorn :8000     │
│ cache 4 s + normalisation│
└─────────────┬─────────────┘
              │ HTTPS
      ┌───────┴────────┐
      ▼                ▼
Airplanes.live     adsb.lol
(source primaire)  (secours)

┌───────────────────────────┐       volume partagé /data
│ admin                     │◄──────── runtime.json ────────┐
│ FastAPI :8000 → hôte 8082 │                             │
│ Basic Auth + UI de config │────► backend /internal/reload┘
└───────────────────────────┘
```

Le frontend mobile (`8081`), l'administration (`8082`) et l'interface desktop
(`8083`) sont exposés sur la machine hôte. Le backend radar n'est pas
accessible directement depuis l'extérieur du réseau Docker.

L'interface desktop est une application statique autonome servie par son propre
Nginx. Elle embarque Leaflet localement, utilise un fond de carte sombre Esri
sans clé API et relaie `/api/` vers le backend ainsi que `/data/` vers le
frontend mobile pour partager les mêmes données aéroports/pistes.

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
└── desktop/
    ├── Dockerfile
    ├── .dockerignore
    ├── nginx.conf
    ├── index.html
    ├── style.css
    ├── app.js
    ├── favicon.svg
    └── vendor/
        └── leaflet/
            ├── leaflet.js
            ├── leaflet.css
            ├── LICENSE
            └── images/
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

Ouvrir la console desktop avancée :

```text
http://localhost:8083
```

Identifiant et mot de passe par défaut : `admin` / `admin`.
Les valeurs personnalisées se trouvent dans `ADSB_ADMIN_USER` et
`ADSB_ADMIN_PASSWORD` du fichier `.env`.

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
| `ADSB_LOG_LEVEL` | `INFO` | Niveau de journalisation |
| `ADSB_CORS_ORIGINS` | `*` | Origines CORS autorisées |
| `ADSB_ADMIN_PORT` | `8082` | Port hôte de l'interface d'administration |
| `ADSB_DESKTOP_PORT` | `8083` | Port hôte de l'interface desktop avancée |
| `ADSB_ADMIN_USER` | `admin` | Utilisateur Basic Auth admin |
| `ADSB_ADMIN_PASSWORD` | `admin` | Mot de passe admin (à changer) |
| `ADSB_RADAR_STYLE` | `phosphor` | Style : `phosphor`, `mono` ou `atc` |
| `ADSB_LOCATION_MODE` | `coords` | Localisation : `coords` ou `airport` |
| `ADSB_AIRPORT_ICAO` | — | Code OACI si `ADSB_LOCATION_MODE=airport` |
| `ADSB_METAR_PROVIDER` | `aviationweather` | Source METAR : `aviationweather`, `metar_taf` ou `checkwx` |
| `METAR_TAF_API_KEY` | — | Clé API metar-taf.com |
| `CHECKWX_API_KEY` | — | Clé API CheckWX |
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

### `POST /internal/reload`

Endpoint interne non exposé par Nginx. Il recharge le fichier
`/data/runtime.json` dans le backend. Il exige l'en-tête `X-Internal-Token`
correspondant à `ADSB_INTERNAL_TOKEN`. Le service d'administration l'appelle
après chaque modification.

## Interface desktop standard

Le service `desktop` est une interface dédiée sur un troisième port TCP. Elle
remplace volontairement le scope PPI par une carte standard, sans balayage
radar, rémanence phosphore ni effets CRT :

- fond de carte sombre Esri et marqueurs avions orientés selon leur cap ;
- aéroports et pistes superposés selon la portée sélectionnée ;
- badges METAR VFR/MVFR/IFR/LIFR et fiche météo au clic sur un aéroport ;
- couche de précipitations RainViewer activable ;
- table complète des cibles dans la bulle ;
- recherche par indicatif, type, hex ou squawk ;
- filtres en vol / au sol / urgences ;
- tri par indicatif, type, altitude, vitesse, squawk ou distance ;
- sélection d'une ligne ou d'un marqueur pour centrer la carte et afficher la
  fiche détaillée de la cible ;
- export CSV de la sélection filtrée ;
- mêmes backend, configuration et données que le radar mobile ;
- Leaflet 1.9.4 embarqué localement, sans CDN JavaScript/CSS.

Le port est configurable avec `ADSB_DESKTOP_PORT`.

## Administration

Le service `admin` est séparé du backend radar :

- interface web sombre et responsive sur `http://<hôte>:8082` ;
- HTTP Basic Auth, avec identifiants par défaut `admin` / `admin` ;
- état du radar : source, cache, nombre de cibles, configuration active ;
- modification à chaud de la position (coordonnées GPS ou code OACI), des
  portées, du cache, de la cadence, des sources ADS-B et de la source METAR.
  En mode OACI, le HUD radar affiche le code de l'aéroport (`· LSGG`) et
  l'aéroport le plus proche passe à `0.0 NM` ;
- le panneau du radar affiche par défaut l'aéroport le plus proche des
  coordonnées configurées, avec bascule CONTACTS / AÉROPORT ;
- persistance dans le volume Docker `radardesk_admin_data` ;
- bouton **Recharger** et **Réinitialiser** vers les variables d'environnement.

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

## Aéroports

Les aéroports sont embarqués localement dans `frontend/static/data/airports.json`.
Ils sont affichés en cyan avec les codes IATA/OACI dans la portée d'affichage
courante. Les gros aéroports restent étiquetés à grande portée ; les aéroports
moyens sont étiquetés en priorité à courte portée pour éviter la surcharge.

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
