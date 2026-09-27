"""
Parsing des commandes Darija -> intention structurée Yassir.
Darija command parsing -> structured Yassir intent.

Approche : dictionnaire de mots-clés pondérés (arabe, arabizi, français).
Chaque service accumule un score ; le meilleur gagne et l'écart avec le second
donne la confiance. Simple, explicable devant un jury, et extensible en
ajoutant des mots dans les dictionnaires ci-dessous.

Approach: weighted keyword dictionaries. Each service accumulates a score;
the best one wins and its margin over the runner-up drives the confidence.
"""

import logging
from typing import Any, Dict, List, Optional, Pattern, Tuple

try:  # Package import (python -m, pytest) / import en tant que package
    from .normalize import build_lookup, compile_term, detect_language, normalize_text, term_variants
except ImportError:  # pragma: no cover - direct script execution
    from normalize import build_lookup, compile_term, detect_language, normalize_text, term_variants

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dictionnaires Darija / Darija dictionaries
# ---------------------------------------------------------------------------

# Services supportés : (service_type, subtype) + mots-clés pondérés.
# Weight guide: 3 = explicit service word, 2 = strong hint, 1 = weak hint.
SERVICES: Dict[str, Dict[str, Any]] = {
    "taxi": {
        "service_type": "ride",
        "subtype": "taxi",
        "keywords": {
            3.0: ["taxi", "taksi", "petit taxi", "grand taxi", "تاكسي", "طاكسي", "درايفر",
                  "driver", "chauffeur", "vtc", "voiture", "tomobil", "طوموبيل", "طونوبيل"],
            2.0: ["bghit taxi", "waslni b taxi", "diini", "ddini", "ديني", "hezni", "هزني",
                  "nmchi", "نمشي", "bghit nmchi", "course", "trajet"],
            1.5: ["waslni", "wasalni", "وصلني", "وصلنا", "waslna"],
        },
    },
    "food": {
        "service_type": "delivery",
        "subtype": "food",
        "keywords": {
            3.0: ["makla", "mekla", "ماكلة", "أكل", "ماكله", "restaurant", "resto", "ريسطو",
                  "livraison repas", "commande food"],
            2.0: ["chri lia", "chri liya", "شري ليا", "hanout", "7anout", "حانوت", "marché",
                  "souk", "سوق", "courses", "طلبية", "tlabiya"],
            1.0: ["jib", "jibli", "jib lia", "jib liya", "جيب", "جيب ليا", "chri", "شري",
                  "kolchi", "koulchi", "كلشي", "commande", "faim", "ji3an", "جيعان"],
        },
    },
    "package": {
        "service_type": "delivery",
        "subtype": "package",
        "keywords": {
            3.0: ["package", "colis", "koli", "كولي", "طرد", "parcel", "enveloppe", "ظرف",
                  "sift", "sifet", "صيفط", "siftli", "صيفط ليا", "sayfet"],
            2.0: ["documents", "wra9", "wraq", "وراق", "clé", "sarout", "ساروت", "sac", "chkara",
                  "شكارة", "carton", "كرطونة", "kartona"],
            1.0: ["7aja", "haja", "حاجة", "hwayej", "حوايج", "livrer", "livraison"],
            0.5: ["waslni", "wasalni", "وصلني", "jib", "جيب"],
        },
    },
}

# Articles alimentaires -> nom canonique (anglais, comme l'API Yassir).
# Food items -> canonical English name.
FOOD_ITEMS: Dict[str, List[str]] = {
    "bread": ["khobz", "khubz", "5obz", "kh0bz", "خبز", "pain", "baguette", "بكيط"],
    "milk": ["7lib", "hlib", "حليب", "lait"],
    "tea": ["atay", "atai", "ataye", "أتاي", "اتاي", "thé", "the"],
    "sugar": ["sokar", "sukkar", "sekkar", "سكر", "sucre"],
    "eggs": ["bid", "bayd", "بيض", "oeufs", "oeuf", "œufs"],
    "water": ["lma", "الما", "eau", "sidi ali", "ain saiss"],
    "oil": ["zit", "زيت", "huile"],
    "cheese": ["fromage", "formaj", "فرماج", "فورماج", "جبن"],
    "coffee": ["9ahwa", "qahwa", "kahwa", "قهوة", "café", "cafe"],
    "yogurt": ["danone", "danon", "دانون", "yaourt", "raib", "رايب"],
    "chicken": ["djaj", "djej", "dajaj", "دجاج", "poulet"],
    "meat": ["l7em", "lhem", "لحم", "viande"],
    "vegetables": ["khodra", "5odra", "خضرة", "خضره", "légumes", "legumes"],
    "fruits": ["fakia", "fakya", "فاكية", "fruits"],
    "pizza": ["pizza", "بيتزا", "بيدزا"],
    "tacos": ["tacos", "طاكوس"],
    "burger": ["burger", "hamburger", "برݣر", "بركر"],
    "msemen": ["msemen", "msmen", "مسمن", "rghaif", "رغايف"],
    "harira": ["harira", "7rira", "حريرة", "حريره"],
    "soda": ["coca", "كوكا", "pepsi", "fanta", "hawai"],
    "juice": ["3asir", "asir", "عصير", "jus"],
}

# Nombres en Darija pour les quantités / Darija numbers for quantities.
NUMBERS: Dict[str, int] = {
    "wa7d": 1, "wahed": 1, "wa7ed": 1, "wahd": 1, "واحد": 1, "un": 1, "une": 1,
    "joj": 2, "jouj": 2, "zouj": 2, "جوج": 2, "زوج": 2, "deux": 2,
    "tlata": 3, "tlate": 3, "تلاتة": 3, "تلاته": 3, "trois": 3,
    "rb3a": 4, "reb3a": 4, "ربعة": 4, "ربعه": 4, "quatre": 4,
    "khamsa": 5, "5amsa": 5, "خمسة": 5, "خمسه": 5, "cinq": 5,
    "setta": 6, "ستة": 6, "sb3a": 7, "seb3a": 7, "سبعة": 7,
    "tmnya": 8, "tmenya": 8, "تمنية": 8, "ts3ood": 9, "tes3ood": 9, "تسعود": 9,
    "3achra": 10, "عشرة": 10, "dix": 10,
}

# Niveaux d'urgence / urgency levels (checked most urgent first).
URGENCY: Dict[str, List[str]] = {
    "urgent": ["fasa", "fissa", "fisa", "فيسع", "فيسا", "daghya", "dghya", "degya", "دغيا",
               "zerba", "zarba", "b zzerba", "زربة", "urgent", "urgence", "mosta3jel", "مستعجل",
               "dlak", "دلاك", "daba", "دابا", "db", "tout de suite", "maintenant", "immédiatement"],
    "quick": ["basr", "bsr3a", "b sor3a", "bser3a", "بسرعة", "بالزربة", "vite", "rapide",
              "rapidement", "sri3", "سريع"],
}

# Service inconnu / fallback when nothing matched.
UNKNOWN_SERVICE = {"service_type": "unknown", "subtype": None}

MAX_TEXT_LENGTH = 500

# ---------------------------------------------------------------------------
# Compilation des regex (une seule fois au chargement du module)
# Pre-compiled patterns (built once at import time)
# ---------------------------------------------------------------------------

_SERVICE_PATTERNS: Dict[str, List[Tuple[float, str, Pattern[str]]]] = {
    name: [
        (weight, term, compile_term(term))
        for weight, words in spec["keywords"].items()
        for term in term_variants(words)
    ]
    for name, spec in SERVICES.items()
}
_FOOD_PATTERNS = build_lookup(FOOD_ITEMS)
_URGENCY_PATTERNS = build_lookup(URGENCY)
_NUMBERS_NORM = {normalize_text(k): v for k, v in NUMBERS.items()}


def _score_services(norm: str) -> Tuple[Dict[str, float], Dict[str, List[str]]]:
    """
    Calcule le score de chaque service.
    Score every service; a keyword counts once, and a phrase that contains a
    shorter keyword ("bghit taxi" ⊃ "taxi") only counts the phrase weight
    once per matched span.
    """
    scores: Dict[str, float] = {}
    matched: Dict[str, List[str]] = {}
    for name, patterns in _SERVICE_PATTERNS.items():
        taken: List[Tuple[int, int]] = []
        score = 0.0
        hits: List[str] = []
        # Highest weight first, then longest term (patterns are length-sorted per weight).
        for weight, term, pattern in sorted(patterns, key=lambda p: (-p[0], -len(p[1]))):
            for m in pattern.finditer(norm):
                if any(m.start() < end and start < m.end() for start, end in taken):
                    continue
                taken.append((m.start(), m.end()))
                score += weight
                hits.append(term)
                break  # count each keyword once
        scores[name] = score
        matched[name] = hits
    return scores, matched


def _extract_items(norm: str) -> List[Dict[str, Any]]:
    """
    Extrait les articles alimentaires et leur quantité.
    Extract food items with an optional preceding quantity ("joj khobz", "2 7lib").
    Items are returned in the order they appear in the text.
    """
    found: List[Tuple[int, Dict[str, Any]]] = []
    for item, patterns in _FOOD_PATTERNS.items():
        for pattern in patterns:
            m = pattern.search(norm)
            if not m:
                continue
            quantity = 1
            before = norm[: m.start()].split()
            if before:
                prev = before[-1]
                if prev.isdigit() and 0 < int(prev) <= 100:
                    quantity = int(prev)
                elif prev in _NUMBERS_NORM:
                    quantity = _NUMBERS_NORM[prev]
            found.append((m.start(), {"name": item, "quantity": quantity, "raw": m.group(0)}))
            break
    found.sort(key=lambda pair: pair[0])
    return [details for _, details in found]


def _detect_urgency(norm: str) -> Tuple[str, Optional[str]]:
    """Retourne (niveau, mot détecté) / returns (level, matched word)."""
    for level in ("urgent", "quick"):
        for pattern in _URGENCY_PATTERNS[level]:
            m = pattern.search(norm)
            if m:
                return level, m.group(0)
    return "normal", None


def _confidence(best: float, second: float) -> float:
    """
    Confiance = force du signal x marge sur le second service.
    Confidence combines signal strength (3 points = one explicit service word)
    and the margin over the runner-up. Range: 0.5 .. 0.99 when a service wins.
    """
    if best <= 0:
        return 0.0
    strength = min(1.0, best / 3.0)
    margin = 1.0 - second / (2.0 * best)
    return round(min(0.99, 0.5 + 0.45 * strength * margin), 2)


def parse_darija_command(text: str) -> Dict[str, Any]:
    """
    Analyse une commande Darija et retourne l'intention structurée.
    Parse a Darija (Arabic script, arabizi or code-switched) command.

    Args:
        text: la commande, ex. "بغيت تاكسي للقارة" / "jib lia khobz o 7lib".

    Returns:
        {
          "service_type": "ride" | "delivery" | "unknown",
          "subtype": "taxi" | "food" | "package" | None,
          "urgency": "normal" | "quick" | "urgent",
          "confidence": float 0..1,
          "items": ["bread", "milk"],          # food only
          "item_details": [{"name", "quantity", "raw"}],
          "matched_keywords": [...],
          "language": "darija" | "darija_latin" | "french" | "mixed" | "unknown",
          "normalized_text": str,
        }

    Raises:
        TypeError: si text n'est pas une chaîne / if text is not a string.
        ValueError: si text est vide ou trop long / if empty or too long.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not text.strip():
        raise ValueError("text is empty")
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"text exceeds {MAX_TEXT_LENGTH} characters")

    norm = normalize_text(text)
    scores, matched = _score_services(norm)
    items = _extract_items(norm)

    # Chaque article alimentaire renforce le service "food".
    # Each recognized food item is a strong food signal.
    if items:
        scores["food"] += 2.0 * len(items)
        matched["food"].extend(i["raw"] for i in items)

    ranking = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_name, best_score = ranking[0]
    second_score = ranking[1][1] if len(ranking) > 1 else 0.0

    if best_score <= 0:
        service = dict(UNKNOWN_SERVICE)
        keywords: List[str] = []
    else:
        service = {"service_type": SERVICES[best_name]["service_type"], "subtype": SERVICES[best_name]["subtype"]}
        keywords = matched[best_name]

    urgency, urgency_word = _detect_urgency(norm)
    if urgency_word:
        keywords = keywords + [urgency_word]

    result = {
        **service,
        "urgency": urgency,
        "confidence": _confidence(best_score, second_score),
        "items": [i["name"] for i in items] if service["subtype"] == "food" else [],
        "item_details": items if service["subtype"] == "food" else [],
        "matched_keywords": keywords,
        "language": detect_language(text),
        "normalized_text": norm,
    }
    logger.debug("Parsed %r -> %s (scores=%s)", text, result["subtype"], scores)
    return result


if __name__ == "__main__":  # Petit test manuel / quick manual check
    import json
    import sys

    logging.basicConfig(level=logging.INFO)
    samples = sys.argv[1:] or ["بغيت تاكسي للقارة", "jib lia khobz o 7lib", "waslni package ldjamaa fasa"]
    for sample in samples:
        print(sample, "->", json.dumps(parse_darija_command(sample), ensure_ascii=False, indent=2))
