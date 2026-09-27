"""
Importe les vrais lieux d'OpenStreetMap pour chaque ville supportée.
Import real places from OpenStreetMap (Overpass API) for each supported city.

Usage:
    python scripts/import_osm.py                 # toutes les villes
    python scripts/import_osm.py casablanca fes  # certaines villes

Écrit data/osm/<ville>.json. Données © contributeurs OpenStreetMap, licence ODbL 1.0.
Writes data/osm/<city>.json. Data © OpenStreetMap contributors, ODbL 1.0.
"""

import json
import logging
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.categories import CATEGORIES  # noqa: E402
from src.landmarks import CITIES  # noqa: E402
from src.normalize import normalize_text  # noqa: E402

logger = logging.getLogger("import_osm")

OUT_DIR = ROOT_DIR / "data" / "osm"

# Serveurs Overpass publics, essayés dans l'ordre (le principal est souvent saturé).
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
# Overpass refuse les requêtes anonymes : on s'identifie.
USER_AGENT = "wassal-landmarks/0.1 (+https://github.com/oussamabartil/wassal)"
TIMEOUT_S = 240

_FILTER_RE = re.compile(r'\["([^"]+)"(?:(=|!=)"([^"]*)")?\]')


def _parse_filter(osm_filter: str) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """'["amenity"="bank"]["name"]' -> [("amenity", "=", "bank"), ("name", None, None)]"""
    return [(k, op or None, v if op else None) for k, op, v in _FILTER_RE.findall(osm_filter)]


def _matches(tags: Dict[str, str], osm_filter: str) -> bool:
    """Reproduit en Python le filtre Overpass, pour classer chaque élément."""
    for key, op, value in _parse_filter(osm_filter):
        if op is None and key not in tags:
            return False
        if op == "=" and tags.get(key) != value:
            return False
        if op == "!=" and tags.get(key) == value:
            return False
    return True


def _category_filters() -> Iterable[Tuple[str, str]]:
    """(catégorie, filtre effectif) ; les catégories `named_only` exigent un nom."""
    for category, spec in CATEGORIES.items():
        for osm_filter in spec["osm"]:
            if spec.get("named_only") and '["name"]' not in osm_filter:
                osm_filter += '["name"]'
            yield category, osm_filter


def build_query(bbox: Tuple[float, float, float, float]) -> str:
    """Une seule requête par ville, avec tous les filtres (moins de charge sur Overpass)."""
    box = ",".join(str(c) for c in bbox)
    lines = [f"  nwr{f}({box});" for _, f in _category_filters()]
    return f"[out:json][timeout:{TIMEOUT_S}];\n(\n" + "\n".join(lines) + "\n);\nout center tags;"


def fetch(query: str) -> Tuple[Dict[str, Any], str]:
    """Interroge les serveurs Overpass jusqu'à obtenir du JSON valide."""
    body = urllib.parse.urlencode({"data": query}).encode()
    errors = []
    for endpoint in ENDPOINTS:
        for attempt in (1, 2):
            req = urllib.request.Request(endpoint, data=body, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT_S + 30) as resp:
                    raw = resp.read()
                data = json.loads(raw)
                if "elements" not in data:
                    raise ValueError(data.get("remark") or "no elements in response")
                if data.get("remark"):  # ex. timeout partiel côté serveur
                    raise ValueError(data["remark"])
                return data, endpoint
            except (urllib.error.URLError, ValueError, TimeoutError, OSError) as exc:
                msg = f"{endpoint} (try {attempt}): {str(exc)[:120]}"
                logger.warning(msg)
                errors.append(msg)
                time.sleep(5 * attempt)
    raise RuntimeError("all Overpass endpoints failed:\n" + "\n".join(errors))


def _element_to_place(element: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tags = element.get("tags") or {}
    category = next((c for c, f in _category_filters() if _matches(tags, f)), None)
    if category is None:
        return None
    if element["type"] == "node":
        lat, lng = element.get("lat"), element.get("lon")
    else:
        center = element.get("center") or {}
        lat, lng = center.get("lat"), center.get("lon")
    if lat is None or lng is None:
        return None

    alt = []
    for key in ("alt_name", "official_name", "short_name", "name:en", "alt_name:ar", "alt_name:fr"):
        for value in (tags.get(key) or "").split(";"):
            if value.strip():
                alt.append(value.strip())

    place = {
        "id": f"osm:{element['type'][0]}{element['id']}",
        "cat": category,
        "name": tags.get("name"),
        "name_ar": tags.get("name:ar"),
        "name_fr": tags.get("name:fr"),
        "alt": sorted(set(alt)),
        "brand": tags.get("brand"),
        "lat": round(lat, 6),
        "lng": round(lng, 6),
    }
    return {k: v for k, v in place.items() if v not in (None, [], "")}


def _dedupe(places: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Un même lieu est souvent dessiné deux fois (point + bâtiment).
    Same category + same name within ~30 m -> keep one.
    """
    seen = set()
    kept = []
    for place in places:
        key = (place["cat"], normalize_text(place.get("name", "")),
               round(place["lat"] / 0.0003), round(place["lng"] / 0.0003))
        if key in seen:
            continue
        seen.add(key)
        kept.append(place)
    return kept


def import_city(city: str) -> Path:
    spec = CITIES[city]
    logger.info("Fetching %s (bbox %s)...", city, spec["bbox"])
    started = time.perf_counter()
    data, endpoint = fetch(build_query(spec["bbox"]))
    places = [p for p in (_element_to_place(e) for e in data["elements"]) if p]
    places = _dedupe(places)
    places.sort(key=lambda p: (p["cat"], p.get("name", ""), p["id"]))

    counts: Dict[str, int] = {}
    for place in places:
        counts[place["cat"]] = counts.get(place["cat"], 0) + 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{city}.json"
    payload = {
        "meta": {
            "city": city,
            "source": "OpenStreetMap via Overpass API",
            "license": "ODbL 1.0 - © OpenStreetMap contributors (https://www.openstreetmap.org/copyright)",
            "osm_timestamp": data.get("osm3s", {}).get("timestamp_osm_base"),
            "endpoint": endpoint,
            "bbox": spec["bbox"],
            "count": len(places),
            "by_category": dict(sorted(counts.items())),
        },
        "places": places,
    }
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    logger.info("%s: %d places in %.0fs -> %s", city, len(places), time.perf_counter() - started, out)
    logger.info("  %s", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return out


def main(argv: List[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cities = argv or list(CITIES)
    unknown = [c for c in cities if c not in CITIES]
    if unknown:
        logger.error("unknown cities: %s (supported: %s)", unknown, list(CITIES))
        return 2
    failed = []
    for city in cities:
        try:
            import_city(city)
        except RuntimeError as exc:
            logger.error("%s failed: %s", city, exc)
            failed.append(city)
        time.sleep(3)  # politesse envers le serveur public
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
