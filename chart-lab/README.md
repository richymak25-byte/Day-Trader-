# Chart Lab

Des graphiques gratuits, sans limites, sur ta propre machine. Pas de compte,
pas d'abonnement, pas de quota.

```bash
python3 chart-lab/server.py --open        # http://127.0.0.1:8765
```

Lance-le depuis le dossier qui contient (ou contiendra) tes fichiers `trader/`.
Python 3.8+ et la bibliothèque standard suffisent : rien à installer.

## Ce que tu as, sans limite

| | |
| --- | --- |
| **Indicateurs** | Autant que tu veux, avec tes paramètres : moyennes mobiles, EMA, VWAP de la séance, Bollinger, SAR, volume, MACD, RSI, ATR, stochastique, OBV, CCI, Williams %R, DMI/ADX, ROC, momentum, TRIX, Awesome Oscillator... |
| **Dessins** | Segments, demi-droites, droites, horizontales, verticales, ligne de prix, canaux, Fibonacci, annotations, étiquettes. Aimant OHLC, verrouillage, masquage. Sauvegardés sur ton disque dans `trader/chartlab/drawings/<SYMBOLE>.json`, un fichier par symbole, visibles sur tous les intervalles. Clic droit sur un dessin pour l'effacer. |
| **Disposition** | 1, 2 ou 4 graphiques. Intervalles 1 min, 5 min, 15 min, 1 h, jour, semaine. |
| **Relecture** | Bouton « Relecture », clique sur une bougie passée, puis lecture / pause / +1 bougie (flèche droite, barre d'espace) à 1x, 2x, 5x ou 10x. Pratique pour s'entraîner sans voir la suite. |
| **Listes** | Ta liste de symboles, plus les setups trouvés par `/trader-scanner` (lus dans `trader/watchlist.json`). |
| **Plan** | Choisis l'entrée, le stop et l'objectif en cliquant sur le graphique, puis « Planifier » : `trader-exit` écrit `trader/plan.json`, la Time Machine rejoue 10 000 séances et `trader-budget` calcule le nombre d'actions selon tes règles (`trader/config.json`). |
| **Alertes** | Alertes de prix au-dessus / en dessous, ou les trois alertes du plan (stop, objectif, « flat by close ») dans `trader/alerts.json`. Elles sonnent avec `/trader-alerts check` (par exemple `/loop 5m /trader-alerts check`). |
| **Capture** | Exporte le graphique en PNG, dessins compris. |

## Ce qu'il ne fait pas

- **Aucun ordre.** La page n'écrit jamais `trader/orders.json` et ne parle à
  aucun courtier. Pour passer un ordre, demande à Claude `/trader-risk` puis
  `/trader-broker` : papier par défaut, et ton « oui » est toujours demandé.
- **Pas de temps réel garanti.** Les données viennent de l'API de graphiques
  de Yahoo Finance : gratuite, non officielle, différée, et elle peut
  répondre 429 si on la sollicite trop. Le serveur garde chaque série en cache
  20 secondes à 15 minutes selon l'intervalle, et les graphiques intraday se
  rafraîchissent une fois par minute.
- **Achat seulement** pour le plan, comme le reste du pack.
- **Pas un conseil financier.** La plupart des day traders perdent de l'argent.

## Options

```bash
python3 chart-lab/server.py --port 9000
python3 chart-lab/server.py --workdir ~/trading          # où se trouve trader/
python3 chart-lab/server.py --offline tests/fixtures/trader-timemachine   # démo sans réseau (TSLA)
```

Le serveur n'écoute que sur `127.0.0.1` et refuse les requêtes venant d'un
autre site (en-têtes `Host` et `Origin` vérifiés).

## Fichiers

```
chart-lab/server.py                 serveur local + API (bibliothèque standard)
chart-lab/static/index.html         la page
chart-lab/static/app.js             graphiques, dessins, relecture, plan, alertes
chart-lab/static/app.css            thème clair et sombre
chart-lab/static/vendor/            KLineChart 9.8.12 (Apache 2.0, voir KLINECHARTS-LICENSE)
trader/chartlab/drawings/*.json     tes dessins
trader/chartlab/layout.json         ta disposition (symboles, indicateurs, listes)
```

Les graphiques sont dessinés par [KLineChart](https://github.com/klinecharts/KLineChart)
(Apache 2.0), copié dans `static/vendor/` pour que tout marche hors ligne.
