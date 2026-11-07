# MINDLOCK Journal

Ton journal de trading personnel, avec un dashboard complet, qui se remplit
automatiquement. Un seul fichier HTML, aucune inscription, aucune dépendance,
tes données restent sur ton appareil.

## Démarrer en 10 secondes

Ouvre `mindlock-journal/index.html` dans ton navigateur, puis :

- **Importer** un export CSV de ton broker (glisser-déposer accepté), ou
- **+ Trade** pour saisir un trade à la main, ou
- **Import & Réglages > Charger la démo** pour voir le dashboard rempli.

## Synchro automatique

Si tu utilises les skills Day Trader de ce repo, chaque exécution que
`/trader-broker` enregistre (`trader/state/broker.json`) arrive seule dans le
journal. Tu peux aussi déposer tes exports CSV dans `trader/journal/import/`.

```bash
python3 mindlock-journal/sync.py              # une synchro
python3 mindlock-journal/sync.py --watch 60   # en continu, toutes les 60 s
python3 mindlock-journal/sync.py --csv ~/Downloads/export.csv
```

Puis ouvre `trader/journal/MINDLOCK-Journal.html` : le dashboard contient déjà
tous tes trades. `trader/journal/trades.json` garde les données ; les notes,
tags, setups et notes d'exécution que tu y ajoutes sont conservés à chaque
synchro. Python 3.8+, rien à installer.

## Ce que fait le dashboard

| Vue | Contenu |
| --- | --- |
| Dashboard | P&L net, win % trades et jours, profit factor, gain moyen / perte moyenne, espérance, MINDLOCK Score (radar), courbe de P&L cumulé, P&L quotidien, calendrier du mois, trades récents, statistiques détaillées |
| Journal quotidien | Une fiche par jour : plan pré-marché, bilan et leçons, humeur, courbe intraday, trades du jour |
| Trades | Tableau triable et paginé, fiche détaillée par trade : setup, règles respectées, note sur 5, émotion, erreurs, tags, stop (pour le R), notes, capture d'écran, exécutions |
| Calendrier | Mois et année en couleur, total par semaine |
| Rapports | P&L par setup, symbole, jour, heure, sens, durée, tags, erreurs, émotion, note et mois |
| Playbook | Tes setups avec leurs règles et leurs vrais chiffres |
| Import & Réglages | Import CSV, devise, capital, multiplicateurs futures, liste d'erreurs, sauvegarde JSON, export CSV |

Filtres globaux : période, symbole, setup, long / short. Thème sombre et clair.
Fonctionne sur ordinateur et mobile.

### MINDLOCK Score

Une note sur 100 qui mesure la qualité de ton trading, pas seulement le P&L :

| Axe | Poids | 100 / 100 quand |
| --- | --- | --- |
| Win % | 15 | 60 % de trades gagnants |
| Profit factor | 25 | 2,5 |
| Gain / Perte | 20 | gain moyen = 2 x perte moyenne |
| Drawdown | 15 | aucun drawdown (10 % du capital = 0) |
| Constance | 10 | 70 % de jours verts |
| Discipline | 15 | plan respecté sur chaque trade |

## Imports reconnus

Les colonnes sont détectées automatiquement (anglais et français, `,` `;`
ou tabulation, décimales `.` ou `,`). Deux familles :

1. **Exécutions** (un achat ou une vente par ligne) : Interactive Brokers,
   Alpaca, TradeStation, Webull, thinkorswim, NinjaTrader... Les achats et
   ventes sont regroupés en trades (entrées multiples, sorties partielles et
   retournement long / short gérés).
2. **Trades clôturés** (ouverture et clôture sur la même ligne) : MT4 / MT5,
   cTrader, Tradovate (Performance), prop firms...

Format minimal :

```
Date,Symbol,Side,Qty,Price,Commission
2026-11-02 09:41:12,TSLA,BUY,10,364.20,1.00
2026-11-02 10:05:40,TSLA,SELL,10,369.85,1.00
```

Futures : la valeur du point est connue pour ES, MES, NQ, MNQ, YM, MYM, RTY,
M2K, CL, MCL, GC, MGC. Les autres se règlent dans Import & Réglages
(`SYMBOLE=valeur`) ou dans `trader/journal/config.json` :
`{"multipliers": {"NQ": 20}}`.

## Où sont mes données ?

- Dans le navigateur (stockage local) pour ce que tu saisis dans la page.
  Exporte une sauvegarde JSON de temps en temps (Import & Réglages).
- Dans `trader/journal/trades.json` pour ce que `sync.py` collecte.

Rien n'est envoyé sur internet. Ce journal n'est pas un conseil financier.
