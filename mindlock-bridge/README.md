# MINDLOCK Bridge : TradingView → Render → MT5

Automatise **MINDLOCK EMA CORE TREND V.4** (mode webhook) sur ton compte MT5
Fusion Markets.

```
TradingView (alerte stratégie, JSON)
   │  POST https://<ton-service>.onrender.com/webhook/<TOKEN>
   ▼
Render : server.py (file de signaux en mémoire)
   ▲  GET /signals/<TOKEN>?after=N   (toutes les 1 s)
   │
MT5 sur ton PC ou un VPS : MindlockBridge.mq5 (exécute chez Fusion Markets)
```

MT5 n'a pas d'API web. C'est donc l'EA qui va chercher les signaux sur
Render, et pas Render qui pousse vers MT5. Il n'y a aucun port à ouvrir chez toi.

## Ce que fait chaque message

| JSON envoyé par le script | Ce que fait l'EA |
| --- | --- |
| `buy` / `sell` avec `price`, `sl`, `tp` | Ferme la position inverse, puis entre au marché. Le lot est calculé sur le SL (% de risque) et le SL/TP est posé chez le courtier. |
| `close` (`signal_oppose`, `fin_de_session`, `all`) | Ferme les positions du sens indiqué. |
| `exit` (`tp`, `sl`, `be`) | Si TradingView est sorti, l'EA ferme aussi (option `InpSyncExits`). |
| `modify_sl` `sl_ajuste` | Applique le SL structurel final, à la même distance du **vrai** prix d'entrée MT5. |
| `modify_sl` `break_even` | Met le SL au **vrai** prix d'entrée MT5. |

À savoir : en mode LIMIT (50 % bougie, EMA, OTE...), TradingView n'envoie le
`buy`/`sell` qu'**au moment où sa limite est touchée**. L'EA entre donc au
marché à ce moment-là, c'est-à-dire au niveau de ta limite, au spread près.

## 1. Render (le serveur)

1. Sur render.com : **New > Blueprint**, choisis ce dépôt, Blueprint path
   `mindlock-bridge/render.yaml`. Autre option : **New > Web Service** avec
   Root Directory `mindlock-bridge`, Build `pip install -r requirements.txt`
   et Start `python3 server.py`.
2. Variable d'environnement `BRIDGE_TOKEN` : un secret long et aléatoire. Le
   Blueprint en génère un. Ce secret va dans l'URL du webhook et dans l'EA :
   ne le partage pas.
3. Vérifie que `https://<ton-service>.onrender.com/health` répond `ok`.

**Plan gratuit ou payant.** Une instance gratuite s'endort après 15 min sans
requête. Tant que l'EA tourne, il l'interroge chaque seconde et la garde
éveillée. Si MT5 s'arrête, le premier webhook peut arriver pendant le réveil
(environ 30 à 60 s) et TradingView peut abandonner. Le plan **Starter** évite
ce problème. Les signaux sont gardés en mémoire : un redéploiement vide la
file, et l'EA le détecte tout seul.

## 2. TradingView (les alertes)

Il faut un abonnement payant (webhooks) et la double authentification activée.

1. Dans les paramètres du script, section **7. Webhook** : coche **Mode
   webhook**.
2. Crée l'alerte :
   - Condition : `MINDLOCK TREND V.4`, puis **« Exécutions d'ordres et appels
     de la fonction alert() »** (les deux : les `modify_sl` passent par `alert()`).
   - Message : exactement `{{strategy.order.alert_message}}`
   - Notifications > **URL du webhook** : `https://<ton-service>.onrender.com/webhook/<BRIDGE_TOKEN>`
3. Garde une deuxième alerte, sans webhook et avec le mode webhook décoché,
   si tu veux continuer à recevoir les messages lisibles sur ton téléphone.

**Plusieurs timeframes sur le même actif** (ex. XAUUSD en 5 et 30 min) :
ajoute le timeframe au JSON pour que l'EA sépare les positions. Dans
`f_json`, remplace la dernière ligne `j + '}'` par :

```pine
    j + ',"tf":"' + timeframe.period + '"}'
```

L'EA marque alors chaque position `ML|5`, `ML|30`... et peut se limiter à
un seul timeframe (`InpOnlyTf`).

## 3. MT5 (l'EA)

1. Copie `MindlockBridge.mq5` dans `Fichier > Ouvrir le dossier des données >
   MQL5 > Experts`, ouvre-le dans MetaEditor et compile (F7).
2. **Outils > Options > Expert Advisors** : coche *Autoriser le trading
   algorithmique* et *Autoriser WebRequest pour les URL listées*, puis ajoute
   `https://<ton-service>.onrender.com`.
3. Glisse l'EA sur **un seul** graphique (n'importe lequel : il trade tous
   les symboles reçus). Renseigne :
   - `InpBridgeUrl` et `InpToken` ;
   - `InpSymbolSuffix` / `InpSymbolMap` si les noms MT5 diffèrent de
     TradingView (ex. `NAS100=USTEC`). Compare avec ton Market Watch ;
   - `InpRiskPercent` (0,5 % par défaut) et `InpMaxLots` ;
   - les locks journaliers −2 % / +4 % (sur le **vrai** compte, en plus de
     celui de TradingView) et `InpMaxTradesPerDay`.
4. **Démo d'abord.** L'EA refuse de démarrer sur un compte réel tant que
   `InpAllowRealAccount` est à `false`.

Le coin du graphique affiche l'état (connecté, signaux traités, Daily Lock),
et l'onglet *Experts* montre chaque décision.

## 4. PC ou VPS

L'EA ne trade que si MT5 est ouvert et connecté. Ton PC suffit pour tester.
Pour du 24 h/24 et 5 j/7, prends un VPS Windows proche des serveurs de Fusion
Markets, ou l'hébergement virtuel MetaQuotes : vérifie dans ce cas que
WebRequest y est bien autorisé pour ton URL.

## Garde-fous

- Le jeton est vérifié à temps constant. Une mauvaise URL reçoit un 404, et
  les logs Render n'affichent ni le jeton ni le corps des requêtes.
- Signal mal formé (SL du mauvais côté, champ inconnu...) : refusé par le serveur.
- Doublon envoyé par TradingView en moins de 5 s : ignoré.
- Signal plus vieux que `InpMaxSignalAgeSec` (60 s) : jamais exécuté en retard.
- Chaque signal est exécuté au plus une fois : l'EA enregistre son id avant
  d'agir, même après un redémarrage de MT5.
- SL/TP invalides au prix actuel, lot sous le minimum ou spread trop large :
  pas d'entrée, avec la raison dans le journal.

Ce n'est pas un conseil financier. Teste plusieurs semaines en démo et
compare chaque trade MT5 avec le trade TradingView avant de passer en réel.
