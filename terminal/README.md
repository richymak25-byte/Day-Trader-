# MINDLOCK Terminal

Un terminal de trading complet, en local, construit sur les onze skills du pack.
Python standard uniquement : rien à installer avec pip.

```bash
python3 terminal/server.py                      # http://127.0.0.1:8765
python3 terminal/server.py --budget 25000
python3 terminal/server.py --anytime            # entraînement paper hors séance
```

## Ce qu'il y a dedans

| Zone | Ce qu'elle fait | Brique utilisée |
| --- | --- | --- |
| Bandeau macro | S&P 500, Nasdaq, Russell, VIX, taux 10 ans, dollar, pétrole, or, bitcoin, EUR/USD, et un régime RISK-ON / RISK-OFF / MIXTE | Yahoo Finance |
| Watchlist | Prix, variation, mini-graphique, ajout et retrait (gardée dans le navigateur) | Yahoo Finance |
| Graphique | Bougies de 1 jour à 5 ans, volume, EMA 9 / 21, VWAP intraday, lignes entrée / stop / cible du plan | lightweight-charts |
| Biais du marché | Score HAUSSIER / BAISSIER / NEUTRE de -100 à +100, avec chaque raison et ses points : tendance, VWAP, MACD, RSI, moyennes 20 / 50 jours, ton des news | règles transparentes |
| News | Titres Yahoo par symbole, marqués positif / négatif et par thème macro (Fed, CPI, NFP, OPEP…) | trader-news |
| Ticket d'ordre | 1 plan (stop en %, en ATR ou à un prix fixe ; cible en R), 2 taille, 3 risque, 4 envoi | trader-exit, trader-budget, trader-risk |
| Time Machine | 10 000 séances simulées et la même règle rejouée sur chaque séance passée | trader-timemachine |
| Scanner | Cassure 20 jours, bull flag, reprise de la MM20, survente et rebond sur la watchlist | trader-scanner |
| Compte paper | Positions, ordres bracket (le stop et la cible se déclenchent sur le dernier prix), historique, P&L | `trader/paper.json` |

## Stratégie MINDLOCK EMA CORE (universelle)

Le terminal fait tourner la stratégie de l'Expert Advisor *MINDLOCK EMA CORE V3 EMA TOUCH*
sur **n'importe quel actif** (or, forex, indices, actions, crypto), en **M5, M15, M30, H1 et H4**.
Le code est dans `terminal/mindlock.py`.

| Filtre | Achat | Vente |
| --- | --- | --- |
| Tendance | EMA 20 > EMA 50 | EMA 20 < EMA 50 |
| Touche | la bougie touche l'EMA 20 (tolérance 0 pip) | idem |
| RSI 14 momentum | RSI > 50 | RSI < 50 |
| RSI extrême | RSI < 75 | RSI > 25 |
| Bougie | haussière | baissière |
| Sorties | stop 150 pips, cible 600 pips | idem |

Clôture et inversion sur signal opposé, une position maximum par actif et par timeframe.
Le risque par trade est de 0,5 % du compte, et le verrou journalier de -2 % / +6 % sur tout le compte.

**Le pip s'adapte à l'actif** (`PipSize` = 0, automatique) : environ 1/10 000 du prix.
Ça donne 0,1 sur l'or (comme `XAUUSD_PipSize`), 0,0001 sur EURUSD, 0,01 sur USDJPY et sur les actions, et 1 sur le bitcoin.
**Le contrat** : 100 000 en forex, 100 oz sur l'or, 1 unité ailleurs.
**Les devises** : le P&L et la taille sont convertis en dollars quand l'actif est coté dans une autre devise (EUR, GBP, JPY…).

- **Panneau MINDLOCK** : choix du timeframe, signal de la dernière bougie clôturée avec chaque filtre ✓/✗, taille, exécution paper, mode auto par actif et par timeframe, liste de toutes les positions MINDLOCK.
- **Scanner MINDLOCK** : toute la watchlist × M5 → H4 en un tableau ; clique une case pour ouvrir l'actif.
- **Backtest** : sur l'actif et le timeframe affichés ; Yahoo donne 60 jours en M5 / M15 / M30 et 2 ans en H1 / H4 (H4 construit à partir du H1). Un export CSV MetaTrader permet de remonter plus loin.
- **Réglages** : dans l'onglet Backtest, puis « Enregistrer comme réglages live » (`trader/mindlock.json`).

Données Yahoo gratuites : l'or est le contrat à terme COMEX (GC=F), proche du spot XAUUSD de ton courtier. Le spread et le slippage ne sont pas simulés.

## Règles de gestion du risque

| Règle | Valeur par défaut | Effet |
| --- | --- | --- |
| Risque par trade | **0,5 %** du compte | La taille de position est calculée pour que la perte au stop ne dépasse pas 0,5 % de l'équité d'ouverture du jour (50 $ sur 10 000 $). |
| Verrou perte | **-2 %** du compte (DailyLossPercent) | Dès que le P&L du jour atteint -2 %, plus aucune entrée jusqu'au lendemain. |
| Verrou gain | **+6 %** du compte (DailyProfitPercent) | Dès que le P&L du jour atteint +6 %, la journée est terminée : gains protégés, plus aucune entrée. |

- Le P&L du jour se mesure contre l'équité à l'ouverture de la journée de New York (positions ouvertes comprises).
- Une fois déclenché, le verrou tient toute la journée, même si le P&L revient.
- Les clôtures de positions restent toujours possibles.
- Ces valeurs viennent des paramètres MINDLOCK (`RiskPercent`, `DailyLossPercent`, `DailyProfitPercent`, `UseDailyLock`), modifiables dans l'onglet Backtest.

## Sécurité

- **Paper uniquement.** Le terminal n'envoie aucun ordre à un vrai courtier.
  Pour passer en réel, il faut passer par `/trader-broker` et ton « oui ».
- Chaque achat passe par le Risk Manager (`trader-risk`) côté serveur. Un
  ordre BLOQUÉ n'est pas exécuté, même sur le compte simulé. `--anytime`
  ne lève que la règle de fenêtre horaire, pour s'entraîner le soir.
- Le serveur écoute sur `127.0.0.1` par défaut.
- Tes règles (perte max, limite journalière, taille max…) sont lues dans
  `trader/config.json`, comme pour les skills.

Données gratuites et différées. Outil éducatif, pas un conseil financier.
