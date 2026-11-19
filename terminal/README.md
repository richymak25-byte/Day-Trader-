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

## Stratégie MINDLOCK EMA CORE TREND V.1 (ton Pine Script)

Le terminal fait tourner un port Python, règle par règle, du Pine Script
*MINDLOCK EMA CORE TREND V.1*, sur **n'importe quel actif** et en **M5, M15, M30, H1, H4**.
Le code est dans `terminal/mindlock.py`.

| Bloc | Règle (réglages par défaut du script) |
| --- | --- |
| Signal BUY | EMA 20 croise au-dessus de l'EMA 50, clôture > EMA 100, RSI 14 > 50 |
| Signal SELL | EMA 20 croise sous l'EMA 50, clôture < EMA 100, RSI 14 < 50 |
| Entrée | BODY LIMIT : corps de la bougie signal + 5 unités ; aussi MARKET, EMA 20, EMA 50, EMA CORE 20/50, OTE Fibonacci (0,705, 20 bougies) |
| Ordre en attente | expire après 10 bougies, remplacé sur signal opposé, annulé hors session ; les limits EMA et OTE suivent le marché |
| SL | structurel : plus bas / plus haut des 10 dernières bougies ± 2 unités, plafonné à 100 unités |
| TP / BE | 300 unités ; SL ramené à l'entrée après +200 unités |
| Signal opposé | ferme la position puis entre dans le nouveau sens |
| Session | 09:30 – 21:45 New York : aucun nouvel ordre en dehors |
| Daily lock | sur le P&L **réalisé** du jour : -2 % / +4 % ; ordres annulés, plus d'entrée jusqu'au lendemain |
| Unité Auto | futures = tick, forex = pip, autres actifs = 10 × tick minimum (crypto : tick selon l'échelle du prix) |

Taille (règle du terminal) : 0,5 % du compte au SL, plafonnée à 30 × le compte en valeur de position.
Le script, lui, trade 1 contrat par défaut : mets « Risque en % » à non et « Lot fixe » à 1 pour le reproduire.

- **Panneau MINDLOCK** : choix du timeframe, les 3 conditions du signal ✓/✗, l'ordre limit proposé avec SL structurel, TP et taille, l'ordre en attente, la position (BE compris), le journal des événements, le mode auto par actif / timeframe.
- **Graphique** : EMA 20 (jaune), 50 (vert), 100 (rouge), les signaux BUY / SELL, les lignes limit / SL / TP.
- **Scanner MINDLOCK** : watchlist × M5 → H4, signal en cours, dernier signal et alignement des EMA.
- **Backtest** : sur l'actif et le timeframe affichés (60 jours en M5 / M15 / M30, 2 ans en H1 / H4) ou sur un export CSV MetaTrader / TradingView.
- **Réglages** : les mêmes groupes que le script, puis « Enregistrer comme réglages live » (`trader/mindlock.json`).

Simulation : le simulateur de TradingView est approché bougie par bougie (limit rempli au prix ou à l'ouverture en cas de gap,
SL / TP actifs dès la bougie suivante, extrême le plus proche de l'ouverture en premier). Pas de spread, commission ni slippage.
Les données Yahoo (l'or = contrat à terme GC=F) sont proches de ton courtier, pas identiques.

## Règles de gestion du risque

| Règle | Valeur par défaut | Effet |
| --- | --- | --- |
| Risque par trade | **0,5 %** du compte | La taille de position est calculée pour que la perte au stop ne dépasse pas 0,5 % de l'équité d'ouverture du jour (50 $ sur 10 000 $). |
| Verrou perte | **-2 %** du P&L réalisé du jour (DailyLossPercent) | Dès que le P&L réalisé du jour atteint -2 %, plus aucune entrée jusqu'au lendemain. |
| Verrou gain | **+4 %** du P&L réalisé du jour (DailyProfitPercent) | Dès que le P&L réalisé du jour atteint +4 %, la journée est terminée : gains protégés, plus aucune entrée. |

- Comme dans le script, seul le P&L réalisé compte, rapporté à l'équité d'ouverture de la journée de New York.
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
