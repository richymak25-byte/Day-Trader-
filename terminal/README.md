# MINDLOCK Terminal

Un terminal de trading complet, en local, construit sur les onze skills du pack.
Python standard uniquement : rien à installer avec pip.

```bash
python3 terminal/server.py                      # http://127.0.0.1:8765
python3 terminal/server.py --budget 25000 --max-loss 100
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
