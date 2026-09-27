"""
Catégories de lieux : mots Darija <-> tags OpenStreetMap.
Place categories shared by the OSM importer and the landmark resolver.

- `osm`   : filtres Overpass utilisés par scripts/import_osm.py.
- `words` : ce que les gens disent ("jamaa", "فرمسيان"...). Quand un de ces mots
            apparaît sans nom précis, on cherche le lieu de cette catégorie
            le plus proche de l'utilisateur. Liste vide = on ne cherche ces
            lieux que par leur nom (cafés, restaurants...), car le mot seul
            serait ambigu ("qahwa" = café le lieu OU le café à acheter).
- `named_only` : n'importer que les éléments qui ont un nom.
"""

from typing import Any, Dict, List

CATEGORIES: Dict[str, Dict[str, Any]] = {
    "mosque": {
        "label": "Mosquée",
        "osm": ['["amenity"="place_of_worship"]["religion"="muslim"]'],
        "words": ["jamaa", "djamaa", "jama3", "jam3", "jem3", "jame3", "masjid", "جامع", "الجامع",
                  "مسجد", "المسجد", "mosquee", "la mosquee"],
    },
    "pharmacy": {
        "label": "Pharmacie",
        "osm": ['["amenity"="pharmacy"]'],
        "words": ["pharmacie", "la pharmacie", "farmasyan", "fermasyan", "frmasyan", "farmasi",
                  "فرمسيان", "الفرمسيان", "فارمسي", "صيدلية", "الصيدلية"],
    },
    "hospital": {
        "label": "Hôpital / clinique",
        "osm": ['["amenity"="hospital"]', '["amenity"="clinic"]'],
        "words": ["sbitar", "sbitar", "lsbitar", "سبيطار", "السبيطار", "مستشفى", "hopital",
                  "l hopital", "clinique", "la clinique", "klinik", "كلينيك", "مصحة"],
    },
    "school": {
        "label": "École",
        "osm": ['["amenity"="school"]'],
        "words": ["madrasa", "lmadrasa", "lmdrasa", "مدرسة", "المدرسة", "ecole", "l ecole",
                  "lycee", "le lycee", "ليسي", "college"],
    },
    "university": {
        "label": "Université / faculté",
        "osm": ['["amenity"="university"]', '["amenity"="college"]'],
        "words": ["jami3a", "جامعة", "الجامعة", "universite", "fac", "la fac", "faculte", "كلية"],
    },
    "train_station": {
        "label": "Gare",
        "osm": ['["railway"="station"]'],
        "words": ["gare", "la gare", "lagar", "lagare", "laggar", "لاكار", "لاگار", "القارة",
                  "قارة", "محطة القطار", "lmahatta", "المحطة", "train", "tran", "تران"],
    },
    "tram_stop": {
        "label": "Arrêt de tram",
        "osm": ['["railway"="tram_stop"]'],
        "words": ["tram", "tramway", "tramway", "طرامواي", "الطرام", "arret tram"],
    },
    "bus_station": {
        "label": "Gare routière",
        "osm": ['["amenity"="bus_station"]'],
        "words": ["gare routiere", "ctm", "محطة الحافلات", "mahatta dyal tobisat", "tobis", "طوبيس"],
    },
    "airport": {
        "label": "Aéroport",
        "osm": ['["aeroway"="aerodrome"]["iata"]'],
        "words": ["aeroport", "l aeroport", "laeroport", "airport", "matar", "lmatar", "المطار", "مطار"],
    },
    "bank": {
        "label": "Banque",
        "osm": ['["amenity"="bank"]'],
        "words": ["banka", "labanka", "bnka", "banque", "la banque", "بنكة", "البنكة", "البنك"],
    },
    "police": {
        "label": "Commissariat",
        "osm": ['["amenity"="police"]'],
        "words": ["comisariya", "lkomisariya", "komisariya", "كوميسارية", "الكوميسارية",
                  "commissariat", "police", "bolis", "البوليس"],
    },
    "post_office": {
        "label": "Bureau de poste",
        "osm": ['["amenity"="post_office"]'],
        "words": ["la poste", "lbosta", "bosta", "البوسطة", "بريد المغرب", "barid"],
    },
    "townhall": {
        "label": "Commune / arrondissement",
        "osm": ['["amenity"="townhall"]'],
        "words": ["baladiya", "lbaladiya", "البلدية", "بلدية", "commune", "la commune",
                  "mo9ata3a", "lmo9ata3a", "المقاطعة", "مقاطعة"],
    },
    "marketplace": {
        "label": "Souk / marché",
        "osm": ['["amenity"="marketplace"]'],
        "words": ["souk", "sou9", "السوق", "سوق", "marche", "le marche", "lmarchi", "marchi", "المارشي"],
    },
    "mall": {
        "label": "Centre commercial",
        "osm": ['["shop"="mall"]'],
        "words": ["mall", "lmall", "centre commercial", "المول", "مول"],
    },
    "supermarket": {
        "label": "Supermarché",
        "osm": ['["shop"="supermarket"]'],
        "words": ["supermarche", "le supermarche", "سوبر مارشي", "سوبرماركت"],
    },
    "convenience": {
        "label": "Hanout / épicerie",
        "osm": ['["shop"="convenience"]'],
        "words": ["hanout", "l7anout", "7anout", "lhanout", "حانوت", "الحانوت", "epicerie", "l epicerie"],
    },
    "fuel": {
        "label": "Station-service",
        "osm": ['["amenity"="fuel"]'],
        "words": ["station essence", "station service", "la pompe", "pompe", "lbomba", "bomba",
                  "بومبة", "البومبة"],
    },
    "park": {
        "label": "Parc / jardin",
        "osm": ['["leisure"="park"]["name"]'],
        "words": ["jnan", "jnane", "جنان", "حديقة", "jardin", "le jardin", "parc", "le parc", "lpark"],
    },
    "stadium": {
        "label": "Stade",
        "osm": ['["leisure"="stadium"]'],
        "words": ["stade", "le stade", "lstad", "stad", "ملعب", "الملعب", "ستاد"],
    },
    "hotel": {
        "label": "Hôtel",
        "osm": ['["tourism"="hotel"]'],
        "words": ["hotel", "l hotel", "otel", "اوطيل", "فندق"],
        "named_only": True,
    },
    # Recherche par nom uniquement (le mot seul serait ambigu).
    "cafe": {
        "label": "Café",
        "osm": ['["amenity"="cafe"]'],
        "words": [],
        "named_only": True,
    },
    "restaurant": {
        "label": "Restaurant",
        "osm": ['["amenity"="restaurant"]', '["amenity"="fast_food"]'],
        "words": [],
        "named_only": True,
    },
    "landmark": {
        "label": "Lieu connu",
        "osm": ['["place"="square"]', '["tourism"="attraction"]', '["historic"="city_gate"]',
                '["historic"="monument"]', '["tourism"="museum"]'],
        "words": [],
        "named_only": True,
    },
}

# Enseignes : "7da bim" = le BIM le plus proche. Reconnues via le tag OSM
# `brand` ou le nom du lieu. Brands matched on the OSM `brand` tag or name.
BRANDS: Dict[str, List[str]] = {
    "bim": ["bim", "بيم"],
    "marjane": ["marjane", "marjan", "مرجان"],
    "carrefour": ["carrefour", "كارفور"],
    "acima": ["acima", "أسيما", "اسيما"],
    "label_vie": ["label vie", "labelvie"],
    "afriquia": ["afriquia", "افريقيا"],
    "shell": ["shell", "شيل"],
    "totalenergies": ["total", "totalenergies"],
    "mcdonalds": ["mcdonald s", "mcdonalds", "mcdo", "macdo", "ماكدو", "ماكدونالدز"],
    "kfc": ["kfc"],
    "attijariwafa": ["attijariwafa", "attijari", "التجاري وفا بنك", "التجاري"],
    "cih": ["cih"],
    "bmce": ["bmce", "bank of africa"],
    "banque_populaire": ["banque populaire", "chaabi", "البنك الشعبي", "الشعبي"],
}


def brand_category(brand_key: str) -> str:
    """Clé de catégorie d'une enseigne / category key for a brand."""
    return f"brand:{brand_key}"


def category_label(category: str) -> str:
    """Libellé lisible, y compris pour les enseignes / human label."""
    if category.startswith("brand:"):
        key = category.split(":", 1)[1]
        return BRANDS.get(key, [key])[0].title()
    return CATEGORIES.get(category, {}).get("label", category)
