"""
Résolveur d'adresses marocaines par points de repère.
Moroccan landmark-based address resolver.

Au Maroc, on ne donne pas "12 rue X" mais "derrière la mosquée" ou "حدا القارة".
Ce module retrouve ces repères dans le texte (arabe, arabizi, français) et
les convertit en coordonnées GPS. La base est extensible par crowdsourcing :
les repères ajoutés par les utilisateurs sont persistés dans un fichier JSON
et reçoivent une confiance plus faible tant qu'ils ne sont pas vérifiés.

People describe places by landmarks, not street addresses. This module finds
landmarks in free text and turns them into coordinates. User-submitted
landmarks are persisted to JSON and trusted less until verified.
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
from typing import Any, Dict, List, Optional, Pattern, Tuple

try:
    from .normalize import compile_term, normalize_text, term_variants
except ImportError:  # pragma: no cover - direct script execution
    from normalize import compile_term, normalize_text, term_variants

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Villes supportées / supported cities
# ---------------------------------------------------------------------------

CITIES: Dict[str, Dict[str, Any]] = {
    "casablanca": {
        "label": "Casablanca",
        "center": (33.5731, -7.5898),
        "aliases": ["casablanca", "casa", "dar lbida", "dar el beida", "الدار البيضاء", "كازا", "كازابلانكا"],
    },
    "fes": {
        "label": "Fès",
        "center": (34.0331, -5.0003),
        "aliases": ["fes", "fès", "fez", "fas", "فاس"],
    },
    "marrakech": {
        "label": "Marrakech",
        "center": (31.6295, -7.9811),
        "aliases": ["marrakech", "marrakesh", "mraksh", "mrakech", "kech", "مراكش"],
    },
}

# Rayon max autour du centre-ville pour un repère crowdsourcé (km).
MAX_CITY_RADIUS_KM = 40.0

# Boîte englobante du Maroc / Morocco bounding box (incl. southern provinces).
MOROCCO_BBOX = {"lat_min": 20.7, "lat_max": 36.0, "lng_min": -17.2, "lng_max": -0.9}

# ---------------------------------------------------------------------------
# Base de repères curés / curated landmarks
# Coordonnées vérifiées sur OpenStreetMap. Coordinates checked against OSM.
# Les alias génériques ("gare", "jamaa") sont résolus dans la ville demandée.
# ---------------------------------------------------------------------------

DEFAULT_LANDMARKS: List[Dict[str, Any]] = [
    # --- Casablanca ---
    {
        "id": "casa_gare_voyageurs", "city": "casablanca", "category": "train_station",
        "name": "Gare Casa-Voyageurs", "lat": 33.5894, "lng": -7.5906,
        "aliases": ["gare casa voyageurs", "casa voyageurs", "gare", "la gare", "lagar", "lagare",
                    "laggar", "لاكار", "لاگار", "القارة", "قارة", "المحطة", "محطة القطار", "lmahatta"],
    },
    {
        "id": "casa_mosquee_hassan2", "city": "casablanca", "category": "mosque",
        "name": "Mosquée Hassan II", "lat": 33.6083, "lng": -7.6328,
        "aliases": ["mosquee hassan 2", "mosquee hassan ii", "hassan 2", "hassan ii", "jamaa hassan",
                    "jam3 hassan", "جامع الحسن الثاني", "مسجد الحسن الثاني", "mosquee", "la mosquee",
                    "jamaa", "djamaa", "jama3", "jam3", "جامع", "الجامع", "مسجد"],
    },
    {
        "id": "casa_aeroport", "city": "casablanca", "category": "airport",
        "name": "Aéroport Mohammed V", "lat": 33.3675, "lng": -7.5898,
        "aliases": ["aeroport mohammed v", "aeroport mohammed 5", "aeroport", "l aeroport", "airport",
                    "laeroport", "matar", "lmatar", "المطار", "مطار محمد الخامس", "nouasser", "النواصر"],
    },
    # --- Fès ---
    {
        "id": "fes_medina", "city": "fes", "category": "medina",
        "name": "Médina Fès (Fès el-Bali)", "lat": 34.0648, "lng": -4.9730,
        "aliases": ["medina fes", "fes el bali", "fas lbali", "medina", "lmdina", "lmedina", "المدينة",
                    "المدينة القديمة", "فاس البالي", "bab boujloud", "bab bujlud", "باب بوجلود",
                    "quaraouiyine", "qarawiyyin", "jamaa l9arawiyin", "القرويين",
                    "jamaa", "djamaa", "jama3", "jam3", "جامع", "الجامع", "mosquee", "la mosquee"],
    },
    {
        "id": "fes_gare", "city": "fes", "category": "train_station",
        "name": "Gare de Fès", "lat": 34.0462, "lng": -4.9914,
        "aliases": ["gare fes", "gare de fes", "gare", "la gare", "lagar", "lagare", "لاكار", "لاگار",
                    "القارة", "قارة", "المحطة", "lmahatta"],
    },
    # --- Marrakech ---
    {
        "id": "rak_jemaa_el_fna", "city": "marrakech", "category": "square",
        "name": "Place Jemaa El Fna", "lat": 31.6258, "lng": -7.9892,
        "aliases": ["jemaa el fna", "jamaa el fna", "jemaa lfna", "jamaa lfna", "jama3 lfna",
                    "djemaa el fna", "جامع الفنا", "جامع لفنا", "sa7a", "saha", "الساحة", "la place"],
    },
    {
        "id": "rak_koutoubia", "city": "marrakech", "category": "mosque",
        "name": "Mosquée Koutoubia", "lat": 31.6237, "lng": -7.9937,
        "aliases": ["koutoubia", "kotobia", "lkoutoubia", "الكتبية", "كتبية",
                    "jamaa", "djamaa", "jama3", "jam3", "جامع", "الجامع", "mosquee", "la mosquee"],
    },
    {
        "id": "rak_gare", "city": "marrakech", "category": "train_station",
        "name": "Gare de Marrakech", "lat": 31.6309, "lng": -8.0158,
        "aliases": ["gare marrakech", "gare de marrakech", "gare", "la gare", "lagar", "lagare",
                    "لاكار", "لاگار", "القارة", "قارة", "المحطة", "lmahatta"],
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

_RELATION_PATTERNS: List[Tuple[str, Pattern[str]]] = [
    (rel, re.compile(rf"(?<!\S){re.escape(v)}(?!\S)"))
    for rel, words in RELATIONS.items()
    for v in term_variants(words)
]


@dataclass
class Landmark:
    """Un point de repère / a landmark."""

    id: str
    name: str
    city: str
    lat: float
    lng: float
    aliases: List[str] = field(default_factory=list)
    category: str = "other"
    source: str = "curated"  # "curated" | "crowdsourced"
    verified: bool = True
    votes: int = 0

    @property
    def base_confidence(self) -> float:
        """Confiance de base : 0.95 si vérifié, 0.6 -> 0.8 selon les votes sinon."""
        if self.verified:
            return 0.95
        return min(0.8, 0.6 + 0.05 * self.votes)


@dataclass
class LandmarkMatch:
    """Un repère trouvé dans le texte / a landmark found in text."""

    landmark: Landmark
    alias: str
    start: int
    end: int
    relation: Optional[str] = None
    role: str = "destination"  # "destination" | "pickup"
    ambiguous: bool = False
    city_fallback: bool = False

    @property
    def confidence(self) -> float:
        conf = self.landmark.base_confidence
        if self.relation:  # "derrière X" est approximatif / approximate position
            conf *= 0.95
        if self.city_fallback:  # trouvé hors de la ville demandée
            conf *= 0.7
        if self.ambiguous:  # plusieurs villes possibles
            conf *= 0.6
        return round(conf, 2)

    def to_dict(self) -> Dict[str, Any]:
        lm = self.landmark
        return {
            "lat": lm.lat,
            "lng": lm.lng,
            "place_name": lm.name,
            "place": lm.name,  # alias court, format de l'exemple Yassir
            "landmark_id": lm.id,
            "city": lm.city,
            "category": lm.category,
            "relation": self.relation,
            "matched_text": self.alias,
            "source": lm.source,
            "confidence": self.confidence,
        }


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance à vol d'oiseau en km / great-circle distance in km."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    return slug or "landmark"


class LandmarkResolver:
    """
    Résout des repères marocains en coordonnées GPS.
    Resolves Moroccan landmarks to GPS coordinates.

    Usage:
        resolver = LandmarkResolver()
        resolver.resolve("derrière la mosquée", "casablanca")
        # {"lat": 33.6083, "lng": -7.6328, "place_name": "Mosquée Hassan II", ...}

    Args:
        store_path: fichier JSON des repères crowdsourcés (optionnel).
                    JSON file for crowdsourced landmarks; None = in-memory only.
    """

    def __init__(self, store_path: Optional[str] = None) -> None:
        self.store_path = store_path
        self._lock = threading.RLock()
        self._landmarks: Dict[str, Landmark] = {}
        self._patterns: Dict[str, List[Tuple[str, Pattern[str]]]] = {}
        self._city_patterns: List[Tuple[str, Pattern[str]]] = [
            (city, compile_term(alias))
            for city, spec in CITIES.items()
            for alias in term_variants(spec["aliases"])
        ]

        for data in DEFAULT_LANDMARKS:
            self._register(Landmark(**data))
        self._load_store()
        logger.info("LandmarkResolver ready: %d landmarks", len(self._landmarks))

    # ------------------------------------------------------------------ utils

    def _register(self, landmark: Landmark) -> None:
        """Ajoute un repère et compile ses alias / add landmark + compile aliases."""
        variants = term_variants([landmark.name] + landmark.aliases)
        self._landmarks[landmark.id] = landmark
        self._patterns[landmark.id] = [(v, compile_term(v)) for v in variants]

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

    @property
    def cities(self) -> List[str]:
        return list(CITIES.keys())

    def list_landmarks(self, city: Optional[str] = None) -> List[Dict[str, Any]]:
        """Liste les repères (filtrés par ville) / list landmarks, optionally by city."""
        key = self.normalize_city(city) if city else None
        with self._lock:
            return [asdict(lm) for lm in self._landmarks.values() if key is None or lm.city == key]

    def __len__(self) -> int:
        return len(self._landmarks)

    # --------------------------------------------------------------- matching

    def _scan(self, norm: str, cities: List[str]) -> List[LandmarkMatch]:
        """Cherche tous les repères des villes données / find every alias occurrence."""
        candidates: List[LandmarkMatch] = []
        with self._lock:
            for lm_id, patterns in self._patterns.items():
                landmark = self._landmarks[lm_id]
                if landmark.city not in cities:
                    continue
                for alias, pattern in patterns:
                    for m in pattern.finditer(norm):
                        candidates.append(LandmarkMatch(landmark, alias, m.start(), m.end()))
        return candidates

    @staticmethod
    def _dedupe(candidates: List[LandmarkMatch]) -> List[LandmarkMatch]:
        """
        Garde le match le plus long par zone de texte.
        Keep the longest match per text span ("jemaa el fna" beats "jamaa");
        verified landmarks win ties.
        """
        ordered = sorted(
            candidates,
            key=lambda c: (-(c.end - c.start), -c.landmark.base_confidence, c.start),
        )
        kept: List[LandmarkMatch] = []
        for cand in ordered:
            if all(cand.end <= k.start or cand.start >= k.end for k in kept):
                kept.append(cand)
        return sorted(kept, key=lambda c: c.start)

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

    def find_matches(self, text: str, city: Optional[str] = None) -> List[LandmarkMatch]:
        """
        Trouve tous les repères du texte, dans l'ordre d'apparition.
        Find all landmarks in text, in order of appearance.

        Stratégie / strategy:
          1. ville demandée (ou mentionnée dans le texte) ;
          2. sinon toutes les villes, avec une confiance réduite.
        """
        norm = normalize_text(text)
        if not norm:
            return []

        target = self.normalize_city(city) or self.detect_city(text)
        matches: List[LandmarkMatch] = []
        if target:
            matches = self._dedupe(self._scan(norm, [target]))

        if not matches:
            # Fallback : toutes les villes. Un alias générique ("gare") présent
            # dans plusieurs villes est marqué ambigu.
            candidates = self._scan(norm, self.cities)
            matches = self._dedupe(candidates)
            for match in matches:
                cities_here = {
                    c.landmark.city for c in candidates if c.start < match.end and match.start < c.end
                }
                match.ambiguous = len(cities_here) > 1
                match.city_fallback = target is not None

        for match in matches:
            self._annotate(norm, match)
        return matches

    def resolve(self, address_text: str, city: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Résout une adresse en coordonnées (le dernier repère = destination).
        Resolve an address to coordinates. With several landmarks, the last
        non-pickup one wins ("mn lgare l jamaa" -> jamaa).

        Returns:
            {"lat", "lng", "place_name", "confidence", ...} ou None si rien trouvé.
        """
        route = self.resolve_route(address_text, city)
        return route["destination"] or route["pickup"]

    def resolve_route(self, text: str, city: Optional[str] = None) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        Sépare départ et destination / split pickup vs destination.

        "mn lgare l jamaa" -> {"pickup": Gare, "destination": Mosquée}
        """
        matches = self.find_matches(text, city)
        pickups = [m for m in matches if m.role == "pickup"]
        destinations = [m for m in matches if m.role == "destination"]
        return {
            "pickup": pickups[0].to_dict() if pickups else None,
            "destination": destinations[-1].to_dict() if destinations else None,
        }

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

        Les repères crowdsourcés ne sont pas vérifiés : confiance 0.6, +0.05
        par vote (max 0.8). Crowdsourced landmarks start at 0.6 confidence.

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

        clean_aliases = [a.strip() for a in (aliases or []) if isinstance(a, str) and a.strip()][:20]
        lm_id = f"crowd_{city_key}_{_slugify(name)}"

        with self._lock:
            existing = self._landmarks.get(lm_id)
            if existing:
                if existing.source == "curated":
                    raise ValueError("landmark already exists in the curated database")
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
    resolver = LandmarkResolver()
    query = " ".join(sys.argv[1:]) or "derrière la mosquée"
    print(json.dumps(resolver.resolve(query, "casablanca"), ensure_ascii=False, indent=2))
