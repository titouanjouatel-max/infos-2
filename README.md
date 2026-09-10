# Tracker Macro Automatisé — XAUUSD · NAS100 · SP500 · BTCUSD

Tableau de bord 100 % automatisé, hébergé sur GitHub Pages, qui affiche le biais
courant (🟢 HAUSSE / 🔴 BAISSE / ⚪ NEUTRE) de 4 actifs — **Or (XAUUSD)**,
**Nasdaq 100 (NAS100)**, **S&P 500 (SP500)** et **Bitcoin (BTCUSD)** — à partir
de l'actualité économique du moment.

Aucune intervention manuelle n'est nécessaire une fois configuré : un workflow
GitHub Actions s'exécute **toutes les heures** (et à chaque push), récupère les
dernières actualités financières, les fait analyser par l'API Anthropic
(Claude), régénère la page, et la republie sur GitHub Pages.

## Comment ça marche

1. **`tracker.py`** :
   - Récupère les dernières actualités via plusieurs flux RSS (Google News —
     Fed/inflation, marchés, crypto, géopolitique —, Yahoo Finance, ForexFactory).
   - Envoie ces titres à l'API Anthropic (modèle Claude) avec un jeu de règles
     macro strictes :
     - Inflation en hausse / Fed *hawkish* → 🔴 BAISSE pour les 4 actifs.
     - Inflation en baisse / Fed *dovish* → 🟢 HAUSSE pour les 4 actifs.
     - Tensions géopolitiques → 🟢 HAUSSE pour l'or, 🔴 BAISSE pour NAS100/SP500/BTCUSD.
     - Résultats Tech positifs → 🟢 HAUSSE pour NAS100 et SP500.
     - Flux ETF / régulation crypto favorable → 🟢 HAUSSE pour BTCUSD.
   - Si la clé API est absente ou l'appel échoue, un **moteur de repli local**
     par mots-clés prend le relais automatiquement (le pipeline ne casse jamais).
   - Génère un `index.html` autonome (CSS inline, thème sombre, responsive).

2. **`.github/workflows/deploy.yml`** :
   - Se déclenche toutes les heures (`cron: "0 * * * *"`), à chaque push sur
     `main`, et manuellement (`workflow_dispatch`).
   - Installe les dépendances, exécute `tracker.py`, puis publie le résultat
     directement sur GitHub Pages via `actions/upload-pages-artifact` +
     `actions/deploy-pages` (aucun commit de régénération n'est poussé sur le
     dépôt, le déploiement se fait par artefact).

## Mise en service (une seule fois)

1. **Ajouter la clé API Anthropic** :
   `Settings` → `Secrets and variables` → `Actions` → `New repository secret`
   → nom `ANTHROPIC_API_KEY`, valeur = votre clé
   ([console.anthropic.com](https://console.anthropic.com/)).
   Sans cette clé, le site fonctionne quand même grâce au repli par mots-clés,
   mais l'analyse est plus fine avec Claude.

2. **Activer GitHub Pages via Actions** :
   `Settings` → `Pages` → `Build and deployment` → `Source` → sélectionner
   **`GitHub Actions`**.

3. Lancer le workflow une première fois (`Actions` → `Deploy Macro Tracker` →
   `Run workflow`), ou simplement pousser un commit sur `main`.

Le site est ensuite disponible à `https://<votre-utilisateur>.github.io/<repo>/`
et se met à jour automatiquement chaque heure.

## Structure du dépôt

```
infos/
├── tracker.py                     # Récupération RSS + analyse Claude + génération HTML
├── requirements.txt                # Dépendances Python (feedparser, requests, anthropic)
├── index.html                      # Dashboard généré (régénéré à chaque exécution)
├── .github/workflows/deploy.yml    # Automatisation horaire + déploiement Pages
└── README.md
```

## Exécution locale

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # optionnel, sinon repli par mots-clés
python3 tracker.py
# ouvre index.html dans un navigateur
```

## Limites connues

- L'analyse dépend de la qualité et de la fraîcheur des flux RSS publics ; un
  flux indisponible est simplement ignoré (le pipeline continue).
- Le moteur de repli par mots-clés est une heuristique simple : il est moins
  nuancé que l'analyse via Claude.
- ⚠️ Ceci n'est pas un conseil en investissement. Le biais affiché est une
  aide à la lecture automatisée, pas une prédiction fiable des marchés.
