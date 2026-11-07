# MaakCopyTrade

Votre propre copieur de trades, dans l'esprit de Tradesyncer, qui tourne **sur votre machine** :
un compte maitre, autant de comptes copieurs que vous voulez, des regles de risque de prop firm
sur chaque copieur et un dashboard complet avec statistiques.

Deux editions, un seul code :

| Edition | Pour | Connecteurs | Port |
|---|---|---|---|
| **Futures** | Prop firms futures (Topstep, Apex, MFFU, Take Profit Trader...) | Tradovate, ProjectX (TopstepX), simulateur | 8710 |
| **CFD** | Prop firms CFD / forex (FTMO, FundedNext, The5ers...) | MetaTrader 5, simulateur | 8720 |

Aucune dependance pour demarrer : Python 3.9+ et c'est tout (bibliotheque standard, SQLite, dashboard
sans librairie externe, fonctionne hors ligne).

## Demarrage rapide (mode demo)

```
cd maakcopytrade
python -m maakcopytrade futures --demo     # http://127.0.0.1:8710
python -m maakcopytrade cfd --demo         # http://127.0.0.1:8720
```

Sous Windows : double-cliquez `start_futures.bat` ou `start_cfd.bat` (ajoutez `--demo` la premiere fois
depuis une invite de commandes). Sous macOS / Linux : `./start_futures.sh --demo`.

`--demo` cree un compte maitre simule avec un petit bot qui trade tout seul, et deux copieurs simules
(par exemple Topstep 50K en x1 et Apex 50K en micros x10). Vous voyez la copie, la latence, les regles
de risque et les statistiques fonctionner tout de suite, sans courtier.

Options : `--port`, `--data DOSSIER` (base SQLite), `--tz America/New_York`, `--roll 18:00`
(heure de changement de journee de trading), `--poll 0.25` (frequence de lecture du maitre), `--no-browser`.

## Ce que fait le copieur

**Futures (mode position nette).** A chaque changement de position du maitre sur un contrat, chaque
copieur est amene a `arrondi(position maitre x multiplicateur)` sur son contrat. Cela gere les entrees,
renforts, sorties partielles, retournements et la mise a plat.
- Multiplicateur par compte (0.5x, 1x, 2x...) et sens inverse (hedge).
- Mapping de racines : `NQ=MNQ, ES=MES` (avec multiplicateur 10 pour copier des minis en micros).
- Contrats max par compte.
- Seuls les **changements** du maitre declenchent des ordres : un copieur que vous fermez a la main
  reste plat jusqu'au prochain mouvement du maitre.

**CFD (mode tickets).** Chaque ticket du maitre est reproduit : ouverture, fermeture partielle
proportionnelle, fermeture, et modification du SL / TP. La correspondance ticket maitre -> tickets
copieur est enregistree, un redemarrage ne duplique rien.
- Calcul des lots : multiplicateur, lot fixe, ou proportionnel a l'equity (copieur / maitre).
- Lot min, pas de lot, lot max, suffixe de symboles (`.r`, `.pro`...) et mapping (`US30=DJ30`).
- Copie du SL / TP optionnelle, sens inverse possible.

**Au demarrage de la copie**, les positions deja ouvertes sur le maitre ne sont pas copiees
(modifiable dans Parametres).

## Regles de risque (par compte)

Presets inclus (Topstep, Apex, MFFU, TPT, FTMO, FundedNext, The5ers) **a verifier** avec les regles
actuelles de votre firme, puis ajustables :

- Perte journaliere max : le compte est aplati et bloque jusqu'a la prochaine session.
- Drawdown max statique, trailing intraday ou trailing fin de journee, avec blocage du plancher au solde
  de depart (+ offset, ex. Apex +100) : aplati et bloque jusqu'a deblocage manuel.
- Objectif de profit avec arret optionnel.
- Marge de securite : agit X $ avant la vraie limite de la firme.
- Taille max par symbole, heure d'aplatissement automatique (ex. 15:55 heure de New York).

Le compte maitre est surveille mais jamais aplati automatiquement.

## Dashboard

- **Dashboard** : soldes, equity, P&L du jour, positions, comptes bloques, win rate et profit factor 30 j,
  une carte par compte avec jauges perte journaliere / drawdown / objectif, courbe d'equity, P&L journalier,
  dernieres executions avec latence.
- **Comptes** : ajout / modification / suppression, connexion, reglages de copie et de risque.
- **Positions** : toutes les positions ouvertes de tous les comptes.
- **Statistiques** (par compte, copieurs, maitres, ou tous ; aujourd'hui a 1 an) : P&L net, win rate,
  profit factor, esperance, gain / perte moyens, drawdown max, Sharpe, Sortino, jours gagnants, consistance
  (meilleur jour / total), series, duree moyenne, long / short, par symbole, par heure, par jour de semaine,
  calendrier mensuel.
- **Trades** : historique complet + export CSV. **Executions** : chaque ordre copie avec sa latence.
  **Journal** : connexions, alertes de risque, erreurs.

## Brancher vos vrais comptes

### Tradovate (edition Futures)
Il faut l'acces API Tradovate (cle API : `cid` + `secret`) sur l'utilisateur qui possede les comptes.
Dans Comptes > Ajouter : environnement `demo` ou `live`, utilisateur, mot de passe, cid, secret et le nom
exact du compte (ex. `APEX-12345-01`). Un compte par ligne : ajoutez votre maitre puis chaque copieur.

### ProjectX / TopstepX (edition Futures)
URL de la gateway de votre firme (ex. `https://api.topstepx.com`), nom d'utilisateur, cle API creee dans
la plateforme, et le nom ou l'id du compte.

### MetaTrader 5 (edition CFD, Windows)
```
pip install MetaTrader5
```
Le paquet officiel ne pilote qu'**un terminal par processus**, donc : installez un terminal MT5 par compte
(dossiers differents, copies portables) et renseignez pour chacun le chemin de son `terminal64.exe`,
le login, le mot de passe et le serveur. MaakCopyTrade lance un processus par compte. Activez
"Algo Trading" dans chaque terminal.

## Securite et limites

- Le serveur n'ecoute que sur 127.0.0.1. Les requetes POST d'une autre origine sont refusees.
- Les identifiants sont stockes en clair dans `data/futures.db` / `data/cfd.db` sur votre machine :
  protegez ce dossier, ne le partagez pas, ne le commitez pas (il est dans `.gitignore`).
- Copie par lecture periodique (polling, 250 ms par defaut) et ordres au marche : pas de copie des ordres
  limites / stops en attente. Testez d'abord sur des comptes demo / simules.
- Les API des courtiers evoluent : en cas d'erreur, regardez l'onglet Journal.
- Verifiez que votre prop firm autorise la copie de trades entre vos comptes (beaucoup l'autorisent sur
  vos propres comptes, certaines l'interdisent entre traders differents).

Logiciel fourni tel quel, sans garantie. Ce n'est pas un conseil financier.

## Tests

```
cd maakcopytrade
python -m unittest discover -s tests
```
