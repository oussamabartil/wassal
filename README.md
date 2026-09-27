# Wassal - وصّل

**Assistant vocal en Darija pour Yassir.** You say *"بغيت تاكسي للقارة"* and Wassal returns a structured request that Yassir can execute.

> Candidature **Yassir AI for Everyday Impact** (Maroc) : rendre Yassir accessible aux personnes qui parlent Darija mais lisent ou tapent difficilement.

```
 🎙️ audio Darija ──► MoulSot v0.3 (ASR) ──► texte ──► parser (intention) ──► landmarks (GPS) ──► JSON Yassir
                                         ▲
                     texte Darija ───────┘
```

| Entrée | Sortie |
|---|---|
| `بغيت تاكسي للقارة` (Casablanca) | `ride / taxi` → Gare Casa-Voyageurs `33.5894, -7.5906` |
| `jib lia khobz o 7lib` | `delivery / food`, items `["bread", "milk"]` |
| `waslni package ldjamaa fasa` (Fès) | `delivery / package` → Médina Fès, urgence `urgent` |
| `taxi derrière la mosquée` (Casablanca) | `ride / taxi` → *derrière* Mosquée Hassan II |

---

## Démarrage rapide

**Python 3.9+** (testé sur 3.13). Aucune version de Python n'est bloquée : `qwen-asr` tire lui-même les versions de `torch`/`transformers`/`accelerate` compatibles avec votre interpréteur.

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env          # Windows : copy .env.example .env
python src/api.py
```

> ⚠️ **Installation lourde (~1-2 Go)** : `qwen-asr` (le loader officiel de MoulSot) entraîne `torch`, `transformers`, `accelerate` et `gradio`. Si vous testez uniquement le parsing de texte et les adresses (pas l'audio), vous pouvez ignorer cette étape et installer seulement `flask flask-cors python-dotenv requests pytest` — `src/api.py` démarre quand même, et `/wassal/command` en JSON texte fonctionne normalement ; seul l'upload audio renverra `MODEL_UNAVAILABLE`.

Ouvrir **http://localhost:5000** pour l'interface web, ou appeler l'API directement :

```bash
curl -X POST http://localhost:5000/wassal/command \
  -H "Content-Type: application/json" \
  -d '{"darija_text": "bghit taxi l jemaa el fna daba", "city": "marrakech", "phone": "0612345678"}'
```

Tests (sans télécharger le modèle, en quelques secondes) :

```bash
pytest -v
```

> Le modèle MoulSot est téléchargé depuis HuggingFace **au premier envoi audio**, pas au démarrage. Mettez `PRELOAD_MODEL=true` pour le charger au lancement. Formats acceptés : wav, flac, ogg, mp3, m4a, aac, webm, opus, mp4. Les formats compressés sont décodés avec PyAV, qui embarque ffmpeg : aucune installation système nécessaire.

---

## Structure

```
wassal/
├── src/
│   ├── transcribe.py   # MoulSot v0.3 : audio -> texte (chargement paresseux, thread-safe)
│   ├── parser.py       # Texte Darija -> service, sous-type, urgence, articles, confiance
│   ├── landmarks.py    # Repères marocains -> GPS, relations spatiales, crowdsourcing
│   ├── normalize.py    # Normalisation arabe / arabizi / français partagée
│   └── api.py          # Serveur Flask (CORS, validation, erreurs JSON standardisées)
├── frontend/index.html # Interface web mobile-friendly (servie sur /)
├── tests/test_commands.py
├── .env.example
└── requirements.txt
```

---

## API

Toutes les réponses suivent la même enveloppe :

```jsonc
// succès
{ "success": true,  "request_id": "a1b2c3d4e5f6", "processing_ms": 3, "data": { ... } }
// erreur
{ "success": false, "request_id": "a1b2c3d4e5f6", "error": { "code": "INVALID_PHONE", "message": "...", "details": {} } }
```

### `GET /wassal/test`

Health check : version, état du modèle ASR, nombre de repères, villes supportées.

### `POST /wassal/command`

**JSON**

```json
{ "darija_text": "بغيت تاكسي للقارة", "city": "casablanca", "phone": "0612345678" }
```

**ou multipart/form-data** avec un fichier `audio` (+ `city`, `phone`) : l'audio est transcrit par MoulSot puis traité comme du texte.

| Champ | Requis | Notes |
|---|---|---|
| `darija_text` | oui (sauf si `audio`) | 500 caractères max ; arabe, arabizi, français ou mélange |
| `city` | non | `casablanca`, `fes`, `marrakech` (accepte `Casa`, `فاس`, `Fès`…) |
| `phone` | non | Numéro marocain, normalisé en `+2126XXXXXXXX` |

**Réponse (`data`)**

```json
{
  "service_type": "ride",
  "subtype": "taxi",
  "destination": {
    "lat": 33.5894, "lng": -7.5906,
    "place": "Gare Casa-Voyageurs", "place_name": "Gare Casa-Voyageurs",
    "relation": null, "confidence": 0.95, "city": "casablanca"
  },
  "pickup": null,
  "items": [],
  "urgency": "normal",
  "confidence": 0.9,
  "confidence_breakdown": { "intent": 0.95, "destination": 0.95 },
  "ready_for_yassir": true,
  "missing_fields": [],
  "warnings": [],
  "language": "darija",
  "yassir_request": {
    "service": "ride", "category": "taxi",
    "pickup":  { "type": "current_location" },
    "dropoff": { "type": "landmark", "lat": 33.5894, "lng": -7.5906, "label": "Gare Casa-Voyageurs" },
    "priority": "normal",
    "customer": { "phone": "+212612345678" },
    "source": "wassal_voice"
  }
}
```

`ready_for_yassir` est `true` quand le service est identifié, que les champs nécessaires sont présents (destination pour taxi/colis, articles pour la nourriture) et que la confiance dépasse `MIN_CONFIDENCE` (0.6 par défaut). Sinon, `missing_fields` et `warnings` expliquent pourquoi, pour que l'app puisse poser une question de relance.

`yassir_request` est le **contrat d'intégration proposé**. Il faudra l'aligner sur l'API partenaire de Yassir.

**Codes d'erreur :** `MISSING_TEXT`, `TEXT_TOO_LONG`, `INVALID_JSON`, `INVALID_CITY`, `INVALID_PHONE`, `UNSUPPORTED_FORMAT`, `UNSUPPORTED_MEDIA_TYPE` (415), `PAYLOAD_TOO_LARGE` (413), `MODEL_UNAVAILABLE` (503), `NO_SPEECH` / `DECODE_ERROR` (422), `INTERNAL_ERROR` (500).

### `GET /wassal/landmarks?city=fes`

Liste les repères connus (curés et crowdsourcés).

### `POST /wassal/landmarks` (crowdsourcing)

```json
{ "name": "Café Atlas", "city": "casablanca", "lat": 33.59, "lng": -7.61, "aliases": ["9ahwa atlas", "قهوة أطلس"] }
```

Les coordonnées sont validées : elles doivent être au Maroc et à moins de 40 km du centre de la ville. Un repère crowdsourcé démarre à une confiance de 0.6, qui augmente de 0.05 à chaque nouvelle soumission identique, jusqu'à 0.8 maximum. Les repères curés restent à 0.95. Si `WASSAL_API_TOKEN` est défini, l'en-tête `X-API-Key` est exigé.

---

## Comment ça marche

### 1. Normalisation (`normalize.py`)

La Darija s'écrit de trois façons, souvent mélangées : alphabet arabe (`بغيت`), arabizi (`bghit`, avec `3/7/9` pour `ع/ح/ق`) et français (`la gare`). Avant tout matching, le texte est mis en minuscules, les accents et harakat sont retirés, et les variantes de lettres sont unifiées (`أ/إ/آ → ا`, `ة → ه`, `گ → ك`). Les mots-clés tolèrent aussi les préfixes collés : `ltaxi`, `للقارة`, `fljamaa`, `ldjamaa`.

### 2. Intention (`parser.py`)

Chaque service a un dictionnaire de mots-clés pondérés :

| Poids | Signification | Exemples |
|---|---|---|
| 3 | mot explicite | `taxi`, `تاكسي`, `درايفر`, `colis`, `طرد`, `makla` |
| 2 | indice fort | `hezni`, `diini`, `hanout`, `طلبية`, chaque article alimentaire |
| 1-1.5 | indice faible | `waslni`, `jib`, `kolchi`, `7aja` |

La confiance combine la force du signal et l'écart avec le deuxième service. `waslni` seul veut dire "emmène-moi" (course), mais `waslni package` devient une livraison de colis.

Urgence : `fasa`, `daba`, `dlak`, `zerba`, `دغيا` → `urgent` ; `basr`, `bsr3a`, `vite` → `quick` ; sinon `normal`.

### 3. Adresses (`landmarks.py`)

- Les alias génériques (`gare`, `jamaa`, `المحطة`) sont résolus **dans la ville demandée** : `jamaa` donne Hassan II à Casablanca, la Médina (Quaraouiyine) à Fès, la Koutoubia à Marrakech.
- L'alias le plus long gagne : `jemaa el fna` n'est pas confondu avec `jamaa`.
- Relations spatiales : `derrière` / `wra` / `مور` → `behind`, `7da` / `حدا` / `qrib` → `near`, `9dam` / `قدام` → `front`.
- Départ et arrivée : `mn lgare l jamaa` donne `pickup` = Gare et `destination` = Mosquée.
- Sans ville, un alias présent dans plusieurs villes est marqué ambigu (confiance × 0.6), et `ready_for_yassir` reste `false`.

### 4. Transcription (`transcribe.py`)

[MoulSot v0.3](https://huggingface.co/atlasia/moulsot.v0.3) (Apache 2.0, architecture Qwen3-ASR). Le modèle n'est pas encore reconnu par `transformers.pipeline()` : on utilise le loader officiel de sa fiche, le paquet `qwen_asr` (`Qwen3ASRModel.from_pretrained(...).transcribe(...)`). L’audio est décodé par Wassal (libsndfile, ou PyAV pour m4a/webm), converti en mono et passé au modèle en `(samples, sample_rate)`. La durée est limitée à `MAX_AUDIO_SECONDS`. `DEVICE=auto` choisit CUDA, MPS ou CPU. La fonction ne lève jamais d'exception : elle renvoie toujours `{status, transcription, language, ...}`.

> Le nom `01Yassine/moulsot.v0.3` (celui de l'énoncé initial) redirige vers le dépôt canonique `atlasia/moulsot.v0.3` sur HuggingFace ; c'est celui-ci qu'utilise `MOULSOT_MODEL` par défaut.

```bash
python src/transcribe.py commande.wav
```

---

## Configuration (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `YASSIR_API_KEY` | – | Clé partenaire Yassir (réservée, pas encore utilisée) |
| `FLASK_ENV` | `production` | `development` active le mode debug |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Adresse d'écoute |
| `MOULSOT_MODEL` | `atlasia/moulsot.v0.3` | Modèle HuggingFace |
| `DEVICE` | `auto` | `auto`, `cpu`, `cuda`, `mps` |
| `ASR_LANGUAGE` | `Arabic` | Langue passée à `qwen_asr` |
| `PRELOAD_MODEL` | `false` | Charger le modèle au démarrage |
| `MAX_AUDIO_SECONDS` | `60` | Durée audio max |
| `MIN_CONFIDENCE` | `0.6` | Seuil de `ready_for_yassir` |
| `LANDMARKS_STORE` | `data/crowdsourced_landmarks.json` | Stockage du crowdsourcing |
| `WASSAL_API_TOKEN` | – | Protège `POST /wassal/landmarks` |
| `CORS_ORIGINS` | `*` | Origines autorisées |

---

## Ajouter des mots ou des repères

- **Nouveau mot Darija :** ajoutez-le dans `SERVICES`, `FOOD_ITEMS` ou `URGENCY` dans [src/parser.py](src/parser.py), puis un cas dans `tests/test_commands.py`.
- **Nouveau repère curé :** ajoutez une entrée à `DEFAULT_LANDMARKS` dans [src/landmarks.py](src/landmarks.py) (coordonnées vérifiées sur OpenStreetMap).
- **Nouvelle ville :** ajoutez-la dans `CITIES` avec son centre et ses alias.

## Limites connues et prochaines étapes

- Parser à base de mots-clés : explicable et rapide, mais il ne comprend pas les négations (`ma bghitch taxi`). Étape suivante : un classifieur léger fine-tuné sur des commandes réelles, en gardant le parser comme repli.
- 8 repères curés sur 3 villes : la base doit grandir, via le crowdsourcing et un import OpenStreetMap.
- Pas d'envoi réel à Yassir : `yassir_request` est prêt, l'appel HTTP reste à brancher une fois la spec partenaire connue.
- Serveur de développement Flask : en production, utiliser `gunicorn "src.api:create_app()"` ou `waitress` sous Windows, avec un rate limiting.

## Licence

Modèle MoulSot v0.3 : Apache 2.0. Licence du code Wassal : à définir (ajouter un fichier `LICENSE`).
