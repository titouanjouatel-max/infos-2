# Tracker Macro Automatisé — XAUUSD · NAS100 · SP500 · BTCUSD

Tableau de bord 100 % automatisé, hébergé sur GitHub Pages, qui affiche :

- le **prix indicatif en dollars** de 4 actifs — **Or (XAUUSD)**, **Nasdaq 100
  (NAS100)**, **S&P 500 (SP500)** et **Bitcoin (BTCUSD)** ;
- leur **biais directionnel de fond** (🟢 HAUSSE / 🔴 BAISSE / ⚪ NEUTRE) déduit
  de l'actualité économique du moment, avec une explication en une phrase ;
- une **synthèse en français** du climat de marché actuel ;
- un **calendrier économique** listant les prochaines échéances à impact
  moyen/fort (Fed, CPI, emploi...).

Aucune intervention manuelle n'est nécessaire une fois configuré : un workflow
GitHub Actions s'exécute **toutes les 5 minutes** (et à chaque push), récupère
les dernières actualités et cotations, les fait analyser par l'API Anthropic
(Claude), régénère la page, et la republie sur GitHub Pages.

## ⚠️ Ce que cet outil est — et n'est pas

Ce tracker tourne au mieux toutes les 5 minutes et se base sur de
**l'actualité écrite**, pas sur le carnet d'ordres ou des données de prix
tick-by-tick. **Il ne fournit donc pas de signal d'entrée fiable à la minute**
(scalping) — aucune analyse de news, aussi rapide soit-elle, ne peut remplacer
l'action du prix en temps réel pour ce type de décision.

Ce qu'il est réellement utile pour un trader court terme :
- connaître le **biais de fond** de la session pour éviter de scalper à
  contre-tendance toute la journée ;
- savoir **quand ne pas trader** : le calendrier économique liste les
  échéances à fort impact (Fed, CPI, NFP...), qui sont typiquement les moments
  où les mèches les plus violentes et les plus imprévisibles se produisent.

Les prix affichés sont **différés** (source gratuite, sans flux temps réel) —
vérifiez toujours le prix exact chez votre broker avant toute décision.

## Comment ça marche

1. **`tracker.py`** :
   - Récupère les dernières actualités via plusieurs flux RSS (Google News —
     Fed/inflation, marchés, crypto, géopolitique —, Yahoo Finance).
   - Récupère un prix indicatif pour chaque actif via Stooq (gratuit, sans clé
     API), avec un repli sur CoinGecko pour le Bitcoin en cas d'échec.
   - Récupère le calendrier économique de la semaine via ForexFactory et ne
     garde que les événements à impact moyen/fort.
   - Envoie les titres collectés à l'API Anthropic (modèle Claude) avec un jeu
     de règles macro strictes :
     - Inflation en hausse / Fed *hawkish* → 🔴 BAISSE pour les 4 actifs.
     - Inflation en baisse / Fed *dovish* → 🟢 HAUSSE pour les 4 actifs.
     - Tensions géopolitiques → 🟢 HAUSSE pour l'or, 🔴 BAISSE pour NAS100/SP500/BTCUSD.
     - Résultats Tech positifs → 🟢 HAUSSE pour NAS100 et SP500.
     - Flux ETF / régulation crypto favorable → 🟢 HAUSSE pour BTCUSD.
   - Demande également à Claude une courte synthèse en français du climat de
     marché.
   - Si la clé API est absente ou l'appel échoue, un **moteur de repli local**
     par mots-clés prend le relais automatiquement (le pipeline ne casse jamais).
   - Génère un `index.html` autonome (CSS inline, thème sombre, responsive).

2. **`.github/workflows/deploy.yml`** :
   - Se déclenche toutes les 5 minutes (`cron: "*/5 * * * *"` — l'intervalle
     minimum fiable pour un cron GitHub Actions), à chaque push sur `main`, et
     manuellement (`workflow_dispatch`).
   - Installe les dépendances, exécute `tracker.py`, puis publie le résultat
     directement sur GitHub Pages via `actions/upload-pages-artifact` +
     `actions/deploy-pages` (aucun commit de régénération n'est poussé sur le
     dépôt, le déploiement se fait par artefact).

## Mise en service (une seule fois)

1. **Ajouter la clé API Anthropic** (optionnel mais recommandé) :
   `Settings` → `Secrets and variables` → `Actions` → `New repository secret`
   → nom `ANTHROPIC_API_KEY`, valeur = votre clé
   ([console.anthropic.com](https://console.anthropic.com/)).
   Sans cette clé, le site fonctionne quand même grâce au repli par mots-clés,
   mais l'analyse et la synthèse sont plus fines avec Claude.

2. **Activer GitHub Pages via Actions** :
   `Settings` → `Pages` → `Build and deployment` → `Source` → sélectionner
   **`GitHub Actions`**.

3. Lancer le workflow une première fois (`Actions` → `Deploy Macro Tracker` →
   `Run workflow`), ou simplement pousser un commit sur `main`.

Le site est ensuite disponible à `https://<votre-utilisateur>.github.io/<repo>/`
et se met à jour automatiquement toutes les 5 minutes.

## Structure du dépôt

```
infos/
├── tracker.py                     # RSS + prix + calendrier + analyse Claude + génération HTML
├── requirements.txt                # Dépendances Python (feedparser, requests, anthropic)
├── index.html                      # Dashboard généré (régénéré à chaque exécution)
├── .github/workflows/deploy.yml    # Automatisation (5 min) + déploiement Pages
└── README.md
```

## Exécution locale

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # optionnel, sinon repli par mots-clés
python3 tracker.py
# ouvre index.html dans un navigateur
```

## Sources de données

| Donnée              | Source                          | Clé API requise |
|---------------------|----------------------------------|------------------|
| Actualités          | Google News, Yahoo Finance       | Non              |
| Prix XAUUSD/NAS100/SP500/BTCUSD | Stooq (différé)      | Non              |
| Prix BTCUSD (repli) | CoinGecko                        | Non              |
| Calendrier économique | ForexFactory                   | Non              |
| Analyse macro        | API Anthropic (Claude)          | Oui (optionnelle, repli par mots-clés sinon) |

## Limites connues

- **Aucun signal de scalping** : l'outil se met à jour toutes les 5 minutes au
  mieux et se base sur de l'actualité, pas sur des données de marché en temps
  réel — voir la section ⚠️ ci-dessus.
- Les prix affichés sont différés et purement indicatifs.
- L'analyse dépend de la qualité et de la fraîcheur des flux RSS publics ; un
  flux indisponible est simplement ignoré (le pipeline continue).
- Le moteur de repli par mots-clés est une heuristique simple : il est moins
  nuancé que l'analyse via Claude.
- ⚠️ Ceci n'est pas un conseil en investissement. Le biais affiché est une
  aide à la lecture automatisée, pas une prédiction fiable des marchés.
