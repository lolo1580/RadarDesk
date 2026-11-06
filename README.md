# RadarDesk — Radar ADS-B Docker

**RadarDesk** est un scope radar militaire / CRT en HTML5 Canvas, avec un
backend FastAPI et un frontend statique servi par Nginx, le tout orchestré par
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
- Boutons `−` / `+` pour ajuster la distance d'affichage sans nouvel appel API.
  En portrait, le réglage est intégré à l'en-tête des contacts prioritaires.
- Gestion visuelle des squawks `7500`, `7600`, `7700` et indices militaires.
- Console : horloge UTC, compteur de cibles, 5 contacts prioritaires.
- Modes paysage 18:9 et portrait compact, sans défilement.
- Cache serveur de 2,5 s, polling frontend de 3 s.
- Aucune ressource frontend externe : compatible Morph Browser / QtWebEngine.

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
│ cache 2,5 s + normalisation│
└─────────────┬─────────────┘
              │ HTTPS
      ┌───────┴────────┐
      ▼                ▼
Airplanes.live     adsb.lol
(source primaire)  (secours)
```

Seul le conteneur `frontend` est exposé sur la machine hôte. Le backend n'est
pas accessible directement depuis l'extérieur du réseau Docker.

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
└── frontend/
    ├── Dockerfile
    ├── .dockerignore
    ├── nginx.conf
    ├── index.html
    └── static/
        ├── style.css
        ├── radar.js
        ├── icon.svg
        └── manifest.webmanifest
```

## Démarrage rapide

Prérequis : Docker Engine et le plugin Docker Compose.

```bash
git clone https://github.com/lolo1580/RadarDesk.git
cd RadarDesk

# Facultatif : personnaliser la configuration
cp .env.example .env

# Construire et démarrer les deux conteneurs
docker compose up -d --build

# Vérifier l'état
docker compose ps
```

Ouvrir :

```text
http://localhost:8000
```

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
| `ADSB_CACHE_TTL` | `2.5` | Durée du cache serveur (s) |
| `ADSB_STALE_TTL` | `120` | Durée maximale du cache de secours (s) |
| `ADSB_TIMEOUT` | `4.0` | Timeout d'un appel upstream (s) |
| `ADSB_PRIMARY_PROVIDER` | `airplanes_live` | Source primaire |
| `ADSB_FALLBACK_PROVIDER` | `adsb_lol` | Secours, ou `none` |
| `ADSB_PRIMARY_COOLDOWN` | `600` | Pause après un 403 primaire (s) |
| `ADSB_LOG_LEVEL` | `INFO` | Niveau de journalisation |
| `ADSB_CORS_ORIGINS` | `*` | Origines CORS autorisées |

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
- **Changement de coordonnées non pris en compte** : recréer le backend :

  ```bash
  docker compose up -d --force-recreate backend
  ```

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
