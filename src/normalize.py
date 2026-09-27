"""
Normalisation de texte Darija (arabe, arabizi, français).
Darija text normalization shared by the parser and the landmark resolver.

La Darija s'écrit en alphabet arabe ("بغيت تاكسي"), en arabizi ("bghit taxi",
avec 3/7/9 pour ع/ح/ق) et se mélange souvent au français ("la gare").
Tout le matching de mots-clés passe par `normalize_text` pour que ces
variantes se ressemblent.
"""

import re
import unicodedata
from functools import lru_cache
from typing import Dict, Iterable, List, Pattern

# Variantes de lettres arabes ramenées à une forme canonique.
# Letter variants folded to one canonical form (hamza forms, ta marbuta,
# Moroccan gaf "گ" often typed as "ك" or "ق").
_ARABIC_FOLD = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ئ": "ي",
        "ؤ": "و",
        "ة": "ه",
        "گ": "ك",
        "ڭ": "ك",
        "ڤ": "ف",
        "ـ": "",  # tatweel
    }
)

# Chiffres arabes-indiens -> ASCII / Arabic-Indic digits -> ASCII.
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

_NON_WORD = re.compile(r"[^\w\s]", flags=re.UNICODE)
_SPACES = re.compile(r"\s+")

# Préfixes collés au mot en Darija : "l-gare", "lgare", "للقارة", "فالجامع"...
# Clitic prefixes glued to words (to / the / in / with).
PREFIXES: List[str] = [
    "lel", "lil", "ll", "l", "el", "al", "li", "fl", "f", "b",
    "لل", "ل", "ال", "بال", "فال", "وال", "ب", "ف", "و",
]


@lru_cache(maxsize=65536)  # beaucoup de noms de lieux se répètent ("Pharmacie", "BIM"...)
def normalize_text(text: str) -> str:
    """
    Normalise un texte Darija pour le matching.
    Normalize Darija text: lowercase, strip accents/harakat, fold Arabic letter
    variants, convert digits, replace punctuation with spaces.

    >>> normalize_text("Derrière la Mosquée !")
    'derriere la mosquee'
    """
    if not text:
        return ""
    # NFKD sépare les accents latins et les hamzas ; on supprime les marques (Mn).
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    # Recompose so remaining Arabic letters are in canonical form.
    result = unicodedata.normalize("NFKC", stripped).lower()
    result = result.translate(_ARABIC_FOLD).translate(_DIGITS)
    result = _NON_WORD.sub(" ", result).replace("_", " ")
    return _SPACES.sub(" ", result).strip()


def strip_arabic_article(term: str) -> str:
    """Retire l'article "ال" : "القارة" -> "قارة" (so "للقارة" can match)."""
    if term.startswith("ال") and len(term) > 3:
        return term[2:]
    return term


def compile_term(term: str, allow_prefix: bool = True) -> Pattern[str]:
    """
    Compile un mot-clé en regex tolérante aux préfixes Darija.
    Compile a keyword into a regex matching it as whole word(s), optionally
    preceded by a glued clitic ("ltaxi", "للقارة", "fljamaa").

    The term must already be normalized.
    """
    body = re.escape(term)
    prefix = ""
    if allow_prefix:
        # Longest prefixes first so "لل" wins over "ل".
        alternatives = "|".join(re.escape(p) for p in sorted(PREFIXES, key=len, reverse=True))
        prefix = f"(?:{alternatives})?"
    return re.compile(rf"(?<!\S){prefix}{body}(?!\S)")


def term_variants(terms: Iterable[str]) -> List[str]:
    """
    Normalise une liste de mots-clés et ajoute les variantes sans article arabe.
    Normalize keywords and add article-less Arabic variants, deduplicated,
    longest first (so multi-word phrases win over single words).
    """
    variants = set()
    for term in terms:
        norm = normalize_text(term)
        if not norm:
            continue
        variants.add(norm)
        variants.add(strip_arabic_article(norm))
    return sorted(variants, key=len, reverse=True)


# --- Détection de langue / language detection ------------------------------

_FRENCH_WORDS = {
    "je", "veux", "un", "une", "le", "la", "les", "de", "du", "des", "pour",
    "vers", "a", "au", "aux", "et", "s", "il", "vous", "plait", "gare",
    "derriere", "devant", "pres", "moi", "mon", "ma", "livrer", "livraison",
    "pain", "lait", "colis", "vite", "urgent", "aller", "est", "avec",
}
_DARIJA_LATIN_WORDS = {
    "bghit", "bghina", "waslni", "wasalni", "jib", "jibli", "lia", "liya",
    "khobz", "7lib", "hlib", "fasa", "fissa", "basr", "dlak", "daba", "dyal",
    "dial", "kolchi", "wach", "fin", "mn", "men", "hna", "tma", "safi",
    "ghir", "bzaf", "chwiya", "zerba", "taksi", "jamaa", "djamaa", "diini",
    "hezni", "nmchi", "sift", "sifet", "atay", "3afak", "afak", "o", "w",
}
_ARABIZI_DIGIT = re.compile(r"[a-z][379]|[379][a-z]")


def detect_language(text: str) -> str:
    """
    Détecte grossièrement la langue / script d'une commande.
    Rough language/script detection for a command.

    Returns one of: "darija" (Arabic script), "darija_latin" (arabizi),
    "french", "mixed" (code-switching), "unknown".
    """
    norm = normalize_text(text)
    if not norm:
        return "unknown"
    arabic_chars = sum(1 for ch in norm if "؀" <= ch <= "ۿ")
    latin_chars = sum(1 for ch in norm if "a" <= ch <= "z")
    total = arabic_chars + latin_chars
    if total == 0:
        return "unknown"

    tokens = norm.split()
    french_hits = sum(1 for t in tokens if t in _FRENCH_WORDS)
    darija_hits = sum(1 for t in tokens if t in _DARIJA_LATIN_WORDS)
    darija_hits += len(_ARABIZI_DIGIT.findall(norm))

    arabic_ratio = arabic_chars / total
    if arabic_ratio > 0.85:
        return "darija"
    if arabic_ratio > 0.15:
        return "mixed"
    if darija_hits and french_hits:
        return "mixed"
    if french_hits > darija_hits:
        return "french"
    if darija_hits:
        return "darija_latin"
    # Latin script with no recognizable words: most likely arabizi.
    return "darija_latin"


def build_lookup(mapping: Dict[str, Iterable[str]]) -> Dict[str, List[Pattern[str]]]:
    """Compile {label: [keywords]} into {label: [patterns]} (longest first)."""
    return {label: [compile_term(v) for v in term_variants(words)] for label, words in mapping.items()}
