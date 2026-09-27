# Wassal - وصّل : comment ça marche

> Version alignée sur le code de la branche `feature/wassal-mvp`. Tous les exemples et chiffres de ce document sont produits par le code, ou sourcés. Quand quelque chose n'est pas encore fait ou pas encore mesuré, c'est écrit.

---

## L'idée en une phrase

**On parle en Darija, Wassal prépare la commande Yassir, y compris quand l'adresse est « derrière la mosquée ».**

```
"بغيت تاكسي للقارة"  →  Yassir Go · Gare Casa-Voyageurs (33.5894, -7.5906) · confiance 0.90
```

---

## Le problème, sans exagérer

**Ce que Yassir propose aujourd'hui au Maroc :** Yassir **Go** (VTC), Yassir **Food** (restaurants) et Yassir **Market** (courses). L'app et le site existent en arabe, en français et en anglais. [yassir.com/morocco](https://yassir.com/morocco)

**Ce qui reste difficile :**

1. **Lire et taper.** L'arabe de l'app est de l'arabe standard écrit. Une personne âgée, ou peu à l'aise avec l'écrit, *parle* Darija : elle ne navigue pas facilement dans des menus et des champs de recherche.
2. **Donner une adresse.** Au Maroc, on donne des repères : « derrière la mosquée », « à côté du hanout vert ». Le chauffeur ou le livreur appelle le client pour trouver, et tout le monde perd du temps.

Wassal s'attaque à ces deux problèmes : **parler au lieu de taper**, et **convertir des repères en point GPS**.

> Chiffres d'impact : à mesurer, pas à inventer. Pour le contexte social, citer une source officielle, par exemple le taux d'analphabétisme du HCP au recensement 2024, avec le lien. Pour le produit, utiliser nos propres mesures (voir la section « Ce qu'on doit mesurer »).

---

## Le flux, étape par étape

```
🎙️ audio Darija ─► 1. Transcription ─► 2. Normalisation ─► 3. Intention ─► 4. Adresse ─► 5. Décision ─► JSON Yassir
                   (MoulSot v0.3)                           (parser.py)     (landmarks.py)  (api.py)
   ou texte ────────────────────────────┘
```

### 1. Transcription : audio → texte (`transcribe.py`)

- Modèle : **[MoulSot v0.3](https://huggingface.co/atlasia/moulsot.v0.3)** d'Atlasia. Architecture Qwen3-ASR, licence Apache 2.0, entraîné sur environ 1 500 h de Darija, dont 80 h de transcriptions de référence. Il gère le mélange Darija / français / arabe.
- **Qualité réelle (fiche officielle) : WER 38.99 %, CER 12.58 %.** Environ 1 mot sur 3 peut être mal transcrit, mais 7 caractères sur 8 sont justes. C'est le **meilleur score publié pour la Darija** (n°1 du leaderboard), mais ce n'est **pas** « 95 % de précision ».
- **Conséquence pour nous :** le parser doit tolérer les fautes. C'est pour ça qu'il cherche des mots-clés plutôt qu'une phrase exacte.
- **Formats :** wav, flac, ogg, mp3, m4a, aac, webm, opus, mp4. Le m4a des téléphones est décodé avec PyAV, sans ffmpeg à installer.
- **Pas encore mesuré :** le temps de transcription sur CPU et sur GPU.

### 2. Normalisation (`normalize.py`)

La Darija s'écrit en arabe (`بغيت`), en arabizi (`bghit`, avec `3/7/9` pour `ع/ح/ق`) et en français, souvent dans la même phrase. On **ne traduit pas** d'un alphabet à l'autre. On uniformise chaque alphabet :

| Règle | Avant | Après |
|---|---|---|
| Minuscules, accents supprimés | `Derrière la Mosquée !` | `derriere la mosquee` |
| Variantes de lettres arabes | `أ إ آ` · `ة` · `گ` | `ا` · `ه` · `ك` |
| Harakat supprimés | `القارَة` | `القاره` |

Ensuite, chaque mot-clé tolère les **préfixes collés** : `ltaxi`, `للقارة`, `ldjamaa`, `fljamaa` sont reconnus. Les dictionnaires contiennent les deux alphabets (`taxi` et `تاكسي`), et c'est comme ça qu'on couvre les deux écritures.

### 3. Intention : quel service ? (`parser.py`)

Chaque service a des mots-clés pondérés. Le service qui a le plus gros score gagne.

| Service Wassal | Produit Yassir | Mots-clés forts (poids 3) | Indices (poids 1 à 2) |
|---|---|---|---|
| `taxi` | **Yassir Go** | `taxi`, `taksi`, `تاكسي`, `طاكسي`, `درايفر` | `hezni`, `diini`, `nmchi` (2) · `waslni` (1.5) |
| `food` | **Yassir Food** | `makla`, `ماكلة`, `resto`, `snack`, `mcdo` | `ji3an`, `ghda`, `ftour` (1.5) · `jib lia` (1) |
| `market` | **Yassir Market** | `hanout`, `حانوت`, `courses`, `marjane`, `bim` | `chri lia`, `souk` (2) · `jib lia`, `kolchi` (1) |
| `package` | *(pas dans l'offre Maroc)* | `colis`, `package`, `طرد`, `sift` | `wra9`, `sarout` (2) · `7aja` (1) |

**Les articles comptent aussi :**

| Type d'article | Service visé | Poids | Exemples |
|---|---|---|---|
| Courses | Market | +2 | `khobz` (pain), `7lib` (lait), `zit` (huile), `atay`, `sokar`, `lma`… |
| Plats | Food | +2 | `pizza`, `tacos`, `tajine`, `harira`, `msemen`… |
| Boissons | Food **et** Market | +1 chacun | `coca`, `jus` |

Les quantités sont lues quand elles précèdent l'article : `joj khobz w 3 7lib` donne 2 pains et 3 laits.

**Urgence :** `fasa`, `daba`, `dlak`, `zerba`, `دغيا` → `urgent` ; `basr`, `bsr3a`, `vite` → `quick` ; sinon `normal`.

**Confiance de l'intention :**

```
force  = min(1, meilleur_score / 3)            → un mot explicite suffit
marge  = 1 − second_score / (2 × meilleur)     → baisse si deux services se disputent
confiance = 0.5 + 0.45 × force × marge          (plafonnée à 0.99, 0 si rien trouvé)
```

Exemple : `sift colis` donne 0.95 (aucun concurrent). `waslni colis` donne 0.85, parce que `waslni` (« emmène-moi ») tire aussi vers le taxi.

### 4. Adresse : repère → GPS (`landmarks.py`)

- **La base :** 8 repères curés sur 3 villes, avec coordonnées vérifiées. Viennent s'y ajouter les repères proposés par les utilisateurs.

  | Ville | Repère | Coordonnées |
  |---|---|---|
  | Casablanca | Gare Casa-Voyageurs | 33.5894, -7.5906 |
  | Casablanca | Mosquée Hassan II | 33.6083, -7.6328 |
  | Casablanca | Aéroport Mohammed V | 33.3675, -7.5898 |
  | Fès | Médina (Fès el-Bali) | 34.0648, -4.9730 |
  | Fès | Gare de Fès | 34.0462, -4.9914 |
  | Marrakech | Place Jemaa El Fna | 31.6258, -7.9892 |
  | Marrakech | Mosquée Koutoubia | 31.6237, -7.9937 |
  | Marrakech | Gare de Marrakech | 31.6309, -8.0158 |

- **Un mot générique dépend de la ville.** `jamaa` (la mosquée) donne Hassan II à Casablanca, la Médina (Quaraouiyine) à Fès et la Koutoubia à Marrakech.
- **L'alias le plus long gagne :** `jemaa el fna` n'est pas confondu avec `jamaa`.
- **Relations spatiales détectées :** `derrière` / `wra` / `مور` → `behind`, `7da` / `حدا` / `qrib` → `near`, `9dam` / `قدام` → `front`. La relation est renvoyée et baisse un peu la confiance (× 0.95). **Le point n'est pas encore décalé** : « derrière la mosquée » renvoie aujourd'hui le point de la mosquée.
- **Départ et arrivée :** dans `mn lgare l jamaa`, la gare est le départ et la mosquée l'arrivée.
- **Confiance d'un repère :**
  - repère curé : 0.95 ;
  - repère proposé par un utilisateur : de 0.6 à 0.8 selon les votes ;
  - × 0.7 si trouvé hors de la ville demandée ;
  - × 0.6 si le même mot existe dans plusieurs villes et qu'aucune ville n'est donnée.
- **Crowdsourcing :** `POST /wassal/landmarks`. Le point doit être au Maroc et à moins de 40 km du centre de la ville. S'il existe déjà, c'est un vote.

### 5. Décision : prêt pour Yassir ? (`api.py`)

```
confiance globale = confiance intention × confiance destination (si une adresse est utilisée)
```

`ready_for_yassir = true` quand les 3 conditions suivantes sont réunies :

1. le service est reconnu ;
2. l'info indispensable est présente :
   - destination pour `taxi` et `package` ;
   - articles **ou** destination pour `food` et `market` ; sans destination, on livre à la position actuelle ;
3. la confiance globale est ≥ 0.6 (`MIN_CONFIDENCE`).

Sinon, `missing_fields` et `warnings` disent ce qui manque, pour que l'app puisse **poser une question** au lieu d'échouer.

---

## Exemples réels (sortie du code)

| Commande | Ville | Résultat | Confiance | Prêt ? |
|---|---|---|---|---|
| `بغيت تاكسي للقارة` | Casablanca | Yassir Go → Gare Casa-Voyageurs | 0.90 (0.95 × 0.95) | ✅ |
| `jib lia khobz o 7lib o zit` | Casablanca | Yassir Market : bread, milk, oil → position actuelle | 0.92 | ✅ |
| `taxi derrière la mosquée` | Casablanca | Yassir Go → *derrière* Mosquée Hassan II | 0.85 (0.95 × 0.90) | ✅ |
| `bghit taxi l la gare` | *(aucune)* | Yassir Go → gare, mais ville ambiguë | 0.54 | ❌ confiance trop basse |
| `bghit taxi` | Casablanca | Yassir Go, pas de destination | 0.95 | ❌ `missing_fields: ["destination"]` |

**JSON envoyé pour le premier exemple** (`yassir_request`) :

```json
{
  "service": "ride",
  "category": "taxi",
  "product": "Yassir Go",
  "pickup":  { "type": "current_location" },
  "dropoff": { "type": "landmark", "lat": 33.5894, "lng": -7.5906, "label": "Gare Casa-Voyageurs" },
  "priority": "normal",
  "customer": { "phone": "+212612345678" },
  "source": "wassal_voice"
}
```

> Ce format est **notre proposition**. On ne connaît pas l'API partenaire de Yassir, et rien n'est envoyé à Yassir pour l'instant. Le JSON est prêt, l'intégration reste à faire avec eux.

---

## Ce qui ne marche pas encore (à dire franchement)

| Limite | Exemple | Qui s'en occupe |
|---|---|---|
| Les négations ne sont pas comprises | `ma bghitch taxi` est lu comme une demande de taxi | A (parser) |
| Le point n'est pas décalé pour « derrière / à côté » | on renvoie le point de la mosquée | B (adresses) |
| « La mosquée » = la grande mosquée de la ville, pas celle du quartier | il faut la position de l'utilisateur | B (adresses) |
| Seulement 8 repères curés | un import OpenStreetMap est prévu | B (adresses) |
| MoulSot jamais mesuré sur nos propres audios | pas de temps de réponse ni de score maison | A (voix) |
| Pas d'envoi réel à Yassir | le JSON est prêt, l'API partenaire est inconnue | équipe |
| Colis hors de l'offre Yassir Maroc | la réponse contient un avertissement | équipe (garder ou retirer) |
| Questions de prix (« شحال؟ ») non gérées | — | plus tard |

---

## Ce qu'on doit mesurer (nos vrais chiffres pour le jury)

1. **Compréhension de bout en bout :** % de commandes vocales réelles transformées en JSON correct, sur un corpus de 50 à 100 audios enregistrés avec consentement.
2. **Transcription :** WER et CER de MoulSot sur *nos* audios, à comparer aux 38.99 % / 12.58 % de la fiche.
3. **Adresses :** erreur moyenne en mètres sur 30 adresses informelles réelles dont on connaît le vrai point GPS.
4. **Temps de réponse :** audio → JSON, sur CPU et sur GPU.
5. **Tests utilisateurs :** nombre de personnes (âgées ou peu à l'aise avec l'écrit) qui réussissent une commande sans aide, sur 5 à 10 essais.

---

## Pistes d'extension (à présenter comme des pistes, pas comme des faits)

- **Algérie et Tunisie :** Yassir est né en Algérie, son premier marché. La Darja algérienne est proche de la Darija marocaine, mais MoulSot est entraîné sur le marocain : il faudrait le valider, voire l'adapter.
- **« Commander pour quelqu'un d'autre » :** Yassir propose déjà cette option au Maroc. Wassal pourrait l'utiliser, par exemple pour qu'un proche commande à la voix pour un parent âgé.
- **Colis :** si Yassir lance la livraison de colis au Maroc, la détection est déjà prête.

---

## Sources

- Yassir, présentation et chiffres (8 marchés, 45 villes, plus de 6 M d'utilisateurs, 130 000 partenaires) : [yassir.com/about-us](https://yassir.com/about-us)
- Yassir Maroc, services Go / Food / Market et langues du site : [yassir.com/morocco](https://yassir.com/morocco)
- MoulSot v0.3, fiche du modèle, WER / CER et licence : [huggingface.co/atlasia/moulsot.v0.3](https://huggingface.co/atlasia/moulsot.v0.3)
- Coordonnées des repères : OpenStreetMap
