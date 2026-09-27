---
title: Wassal
emoji: 🎙️
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
pinned: false
short_description: Assistant vocal Darija vers commandes Yassir (démo)
---

# Wassal - وصّل

Assistant vocal en **Darija** pour Yassir : on parle, par exemple « بغيت تاكسي للقارة » ou « taxi derrière la mosquée », et Wassal prépare la commande **Yassir Go / Food / Market**. Il comprend aussi les adresses à la marocaine, à partir de vrais lieux OpenStreetMap.

- **Code source :** https://github.com/oussamabartil/wassal
- **Transcription :** [MoulSot v0.3](https://huggingface.co/atlasia/moulsot.v0.3), d'Atlasia (Apache 2.0)
- **Lieux :** © contributeurs [OpenStreetMap](https://www.openstreetmap.org/copyright), licence ODbL

## À savoir avant d'essayer

- **C'est une démonstration.** Aucune commande n'est envoyée à Yassir : la course, le chauffeur et le temps d'arrivée affichés sont **simulés**.
- **Au démarrage, le modèle vocal se charge pendant quelques minutes.** Les commandes tapées fonctionnent tout de suite. L'état du modèle est visible sur `/wassal/test` (`asr_model_status`).
- **Sur le matériel gratuit (CPU), une transcription prend plusieurs secondes.**
- **N'entrez pas de vrai numéro de téléphone.** Le texte des commandes apparaît dans les journaux du serveur.
