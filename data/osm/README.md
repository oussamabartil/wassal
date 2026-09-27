# Lieux réels (OpenStreetMap)

Ce dossier contient les vrais lieux utilisés par Wassal pour résoudre les adresses : mosquées, pharmacies, gares, banques, hanouts, arrêts de tram, etc.

- **Source :** [OpenStreetMap](https://www.openstreetmap.org), via l'API Overpass
- **Licence :** [ODbL 1.0](https://opendatacommons.org/licenses/odbl/). © contributeurs OpenStreetMap. Toute interface qui affiche ces lieux doit citer « © OpenStreetMap contributors ».
- **Généré par :** `python scripts/import_osm.py`

La date de l'extraction et le nombre de lieux par catégorie se trouvent dans le champ `meta` de chaque fichier.

## Mettre à jour

```bash
python scripts/import_osm.py              # les 3 villes
python scripts/import_osm.py casablanca   # une seule ville
```

Pour ajouter une ville : ajoutez-la dans `CITIES` ([src/landmarks.py](../../src/landmarks.py)) avec sa zone `bbox`, puis relancez le script.

## Améliorer les données

Si un lieu manque ou est mal placé, le mieux est de le corriger **directement sur OpenStreetMap** (openstreetmap.org → Modifier), puis de relancer l'import. La correction profite alors à tout le monde, pas seulement à Wassal.
