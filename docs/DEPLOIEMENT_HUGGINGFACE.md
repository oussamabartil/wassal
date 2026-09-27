# Mettre Wassal en ligne sur Hugging Face Spaces (avec la voix)

Résultat : un lien public en HTTPS, du type `https://<votre-compte>-wassal.hf.space`, où le jury peut parler ou taper une commande en Darija.

**Pourquoi Hugging Face :**
- MoulSot y est déjà hébergé ;
- le matériel gratuit a assez de mémoire pour charger le modèle ;
- le HTTPS est fourni, et il est indispensable pour le micro et le bouton 📍.

> Les limites du matériel gratuit changent de temps en temps (mémoire, mise en veille). Vérifiez sur https://huggingface.co/pricing#spaces avant le jour de la démo.

---

## Comment ça marche

Le Space ne contient que **2 fichiers**, ceux du dossier [`deploy/huggingface/`](../deploy/huggingface/) :

| Fichier | Rôle |
|---|---|
| `README.md` | Configuration du Space (SDK Docker, port 7860) + page de présentation |
| `Dockerfile` | Récupère le code sur **GitHub** (branche `main`), installe les dépendances, lance le serveur |

Le code reste donc à un seul endroit : GitHub. Pour mettre le Space à jour, on relance un build, sans jamais copier le code à la main.

---

## Étape 0 : avant de commencer

La branche `deploy/huggingface-space` doit être **mergée dans `main`**, car le Dockerfile clone `main`.

## Étape 1 : créer le Space

1. Créez un compte sur https://huggingface.co/join, ou connectez-vous.
2. Allez sur https://huggingface.co/new-space et remplissez :
   - **Space name :** `wassal`
   - **SDK :** **Docker**, puis le modèle **Blank**
   - **Hardware :** **CPU basic** (gratuit)
   - **Visibility :** **Public**, pour que le jury puisse l'ouvrir
3. Cliquez sur **Create Space**.

## Étape 2 : envoyer les 2 fichiers

1. Dans le Space, ouvrez l'onglet **Files**, puis **Add file → Upload files**.
2. Glissez `deploy/huggingface/README.md` et `deploy/huggingface/Dockerfile`. Le `README.md` remplace celui créé par défaut.
3. Cliquez sur **Commit changes to main**.

Le build démarre tout seul. Vous pouvez le suivre dans l'onglet **Logs**.

## Étape 3 : protéger le crowdsourcing

Dans **Settings → Variables and secrets → New secret** :

| Nom | Valeur |
|---|---|
| `WASSAL_API_TOKEN` | un mot de passe long et aléatoire |

Sans ce secret, n'importe qui peut ajouter des lieux via `POST /wassal/landmarks`.

## Étape 4 : attendre et vérifier

1. **Le build** passe de « Building » à « Running ». Le premier build télécharge torch et ses dépendances, ce qui prend environ 10 à 20 minutes. Les suivants sont plus rapides, tant que `requirements.txt` ne change pas.
2. **Le modèle vocal** se télécharge ensuite, soit plusieurs Go, pendant quelques minutes. Pendant ce temps, **les commandes tapées marchent déjà**.
3. Ouvrez `https://<votre-compte>-wassal.hf.space/wassal/test` et lisez `asr_model_status.state` :
   - `loading` : le modèle est en cours de chargement, il faut patienter ;
   - `ready` : la voix fonctionne ;
   - `error` : lisez le message dans `asr_model_status.error` et dans l'onglet **Logs**.

## Étape 5 : tester comme le jury

Ouvrez **directement** `https://<votre-compte>-wassal.hf.space`, et non la page `huggingface.co/spaces/...`. Dans cette page, l'app est affichée dans un cadre, et le navigateur peut y bloquer le micro et la position.

1. Tapez `taxi derrière la mosquée`, cliquez sur 📍 et acceptez la localisation.
2. Envoyez un audio en Darija.
3. Validez la commande. Le dispatch affiché est **simulé**.

---

## Mettre à jour après un changement sur GitHub

1. Mergez la PR dans `main`.
2. Dans le Space : **Settings → Factory rebuild**.

## Le jour de la démo

- **Réveillez le Space 10 à 15 minutes avant.** Un Space gratuit s'endort quand personne ne l'utilise. Au réveil, il recharge le modèle.
- **Sur CPU gratuit, une phrase peut prendre plusieurs secondes.** Pour une démo fluide, passez temporairement sur un **GPU** dans **Settings → Hardware**. C'est payant à l'heure : pensez à revenir sur CPU basic juste après. Il faut aussi reconstruire l'image avec torch CUDA, voir ci-dessous.
- **Gardez un plan B :** des commandes tapées, qui marchent même si la voix est lente.

### Passer sur GPU (optionnel)

Le Dockerfile installe **torch CPU**. Pour un GPU, dans le `Dockerfile` du Space, remplacez :

```
torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu
```

par :

```
torch==2.14.0
```

Puis choisissez un matériel GPU dans **Settings → Hardware**.

## Données et vie privée

- **Les lieux ajoutés par crowdsourcing sont perdus à chaque redémarrage** : le disque d'un Space gratuit n'est pas conservé. Les 11 352 lieux OpenStreetMap, eux, sont dans l'image et ne sont jamais perdus.
- **Le texte des commandes apparaît dans les logs du Space.** Demandez aux testeurs de ne pas saisir de vrai numéro de téléphone.
