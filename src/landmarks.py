"""
Résolveur d'adresses marocaines à partir de vrais lieux (OpenStreetMap).
Moroccan landmark resolver backed by real places from OpenStreetMap.

Au Maroc, on ne donne pas "12 rue X" mais "derrière la mosquée" ou "حدا القارة".
Deux façons de désigner un lieu :

1. Par son NOM : "jamaa hassan 2", "pharmacie al amal", "koutoubia".
   -> on cherche ce nom parmi les lieux réels de la ville.
2. Par sa CATÉGORIE : "la mosquée", "lfarmasyan", "7da bim".
   -> on prend le lieu réel de cette catégorie le plus proche de l'utilisateur.
      Sans position, on ne devine pas : le lieu reste "non résolu" et l'app
      doit demander la position (sauf gare / aéroport, qui ont un lieu par défaut).

Sources, par ordre de confiance :
  - curated      : quelques grands repères vérifiés à la main (0.95)
  - osm          : lieux importés d'OpenStreetMap par scripts/import_osm.py (0.90)
  - crowdsourced : lieux proposés par les utilisateurs (0.60 -> 0.80 avec les votes)
"""

import json
import logging
import math
import os
import re
import tempfile
import threading
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    from .categories import BRANDS, CATEGORIES, brand_category, category_label
    from .normalize import PREFIXES, compile_term, normalize_text, strip_arabic_article, term_variants
    from .parser import ITEMS, SERVICES, URGENCY
except ImportError:  # pragma: no cover - direct script execution
    from categories import BRANDS, CATEGORIES, brand_category, category_label
    from normalize import PREFIXES, compile_term, normalize_text, strip_arabic_article, term_variants
    from parser import ITEMS, SERVICES, URGENCY

logger = logging.getLogger(__name__)

LatLng = Tuple[float, float]

# ---------------------------------------------------------------------------
# Villes supportées / supported cities
# ---------------------------------------------------------------------------

CITIES: Dict[str, Dict[str, Any]] = {
    "casablanca": {
        "label": "Casablanca",
        "center": (33.5731, -7.5898),
        # (sud, ouest, nord, est) : zone importée depuis OpenStreetMap
        "bbox": (33.45, -7.75, 33.66, -7.45),
        "aliases": ["casablanca", "casa", "dar lbida", "dar el beida", "الدار البيضاء", "كازا", "كازابلانكا"],
    },
    "fes": {
        "label": "Fès",
        "center": (34.0331, -5.0003),
        "bbox": (33.97, -5.10, 34.10, -4.90),
        "aliases": ["fes", "fès", "fez", "fas", "فاس"],
    },
    "marrakech": {
        "label": "Marrakech",
        "center": (31.6295, -7.9811),
        "bbox": (31.55, -8.12, 31.72, -7.90),
        "aliases": ["marrakech", "marrakesh", "mraksh", "mrakech", "kech", "مراكش"],
    },
}

# Rayon max autour du centre-ville pour un repère crowdsourcé (km).
MAX_CITY_RADIUS_KM = 40.0

# Boîte englobante du Maroc / Morocco bounding box (incl. southern provinces).
MOROCCO_BBOX = {"lat_min": 20.7, "lat_max": 36.0, "lng_min": -17.2, "lng_max": -0.9}

# Un lieu OSM à moins de cette distance d'un repère curé de même catégorie
# est un doublon du repère curé (on garde le curé).
CURATED_DEDUPE_M = 300.0
# Plusieurs lieux de même nom à moins de cette distance = le même endroit.
SAME_PLACE_M = 300.0

# ---------------------------------------------------------------------------
# Grands repères vérifiés à la main (coordonnées OpenStreetMap).
# Seulement des alias PROPRES à ce lieu : "la mosquée" n'est plus Hassan II,
# c'est la mosquée la plus proche (catégorie). `default_for` : lieu utilisé
# quand l'utilisateur dit "la gare" sans donner sa position.
# ---------------------------------------------------------------------------

DEFAULT_LANDMARKS: List[Dict[str, Any]] = [
    # --- Casablanca ---
    {
        "id": "casa_gare_voyageurs", "city": "casablanca", "category": "train_station",
        "name": "Gare Casa-Voyageurs", "lat": 33.5894, "lng": -7.5906,
        "aliases": ["gare casa voyageurs", "casa voyageurs", "لاكار ديال كازا فوياجور"],
        "default_for": ["train_station"],
    },
    {
        "id": "casa_mosquee_hassan2", "city": "casablanca", "category": "mosque",
        "name": "Mosquée Hassan II", "lat": 33.6083, "lng": -7.6328,
        "aliases": ["mosquee hassan 2", "mosquee hassan ii", "hassan 2", "hassan ii", "jamaa hassan",
                    "jam3 hassan", "جامع الحسن الثاني", "مسجد الحسن الثاني"],
    },
    {
        "id": "casa_aeroport", "city": "casablanca", "category": "airport",
        "name": "Aéroport Mohammed V", "lat": 33.3675, "lng": -7.5898,
        "aliases": ["aeroport mohammed v", "aeroport mohammed 5", "مطار محمد الخامس", "nouasser", "النواصر"],
        "default_for": ["airport"],
    },
    # --- Fès ---
    {
        "id": "fes_medina", "city": "fes", "category": "medina",
        "name": "Médina Fès (Fès el-Bali)", "lat": 34.0648, "lng": -4.9730,
        "aliases": ["medina fes", "fes el bali", "fas lbali", "medina", "lmdina", "lmedina",
                    "المدينة القديمة", "فاس البالي", "bab boujloud", "bab bujlud", "باب بوجلود"],
    },
    {
        "id": "fes_gare", "city": "fes", "category": "train_station",
        "name": "Gare de Fès", "lat": 34.0462, "lng": -4.9914,
        "aliases": ["gare fes", "gare de fes"],
        "default_for": ["train_station"],
    },
    # --- Marrakech ---
    {
        "id": "rak_jemaa_el_fna", "city": "marrakech", "category": "landmark",
        "name": "Place Jemaa El Fna", "lat": 31.6258, "lng": -7.9892,
        "aliases": ["jemaa el fna", "jamaa el fna", "jemaa lfna", "jamaa lfna", "jama3 lfna",
                    "djemaa el fna", "جامع الفنا", "جامع لفنا", "sa7a", "الساحة", "la place"],
    },
    {
        "id": "rak_koutoubia", "city": "marrakech", "category": "mosque",
        "name": "Mosquée Koutoubia", "lat": 31.6237, "lng": -7.9937,
        "aliases": ["koutoubia", "kotobia", "lkoutoubia", "الكتبية", "كتبية"],
    },
    {
        "id": "rak_gare", "city": "marrakech", "category": "train_station",
        "name": "Gare de Marrakech", "lat": 31.6309, "lng": -8.0158,
        "aliases": ["gare marrakech", "gare de marrakech"],
        "default_for": ["train_station"],
    },
]

# Relations spatiales ("derrière la mosquée") / spatial relations.
RELATIONS: Dict[str, List[str]] = {
    "behind": ["derriere", "wra", "wara", "mor", "mour", "lor", "lour", "ور", "ورا", "مور", "لور"],
    "front": ["devant", "en face", "en face de", "9dam", "qdam", "gdam", "قدام", "gbalt", "9balt", "قبالت"],
    "near": ["pres", "pres de", "a cote", "a cote de", "qrib", "9rib", "grib", "قريب", "7da", "hda",
             "7dah", "حدا", "جنب", "janb", "juste a cote"],
    "inside": ["dakhl", "f wost", "fwost", "داخل", "وسط", "dans"],
}

# Mots indiquant l'origine ("mn la gare") / origin markers -> pickup.
ORIGIN_MARKERS = {"mn", "men", "min", "من", "depuis"}
# Mots ignorés entre le marqueur et le repère / filler words between marker and landmark.
_FILLERS = {"l", "el", "al", "la", "le", "les", "ل", "ال", "d", "de", "dyal", "dial"}

# Noms trop génériques pour identifier un lieu précis (on passe alors par la catégorie).
_GENERIC_NAMES = {
    "hanout", "cafe", "cafe restaurant", "restaurant", "snack", "boulangerie", "patisserie",
    "ecole", "ecole primaire", "college", "lycee", "centre de sante", "dispensaire", "mosquee",
    "pharmacie", "banque", "poste", "hotel", "parc", "jardin", "stade", "مقهى", "مسجد", "صيدلية",
    "مدرسة", "حانوت", "مخبزة", "مطعم", "salle de priere", "مصلى", "masjid", "مركز صحي",
}

_RELATION_PATTERNS = [
    (rel, re.compile(rf"(?<!\S){re.escape(v)}(?!\S)"))
    for rel, words in RELATIONS.items()
    for v in term_variants(words)
]
_TOKEN_RE = re.compile(r"\S+")
_PREFIXES_BY_LENGTH = sorted(PREFIXES, key=len, reverse=True)

# Mots de catégorie normalisés : {catégorie: [mots]} (+ enseignes "brand:<clé>").
CATEGORY_WORDS: Dict[str, List[str]] = {
    cat: term_variants(spec["words"]) for cat, spec in CATEGORIES.items() if spec["words"]
}
CATEGORY_WORDS.update({brand_category(key): term_variants(words) for key, words in BRANDS.items()})
_ALL_CATEGORY_WORDS: Set[str] = {w for words in CATEGORY_WORDS.values() for w in words}

# Mots de commande ("taxi", "jib", "khobz", "daba"...) : jamais un nom de lieu,
# même si un café OSM s'appelle "Taxi".
_COMMAND_WORDS: Set[str] = set(term_variants(
    [w for spec in SERVICES.values() for words in spec["keywords"].values() for w in words]
    + [w for words in ITEMS.values() for w in words]
    + [w for words in URGENCY.values() for w in words]
    + ["bghit", "bghina", "بغيت", "lia", "liya", "ليا", "3afak", "عفاك", "salam", "سلام"]
))
_BRAND_LOOKUP: Dict[str, str] = {
    normalize_text(w): key for key, words in BRANDS.items() for w in words
}


# ---------------------------------------------------------------------------
# Modèle de données
# ---------------------------------------------------------------------------

@dataclass
class Landmark:
    """Un lieu réel / a real place."""

    id: str
    name: str
    city: str
    lat: float
    lng: float
    aliases: List[str] = field(default_factory=list)
    category: str = "other"
    source: str = "curated"  # "curated" | "osm" | "crowdsourced"
    verified: bool = True
    votes: int = 0
    default_for: List[str] = field(default_factory=list)
    brands: List[str] = field(default_factory=list)

    @property
    def base_confidence(self) -> float:
        """Curé 0.95, OSM 0.90, crowdsourcé 0.6 -> 0.8 selon les votes."""
        if self.source == "curated":
            return 0.95
        if self.source == "osm":
            return 0.90
        return min(0.8, 0.6 + 0.05 * self.votes)


@dataclass
class LandmarkMatch:
    """Un lieu désigné dans le texte / a place referred to in the text."""

    start: int
    end: int
    matched_text: str
    match_type: str  # "name" | "category"
    landmark: Optional[Landmark] = None
    category: Optional[str] = None
    candidates: List[Landmark] = field(default_factory=list)
    relation: Optional[str] = None
    role: str = "destination"  # "destination" | "pickup"
    ambiguous: bool = False
    city_fallback: bool = False
    distance_m: Optional[float] = None
    unresolved_reason: Optional[str] = None

    @property
    def confidence(self) -> float:
        if self.landmark is None:
            return 0.0
        conf = self.landmark.base_confidence
        if self.match_type == "category":
            # "la mosquée" -> plus le lieu trouvé est proche, plus on est sûr.
            conf = _distance_confidence(self.distance_m) if self.distance_m is not None else 0.8
        if self.relation:  # "derrière X" est approximatif / approximate position
            conf *= 0.95
        if self.city_fallback:  # trouvé hors de la ville demandée
            conf *= 0.7
        if self.ambiguous:  # plusieurs lieux possibles, rien pour trancher
            conf *= 0.6
        return round(conf, 2)

    def to_dict(self) -> Dict[str, Any]:
        if self.landmark is None:
            return {
                "matched_text": self.matched_text,
                "category": self.category,
                "category_label": category_label(self.category or ""),
                "role": self.role,
                "reason": self.unresolved_reason,
            }
        lm = self.landmark
        return {
            "lat": lm.lat,
            "lng": lm.lng,
            "place_name": lm.name,
            "place": lm.name,
            "landmark_id": lm.id,
            "city": lm.city,
            "category": lm.category,
            "category_label": category_label(lm.category),
            "match_type": self.match_type,
            "relation": self.relation,
            "matched_text": self.matched_text,
            "distance_m": round(self.distance_m) if self.distance_m is not None else None,
            "candidates": len(self.candidates) or 1,
            "source": lm.source,
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# Géométrie
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance à vol d'oiseau en km / great-circle distance in km."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _distance_m(a: LatLng, lm: Landmark) -> float:
    return haversine_km(a[0], a[1], lm.lat, lm.lng) * 1000


def _distance_confidence(distance_m: float) -> float:
    """Confiance d'un lieu "le plus proche" selon sa distance à l'utilisateur."""
    if distance_m <= 500:
        return 0.9
    if distance_m <= 1500:
        return 0.8
    if distance_m <= 3000:
        return 0.7
    return 0.5


def _spread_m(landmarks: List[Landmark]) -> float:
    """Distance max entre le premier lieu et les autres."""
    first = landmarks[0]
    return max((_distance_m((first.lat, first.lng), lm) for lm in landmarks[1:]), default=0.0)


def _slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    return slug or "landmark"


# ---------------------------------------------------------------------------
# Alias et index
# ---------------------------------------------------------------------------

def _usable_alias(alias: str) -> bool:
    """Un nom est-il assez précis pour désigner UN lieu ?"""
    if not alias or alias in _ALL_CATEGORY_WORDS or alias in _GENERIC_NAMES or alias in _COMMAND_WORDS:
        return False
    if alias.replace(" ", "").isdigit():
        return False
    tokens = alias.split()
    return len(tokens) > 1 or len(alias) >= 4


def _strip_category_word(name: str, category: str) -> Optional[str]:
    """"mosquee al fath" -> "al fath" (pour générer "jamaa al fath", "جامع الفتح"...)."""
    for word in CATEGORY_WORDS.get(category, []):
        if name.startswith(word + " "):
            core = name[len(word) + 1:].strip()
            return core if len(core) >= 3 else None
    return None


def build_aliases(names: Iterable[str], category: str) -> List[str]:
    """
    Tous les alias utilisables d'un lieu : noms normalisés, sans article arabe,
    et variantes avec les synonymes Darija de sa catégorie.
    "Mosquée Al Fath" -> "mosquee al fath", "jamaa al fath", "جامع al fath"...
    """
    aliases: Set[str] = set()
    for raw in names:
        norm = normalize_text(raw or "")
        if not norm:
            continue
        for variant in {norm, strip_arabic_article(norm)}:
            if _usable_alias(variant):
                aliases.add(variant)
        core = _strip_category_word(norm, category)
        if core:
            for word in CATEGORY_WORDS.get(category, []):
                aliases.add(f"{word} {core}")
    return sorted(aliases)


def _token_variants(token: str) -> List[str]:
    """Le mot tel quel + sans préfixe collé : "lgare" -> ["lgare", "gare"]."""
    variants = [token]
    for prefix in _PREFIXES_BY_LENGTH:
        if token.startswith(prefix) and len(token) - len(prefix) >= 2:
            variants.append(token[len(prefix):])
    return variants


class _PhraseIndex:
    """
    Index de phrases par premier mot, tolérant aux préfixes collés.
    Phrase index keyed by first token; much faster than one regex per alias
    once thousands of real places are loaded.
    """

    def __init__(self) -> None:
        self._by_first: Dict[str, List[Tuple[Tuple[str, ...], str, Any]]] = {}

    def add(self, phrase: str, payload: Any) -> None:
        tokens = tuple(phrase.split())
        if tokens:
            self._by_first.setdefault(tokens[0], []).append((tokens, phrase, payload))

    def find(self, tokens: List[Tuple[str, int, int]]) -> List[Tuple[int, int, str, Any]]:
        """Toutes les occurrences : (début, fin, phrase, payload)."""
        hits = []
        for i, (token, start, _) in enumerate(tokens):
            for variant in _token_variants(token):
                for phrase_tokens, phrase, payload in self._by_first.get(variant, ()):
                    n = len(phrase_tokens)
                    if i + n > len(tokens):
                        continue
                    if all(tokens[i + k][0] == phrase_tokens[k] for k in range(1, n)):
                        hits.append((start, tokens[i + n - 1][2], phrase, payload))
        return hits


# Repères curés par (ville, catégorie), pour écarter leurs doublons OSM.
_CURATED_BY_CATEGORY: Dict[Tuple[str, str], List[Landmark]] = {}
for _data in DEFAULT_LANDMARKS:
    _lm = Landmark(**_data)
    _CURATED_BY_CATEGORY.setdefault((_lm.city, _lm.category), []).append(_lm)

_CATEGORY_INDEX = _PhraseIndex()
for _category, _words in CATEGORY_WORDS.items():
    for _word in _words:
        _CATEGORY_INDEX.add(_word, _category)


def _tokenize(norm: str) -> List[Tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(norm)]


# ---------------------------------------------------------------------------
# Résolveur
# ---------------------------------------------------------------------------

class LandmarkResolver:
    """
    Résout des repères marocains en coordonnées GPS à partir de vrais lieux.

    Usage:
        resolver = LandmarkResolver(osm_dir="data/osm")
        resolver.resolve("derrière la mosquée", "casablanca", user_location=(33.59, -7.61))
        # -> la mosquée réelle la plus proche de (33.59, -7.61)

    Args:
        store_path: fichier JSON des repères crowdsourcés (None = en mémoire).
        osm_dir: dossier des fichiers data/osm/<ville>.json (None = pas d'OSM).
    """

    def __init__(self, store_path: Optional[str] = None, osm_dir: Optional[str] = None) -> None:
        self.store_path = store_path
        self.osm_dir = osm_dir
        self._lock = threading.RLock()
        self._landmarks: Dict[str, Landmark] = {}
        self._name_index: Dict[str, _PhraseIndex] = {city: _PhraseIndex() for city in CITIES}
        # {ville: {catégorie: {id: lieu}}} ; dict par id = ajout en O(1).
        self._by_category: Dict[str, Dict[str, Dict[str, Landmark]]] = {city: {} for city in CITIES}
        self.osm_meta: Dict[str, Dict[str, Any]] = {}
        self._city_patterns = [
            (city, compile_term(alias))
            for city, spec in CITIES.items()
            for alias in term_variants(spec["aliases"])
        ]

        for data in DEFAULT_LANDMARKS:
            self._register(Landmark(**data))
        self._load_osm()
        self._load_store()
        logger.info("LandmarkResolver ready: %s", self.stats())

    # ------------------------------------------------------------- chargement

    def _register(self, landmark: Landmark) -> None:
        """Indexe un lieu par ses noms et par sa catégorie (+ enseignes)."""
        self._landmarks[landmark.id] = landmark
        for alias in build_aliases([landmark.name] + landmark.aliases, landmark.category):
            self._name_index[landmark.city].add(alias, landmark.id)
        by_cat = self._by_category[landmark.city]
        for cat in [landmark.category] + [brand_category(b) for b in landmark.brands]:
            by_cat.setdefault(cat, {})[landmark.id] = landmark

    def _load_osm(self) -> None:
        """Charge data/osm/<ville>.json produit par scripts/import_osm.py."""
        if not self.osm_dir:
            return
        directory = Path(self.osm_dir)
        if not directory.is_dir():
            logger.warning("OSM data dir %s not found: run python scripts/import_osm.py", directory)
            return
        for city in CITIES:
            path = directory / f"{city}.json"
            if not path.exists():
                logger.warning("No OSM data for %s (%s)", city, path)
                continue
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                added = sum(self._add_osm_place(city, place) for place in payload.get("places", []))
                self.osm_meta[city] = {**payload.get("meta", {}), "loaded": added}
                logger.info("Loaded %d OSM places for %s", added, city)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                logger.error("Could not load OSM data %s: %s", path, exc)

    def _add_osm_place(self, city: str, place: Dict[str, Any]) -> bool:
        category = place["cat"]
        lat, lng = float(place["lat"]), float(place["lng"])
        # Doublon d'un grand repère curé (ex. Mosquée Hassan II) -> on garde le curé.
        for curated in _CURATED_BY_CATEGORY.get((city, category), []):
            if _distance_m((lat, lng), curated) < CURATED_DEDUPE_M:
                return False

        names = [place.get("name"), place.get("name_fr"), place.get("name_ar")] + place.get("alt", [])
        names = [n for n in names if n]
        display = place.get("name_fr") or place.get("name") or place.get("name_ar") \
            or category_label(category)
        brands = {
            _BRAND_LOOKUP[key] for key in
            (normalize_text(place.get("brand") or ""), normalize_text(place.get("name") or ""))
            if key in _BRAND_LOOKUP
        }
        self._register(Landmark(
            id=place["id"], name=display, city=city, lat=lat, lng=lng,
            aliases=names, category=category, source="osm", verified=True,
            brands=sorted(brands),
        ))
        return True

    def _load_store(self) -> None:
        """Charge les repères crowdsourcés / load crowdsourced landmarks."""
        if not self.store_path or not os.path.exists(self.store_path):
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as fh:
                records = json.load(fh)
            for data in records:
                self._register(Landmark(**data))
            logger.info("Loaded %d crowdsourced landmarks from %s", len(records), self.store_path)
        except (OSError, ValueError, TypeError) as exc:
            # Un fichier corrompu ne doit pas empêcher le démarrage.
            logger.error("Could not load landmark store %s: %s", self.store_path, exc)

    def _save_store(self) -> None:
        """Écriture atomique du fichier JSON / atomic write of the JSON store."""
        if not self.store_path:
            return
        records = [asdict(lm) for lm in self._landmarks.values() if lm.source == "crowdsourced"]
        directory = os.path.dirname(os.path.abspath(self.store_path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(records, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.store_path)
        except OSError:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    # ------------------------------------------------------------------ villes

    @staticmethod
    def normalize_city(city: Optional[str]) -> Optional[str]:
        """
        "Casa", "الدار البيضاء", "Fès" -> clé canonique ("casablanca", "fes").
        Returns None when the city is empty or unknown.
        """
        if not city:
            return None
        norm = normalize_text(city)
        for key, spec in CITIES.items():
            if norm == key or norm in {normalize_text(a) for a in spec["aliases"]}:
                return key
        return None

    def detect_city(self, text: str) -> Optional[str]:
        """Détecte une ville mentionnée dans le texte / detect a city named in text."""
        norm = normalize_text(text)
        for city, pattern in self._city_patterns:
            if pattern.search(norm):
                return city
        return None

    @staticmethod
    def city_from_location(location: Optional[LatLng]) -> Optional[str]:
        """Ville dont la zone contient la position / city whose bbox contains the point."""
        if not location:
            return None
        lat, lng = location
        for city, spec in CITIES.items():
            south, west, north, east = spec["bbox"]
            if south <= lat <= north and west <= lng <= east:
                return city
        return None

    @property
    def cities(self) -> List[str]:
        return list(CITIES.keys())

    def stats(self) -> Dict[str, Any]:
        """Nombre de lieux par source et par ville."""
        with self._lock:
            by_source: Dict[str, int] = {}
            by_city: Dict[str, int] = {}
            for lm in self._landmarks.values():
                by_source[lm.source] = by_source.get(lm.source, 0) + 1
                by_city[lm.city] = by_city.get(lm.city, 0) + 1
        return {"total": len(self._landmarks), "by_source": by_source, "by_city": by_city}

    def list_landmarks(self, city: Optional[str] = None, category: Optional[str] = None,
                       source: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        """Liste filtrée des lieux / filtered list of places."""
        key = self.normalize_city(city) if city else None
        with self._lock:
            rows = [
                asdict(lm) for lm in self._landmarks.values()
                if (key is None or lm.city == key)
                and (category is None or lm.category == category)
                and (source is None or lm.source == source)
            ]
        return rows[:limit]

    def __len__(self) -> int:
        return len(self._landmarks)

    # --------------------------------------------------------------- matching

    @staticmethod
    def _dedupe(matches: List[LandmarkMatch]) -> List[LandmarkMatch]:
        """
        Garde la mention la plus longue par zone de texte ; à longueur égale,
        un nom précis bat une catégorie ("jamaa hassan" > "jamaa").
        """
        ordered = sorted(
            matches,
            key=lambda m: (-(m.end - m.start), m.match_type != "name", m.start),
        )
        kept: List[LandmarkMatch] = []
        for cand in ordered:
            if all(cand.end <= k.start or cand.start >= k.end for k in kept):
                kept.append(cand)
        return sorted(kept, key=lambda m: m.start)

    @staticmethod
    def _annotate(norm: str, match: LandmarkMatch) -> None:
        """Détecte relation spatiale et rôle (départ/arrivée) juste avant le repère."""
        before = norm[: match.start].split()
        window = " ".join(before[-4:])
        for relation, pattern in _RELATION_PATTERNS:
            if pattern.search(window):
                match.relation = relation
                break
        # Remonte en sautant les articles : "mn l gare" -> "mn".
        for token in reversed(before[-3:]):
            if token in ORIGIN_MARKERS:
                match.role = "pickup"
                break
            if token not in _FILLERS:
                break

    def _name_matches(self, tokens, cities: List[str]) -> List[LandmarkMatch]:
        """Mentions par nom ; un même nom peut désigner plusieurs lieux."""
        grouped: Dict[Tuple[int, int, str], List[Landmark]] = {}
        with self._lock:
            for city in cities:
                for start, end, alias, lm_id in self._name_index[city].find(tokens):
                    lm = self._landmarks.get(lm_id)
                    group = grouped.setdefault((start, end, alias), [])
                    if lm and all(other.id != lm.id for other in group):
                        group.append(lm)
        return [
            LandmarkMatch(start, end, alias, "name", candidates=cands)
            for (start, end, alias), cands in grouped.items()
        ]

    @staticmethod
    def _category_matches(tokens) -> List[LandmarkMatch]:
        seen = set()
        matches = []
        for start, end, word, category in _CATEGORY_INDEX.find(tokens):
            if (start, end, category) not in seen:
                seen.add((start, end, category))
                matches.append(LandmarkMatch(start, end, word, "category", category=category))
        return matches

    def _pick_named(self, match: LandmarkMatch, user_location: Optional[LatLng]) -> None:
        """Choisit le lieu parmi les homonymes : le plus proche, sinon ambigu."""
        cands = match.candidates
        if user_location:
            best = min(cands, key=lambda lm: _distance_m(user_location, lm))
            match.landmark = best
            match.distance_m = _distance_m(user_location, best)
        else:
            match.landmark = max(cands, key=lambda lm: lm.base_confidence)
            match.ambiguous = len({lm.city for lm in cands}) > 1 or (
                len(cands) > 1 and _spread_m(cands) > SAME_PLACE_M
            )

    def _pick_category(self, match: LandmarkMatch, city: Optional[str],
                       user_location: Optional[LatLng]) -> None:
        """Le lieu de cette catégorie le plus proche ; sans position, pas de devinette."""
        if city is None:
            match.unresolved_reason = "city_required"
            return
        with self._lock:
            pool = list(self._by_category[city].get(match.category or "", {}).values())
        if not pool:
            match.unresolved_reason = "no_known_place_of_this_category"
            return
        if user_location:
            best = min(pool, key=lambda lm: _distance_m(user_location, lm))
            match.landmark = best
            match.distance_m = _distance_m(user_location, best)
            match.candidates = pool
            return
        defaults = [lm for lm in pool if match.category in lm.default_for]
        if defaults:
            match.landmark = defaults[0]
        else:
            match.unresolved_reason = "user_location_required"

    def find_matches(self, text: str, city: Optional[str] = None,
                     user_location: Optional[LatLng] = None) -> List[LandmarkMatch]:
        """
        Trouve tous les lieux désignés dans le texte, dans l'ordre d'apparition.
        Find every place referred to in the text, in order of appearance.

        Ville utilisée : `city`, sinon la ville citée dans le texte, sinon celle
        qui contient la position de l'utilisateur. Si aucun nom n'est trouvé
        dans cette ville, on cherche les noms dans les autres villes
        (confiance réduite).
        """
        norm = normalize_text(text)
        if not norm:
            return []
        tokens = _tokenize(norm)
        target = self.normalize_city(city) or self.detect_city(text) or self.city_from_location(user_location)

        category_hits = self._category_matches(tokens)
        if target:
            named = self._name_matches(tokens, [target])
            matches = self._dedupe(named + category_hits)
            if not any(m.match_type == "name" for m in matches) and not category_hits:
                others = [c for c in CITIES if c != target]
                fallback = self._dedupe(self._name_matches(tokens, others))
                for m in fallback:
                    m.city_fallback = True
                matches = fallback
        else:
            matches = self._dedupe(self._name_matches(tokens, list(CITIES)) + category_hits)

        for match in matches:
            if match.match_type == "name":
                self._pick_named(match, user_location)
            else:
                self._pick_category(match, target, user_location)
            self._annotate(norm, match)
        return matches

    def resolve_route(self, text: str, city: Optional[str] = None,
                      user_location: Optional[LatLng] = None) -> Dict[str, Any]:
        """
        Sépare départ et destination / split pickup vs destination.

        "mn lgare l jamaa" -> {"pickup": gare, "destination": mosquée, "unresolved": [...]}
        `unresolved` liste les lieux compris mais pas localisés (ex. "la mosquée"
        sans position) : l'app doit alors demander la position.
        """
        matches = self.find_matches(text, city, user_location)
        resolved = [m for m in matches if m.landmark is not None]
        pickups = [m for m in resolved if m.role == "pickup"]
        destinations = [m for m in resolved if m.role == "destination"]
        return {
            "pickup": pickups[0].to_dict() if pickups else None,
            "destination": destinations[-1].to_dict() if destinations else None,
            "unresolved": [m.to_dict() for m in matches if m.landmark is None],
        }

    def resolve(self, address_text: str, city: Optional[str] = None,
                user_location: Optional[LatLng] = None) -> Optional[Dict[str, Any]]:
        """
        Résout une adresse en coordonnées (le dernier repère = destination).

        Returns:
            {"lat", "lng", "place_name", "confidence", ...} ou None.
        """
        route = self.resolve_route(address_text, city, user_location)
        return route["destination"] or route["pickup"]

    # --------------------------------------------------------- crowdsourcing

    def add_landmark(
        self,
        name: str,
        city: str,
        lat: float,
        lng: float,
        aliases: Optional[List[str]] = None,
        category: str = "other",
    ) -> Dict[str, Any]:
        """
        Ajoute (ou vote pour) un repère proposé par un utilisateur.
        Add a user-submitted landmark, or upvote it if it already exists.

        Raises:
            ValueError: ville inconnue, coordonnées invalides ou hors ville.
        """
        city_key = self.normalize_city(city)
        if not city_key:
            raise ValueError(f"unknown city '{city}', supported: {', '.join(self.cities)}")
        name = (name or "").strip()
        if not 2 <= len(name) <= 100:
            raise ValueError("name must be 2-100 characters")
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            raise ValueError("lat/lng must be numbers") from None
        if not (MOROCCO_BBOX["lat_min"] <= lat <= MOROCCO_BBOX["lat_max"]
                and MOROCCO_BBOX["lng_min"] <= lng <= MOROCCO_BBOX["lng_max"]):
            raise ValueError("coordinates are outside Morocco")
        center = CITIES[city_key]["center"]
        if haversine_km(lat, lng, *center) > MAX_CITY_RADIUS_KM:
            raise ValueError(f"coordinates are more than {MAX_CITY_RADIUS_KM:.0f} km from {city_key}")
        if category != "other" and category not in CATEGORIES:
            raise ValueError(f"unknown category '{category}'")

        clean_aliases = [a.strip() for a in (aliases or []) if isinstance(a, str) and a.strip()][:20]
        lm_id = f"crowd_{city_key}_{_slugify(name)}"

        with self._lock:
            existing = self._landmarks.get(lm_id)
            if existing:
                existing.votes += 1
                existing.aliases = sorted(set(existing.aliases) | set(clean_aliases))
                self._register(existing)
                landmark = existing
                logger.info("Crowdsourced landmark upvoted: %s (votes=%d)", lm_id, existing.votes)
            else:
                landmark = Landmark(
                    id=lm_id, name=name, city=city_key, lat=lat, lng=lng,
                    aliases=clean_aliases, category=category or "other",
                    source="crowdsourced", verified=False, votes=1,
                )
                self._register(landmark)
                logger.info("Crowdsourced landmark added: %s", lm_id)
            self._save_store()
            return asdict(landmark)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    root = Path(__file__).resolve().parent.parent
    resolver = LandmarkResolver(osm_dir=str(root / "data" / "osm"))
    query = " ".join(sys.argv[1:]) or "derrière la mosquée"
    # Exemple : position près du boulevard Zerktouni à Casablanca.
    print(json.dumps(resolver.resolve_route(query, "casablanca", (33.5870, -7.6300)),
                     ensure_ascii=False, indent=2))
