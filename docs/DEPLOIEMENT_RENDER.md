# Mettre Wassal en ligne gratuitement sur Render (sans la voix)

Résultat : un lien public en HTTPS, du type `https://wassal-xxxx.onrender.com`, que toute l'équipe et le jury peuvent ouvrir sur téléphone ou sur ordinateur.

## Ce qui marche en ligne, et ce qui ne marche pas

| ✅ En ligne | ❌ Pas en ligne |
|---|---|
| Commandes **tapées** en Darija (arabe, arabizi, français) | La **voix** : le bouton 🎤 est grisé et invite à taper |
| Les 11 352 vrais lieux OpenStreetMap, « la mosquée la plus proche » via le bouton 📍 | |
| Onglets Yassir Go / Food / Market et dispatch **simulé** | |

**Pourquoi pas la voix :** le modèle MoulSot a besoin d'environ 7 Go de mémoire, et l'offre gratuite de Render n'en donne que 512 Mo. Sans la voix, Wassal utilise environ 80 Mo.

Hugging Face aurait pu faire tourner la voix, mais ses Spaces Docker demandent désormais l'abonnement payant PRO. Pour la démo vocale, lancez Wassal **en local** : `python src/api.py`.

---

## Étape 0 : avant de commencer

La branche `deploy/render-free` doit être **mergée dans `main`**, car Render déploie `main`.

## Étape 1 : créer le service (environ 5 minutes)

1. Allez sur https://render.com et cliquez sur **Get Started**, puis **Sign in with GitHub** (compte `oussamabartil`).
2. Dans le tableau de bord, cliquez sur **New → Blueprint**.
3. Connectez GitHub si on vous le demande, puis choisissez le dépôt **`oussamabartil/wassal`**.
4. Render lit le fichier [`render.yaml`](../render.yaml) et affiche un service `wassal` (plan **Free**). Cliquez sur **Apply**.

C'est tout. Le fichier `render.yaml` règle déjà :
- l'installation, avec les dépendances légères de `requirements-server.txt` ;
- le démarrage, avec gunicorn ;
- la vérification de santé, sur `/wassal/test` ;
- le mot de passe du crowdsourcing (`WASSAL_API_TOKEN`), généré au hasard par Render.

## Étape 2 : attendre et vérifier (environ 3 à 5 minutes)

1. Dans le service `wassal`, l'onglet **Logs** montre l'installation, puis `Listening at: http://0.0.0.0:…`.
2. Le lien apparaît en haut de la page : `https://wassal-xxxx.onrender.com`.
3. Ouvrez `…/wassal/test`. Vous devez y lire `"status": "ok"`, environ 11 360 lieux et `"asr_available": false`.
4. Ouvrez le lien principal, cliquez sur 📍 et tapez `taxi derrière la mosquée`.

## Partager avec l'équipe

Envoyez simplement le lien `https://wassal-xxxx.onrender.com`. Il n'y a rien à installer.

## Bon à savoir

- **Le service s'endort** après environ 15 minutes sans visite, sur l'offre gratuite. La visite suivante le réveille, en environ une minute. Avant une démo, ouvrez le lien quelques minutes à l'avance.
- **Chaque push sur `main` redéploie automatiquement.** Merger une PR suffit pour mettre le site à jour.
- **Les lieux ajoutés par crowdsourcing sont perdus à chaque redéploiement,** car le disque gratuit n'est pas conservé. Les lieux OpenStreetMap, eux, sont dans le code et ne sont jamais perdus.
- **Pour ajouter un lieu via l'API,** le mot de passe se trouve dans Render, sous **Environment → `WASSAL_API_TOKEN`**. Envoyez-le dans l'en-tête `X-API-Key`.
- **Vie privée :** le texte des commandes apparaît dans les **Logs**. Demandez aux testeurs de ne pas saisir de vrai numéro de téléphone.
