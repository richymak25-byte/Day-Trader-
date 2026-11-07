# MINDLOCK Journal

Ton journal de trading personnel, avec un dashboard complet, qui se remplit
automatiquement. Un seul fichier HTML, aucune inscription, aucune dépendance,
tes données restent sur ton appareil.

## Installation sur Windows (FTMO, FundedNext, FundingPips, Foxx Funded, Topstep, Lucid)

1. Installe **Python 3** depuis [python.org](https://www.python.org/downloads/windows/)
   en cochant **« Add python.exe to PATH »**.
2. Télécharge ce repo (bouton vert **Code > Download ZIP** sur GitHub) et dézippe-le.
3. Double-clique **`mindlock-journal\windows\Installer MINDLOCK.bat`**. Il :
   - installe le module MetaTrader5,
   - crée ta configuration `trader\journal\connectors.json`,
   - liste les terminaux MT5 trouvés sur ton PC et le compte ouvert dans chacun,
   - propose de lancer MINDLOCK au démarrage de Windows.
4. Dans le fichier qui s'ouvre, complète **Topstep** (`username` + `api_key` de
   TopstepX, Réglages > API) et **Lucid** (identifiants Tradovate + `cid` / `sec`
   de l'accès API Tradovate). Enregistre.
5. Double-clique **`mindlock-journal\windows\Lancer MINDLOCK.bat`** : le dashboard
   s'ouvre et se met à jour seul toutes les 60 secondes.

**MT5 (FTMO, FundedNext, FundingPips, Foxx Funded) : aucun mot de passe.** Garde
le terminal MT5 de chaque firm ouvert : le journal lit le compte connecté dedans.
Il ne change jamais de compte à ta place (ça te déconnecterait pendant que tu
trades). Tu as plusieurs comptes chez une firm ? Chacun se synchronise quand il
est le compte ouvert dans MT5, et ses trades restent ensuite dans le journal.
Si un terminal n'est pas trouvé, le journal ne prend jamais celui d'une autre
firm : lance `py mindlock-journal\sync.py --list-mt5` et copie son chemin dans `"path"`.

**Lucid (Tradovate)** : l'accès API est une option payante de Tradovate et
certaines prop firms le désactivent. Sans accès API, active le bloc
« Lucid exports » et dépose l'export *Performance* (CSV) de Tradovate dans
`~/MINDLOCK/exports/lucid` : il est importé tout seul.

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
python3 mindlock-journal/sync.py --serve      # en direct sur http://127.0.0.1:8765
python3 mindlock-journal/sync.py --csv ~/Downloads/export.csv
```

Puis ouvre `trader/journal/MINDLOCK-Journal.html` : le dashboard contient déjà
tous tes trades. `trader/journal/trades.json` garde les données ; les notes,
tags, setups et notes d'exécution que tu y ajoutes sont conservés à chaque
synchro. Python 3.8+, rien à installer.

## Brokers et prop firms connectés

`sync.py` lit directement l'historique de tes comptes via l'API officielle de
chaque plateforme. Les connecteurs sont **en lecture seule** : ils ne peuvent ni
passer, ni modifier, ni annuler un ordre.

1. Copie `mindlock-journal/connectors.example.json` vers `trader/journal/connectors.json`.
2. Garde les comptes que tu utilises, mets `"enabled": true` et renseigne tes accès.
   Conseil : écris les secrets sous la forme `"env:NOM_VARIABLE"` pour les lire
   dans une variable d'environnement plutôt que dans le fichier.
3. Lance le journal en direct :

```bash
python3 mindlock-journal/sync.py --serve
```

Ouvre <http://127.0.0.1:8765> : tes comptes sont relus toutes les 60 secondes
et les nouveaux trades apparaissent tout seuls dans la page, sans recharger.
Plusieurs comptes ? Le filtre **Compte** et le rapport **Par compte** les séparent.

| Type | Plateformes | Ce qu'il faut |
| --- | --- | --- |
| `projectx` | TopstepX et les prop firms sur ProjectX | identifiant + clé API (Réglages > API de la plateforme). Autre firm que Topstep : son adresse dans `base_url` |
| `tradovate` | Tradovate et les prop firms sur Tradovate (Apex, Tradeify, Take Profit Trader...) | identifiant, mot de passe, et l'accès API Tradovate (abonnement API, `cid` + `sec`). **Bêta** |
| `mt5` | MetaTrader 5 : FTMO, FundedNext, The5ers, E8, Alpha Capital, FundingPips... et tout broker MT5 | Windows + terminal MT5 + `pip install MetaTrader5`, numéro de compte, mot de passe, serveur |
| `ibkr` | Interactive Brokers | une Flex Query (Trades, niveau Execution) + le jeton Flex Web Service |
| `alpaca` | Alpaca (paper et réel) | clé API + secret |
| `oanda` | OANDA | jeton API v20 |
| `bybit` | Bybit et les prop firms crypto sur Bybit | clé API **en lecture seule** + secret |
| `binance` | Binance Futures USD-M | clé API **en lecture seule** + secret + liste des symboles |
| `folder` | tout le reste : NinjaTrader, Rithmic (R Trader Pro), cTrader, DXtrade, Match-Trader, MT4, Webull, thinkorswim, eToro... | un dossier (ex. `~/Downloads`) : chaque export CSV qui y arrive est importé seul |

À savoir :

- **Ta prop firm** : elle te donne une plateforme (ProjectX, Tradovate, Rithmic,
  MT5, cTrader...). Choisis le connecteur de cette plateforme. Certaines firms
  bloquent l'accès API sur les comptes d'évaluation : dans ce cas, utilise `folder`
  avec les exports CSV.
- **Interactive Brokers** met à jour les Flex Queries une fois par jour : les
  trades du jour arrivent le lendemain.
- **MT4** n'a pas d'API utilisable depuis Python : exporte l'historique (CSV) dans
  le dossier surveillé.
- Un connecteur en erreur (mot de passe, réseau) est signalé dans le terminal et
  n'empêche pas les autres de se synchroniser.
- Le dossier `trader/` (tes données et tes accès) est exclu de Git par `.gitignore`.
- Les connecteurs ont été testés avec des réponses d'API simulées. Avec ton
  premier vrai compte, vérifie que les premiers trades importés sont corrects.

### Modèle prêt pour FTMO, Foxx Funded, FundingPips, FundedNext, Topstep et Lucid

`connectors.prop-firms.example.json` contient un bloc par prop firm. Pour chaque
compte : numéro, mot de passe **investisseur** (lecture seule) et nom exact du
serveur tel qu'il apparaît dans l'e-mail d'identifiants ou la fenêtre de connexion
MT5. Plusieurs comptes chez la même firm : copie le bloc et change `name`
(ex. `"FTMO 100K #2"`). Comptes sur cTrader ou Match-Trader : exporte l'historique
en CSV dans le dossier `exports/cfd`. Les dates `05/11/2026` sont lues au format
français, sauf si le fichier prouve le contraire (réglable dans Import & Réglages).

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

## Sauvegarde automatique

À activer une seule fois : **Import & Réglages > Activer la sauvegarde automatique**,
puis enregistre le fichier dans `trader/journal/mindlock-backup.json`.

1. **Fichier sur ton disque**, réécrit tout seul environ une seconde après chaque
   modification (Chrome, Edge, Opera). Après un redémarrage du navigateur, il
   redemande l'accès : un clic sur le badge « Sauvegarde en pause » en bas à gauche.
2. **Copies de secours dans le navigateur** : une toutes les 30 minutes et une
   dès que des trades disparaissent. Toutes celles des dernières 24 h sont gardées,
   puis une par jour sur 14 jours. Chacune se restaure en un clic.
3. **Firefox et Safari** ne peuvent pas écrire de fichier : coche « Télécharger
   aussi une copie par jour » pour recevoir un fichier par jour dans tes Téléchargements.

Si le navigateur est vidé ou si tu changes d'ordinateur, lance `sync.py` :
il relit `mindlock-backup.json` et remet trades, notes, journal, playbook et
réglages dans le dashboard.

## Où sont mes données ?

- Dans le navigateur (stockage local) pour ce que tu saisis dans la page, et
  dans le fichier de sauvegarde automatique.
- Dans `trader/journal/trades.json` pour ce que `sync.py` collecte.

Rien n'est envoyé sur internet. Ce journal n'est pas un conseil financier.
