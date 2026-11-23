//+------------------------------------------------------------------+
//| MindlockBridge.mq5                                               |
//| Exécute dans MT5 les signaux de MINDLOCK EMA CORE TREND (mode    |
//| webhook) relayés par le serveur mindlock-bridge sur Render.      |
//|                                                                  |
//| TradingView -> POST /webhook/<TOKEN> -> Render                   |
//| cet EA      -> GET  /signals/<TOKEN>?after=N (toutes les X ms)   |
//|                                                                  |
//| Actions : buy, sell (entrée au marché avec SL/TP), close,        |
//| exit (synchronise une sortie TP/SL/BE vue sur TradingView),      |
//| modify_sl (SL ajusté, break-even).                               |
//|                                                                  |
//| Outils > Options > Expert Advisors : cocher "Autoriser           |
//| WebRequest pour les URL listées" et ajouter l'URL Render.        |
//+------------------------------------------------------------------+
#property copyright "MINDLOCK"
#property version   "1.00"
#property description "Pont TradingView -> Render -> MT5 pour MINDLOCK EMA CORE TREND"

#include <Trade/Trade.mqh>

enum ENUM_LOT_MODE
  {
   LOT_RISK_PERCENT = 0, // % du capital risqué sur le SL
   LOT_FIXED        = 1  // lot fixe
  };

input group "1. Pont webhook"
input string InpBridgeUrl        = "https://mindlock-bridge.onrender.com"; // URL du serveur Render (sans / final)
input string InpToken            = "";      // BRIDGE_TOKEN (le même que sur Render)
input int    InpPollMs           = 1000;    // Interrogation toutes les X ms
input int    InpMaxSignalAgeSec  = 60;      // Ignorer un signal plus vieux que X secondes

input group "2. Symboles"
input string InpSymbolSuffix     = "";      // Suffixe MT5 (ex: ".a"), vide si aucun
input string InpSymbolMap        = "";      // Correspondances TV=MT5, ex: NAS100=USTEC,US30=US30
input string InpOnlyTf           = "";      // Ne traiter que ce timeframe (si le JSON contient "tf"), vide = tous

input group "3. Taille de position"
input ENUM_LOT_MODE InpLotMode   = LOT_RISK_PERCENT;
input double InpRiskPercent      = 0.5;     // % du capital risqué par trade
input double InpFixedLots        = 0.01;    // Lot fixe
input double InpMaxLots          = 1.0;     // Lot maximum, quoi qu'il arrive
input bool   InpSkipBelowMinLot  = true;    // Si le risque donne moins que le lot mini : ne pas trader

input group "4. Exécution"
input long   InpMagic            = 250504;  // Magic number
input int    InpDeviationPoints  = 30;      // Glissement max (points)
input int    InpMaxSpreadPoints  = 0;       // Spread max pour entrer (points), 0 = pas de limite
input bool   InpKeepDistances    = true;    // Garder les distances SL/TP de TV autour du vrai prix d'exécution
input bool   InpCloseOpposite    = true;    // Une entrée ferme d'abord la position inverse
input bool   InpOnePerSymbol     = true;    // Une seule position par symbole et par sens
input bool   InpSyncExits        = true;    // Fermer quand TradingView sort (TP/SL/BE vus sur TV)

input group "5. Sécurité"
input bool   InpAllowRealAccount = false;   // Autoriser un compte RÉEL (démo d'abord !)
input double InpDailyLossPct     = 2.0;     // Daily Loss Lock (% réalisé), 0 = off
input double InpDailyProfitPct   = 4.0;     // Daily Profit Lock (% réalisé), 0 = off
input int    InpMaxTradesPerDay  = 10;      // Entrées max par jour, 0 = pas de limite

CTrade   g_trade;
long     g_lastId   = 0;
long     g_boot     = 0;
datetime g_lastErr  = 0;
string   g_status   = "démarrage";
int      g_handled  = 0;

string GvLast() { return "MLB_" + IntegerToString(InpMagic) + "_last"; }
string GvBoot() { return "MLB_" + IntegerToString(InpMagic) + "_boot"; }

void SaveState()
  {
   GlobalVariableSet(GvLast(), (double)g_lastId);
   GlobalVariableSet(GvBoot(), (double)g_boot);
  }

void Log(const string msg)
  {
   Print("[MindlockBridge] ", msg);
  }

void LogThrottled(const string msg)
  {
   if(TimeLocal() - g_lastErr >= 60)
     {
      Log(msg);
      g_lastErr = TimeLocal();
     }
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   if(StringLen(InpToken) < 16)
     {
      Alert("MindlockBridge : renseigne InpToken (BRIDGE_TOKEN, 16 caractères minimum).");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(StringFind(InpBridgeUrl, "https://") != 0)
     {
      Alert("MindlockBridge : InpBridgeUrl doit commencer par https://");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL && !InpAllowRealAccount)
     {
      Alert("MindlockBridge : compte RÉEL détecté. Teste d'abord sur démo, puis passe InpAllowRealAccount à true.");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(GlobalVariableCheck(GvLast()))
      g_lastId = (long)GlobalVariableGet(GvLast());
   if(GlobalVariableCheck(GvBoot()))
      g_boot = (long)GlobalVariableGet(GvBoot());

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpDeviationPoints);
   g_trade.SetAsyncMode(false);

   EventSetMillisecondTimer(MathMax(250, InpPollMs));
   Log(StringFormat("prêt. Compte %s (%s), dernier signal %I64d",
                    IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)),
                    AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL ? "RÉEL" : "démo",
                    g_lastId));
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   Comment("");
  }

void OnTimer()
  {
   Poll();
   Comment(StringFormat("MINDLOCK BRIDGE\n%s\nsignaux traités : %d | dernier id : %I64d\n%s",
                        g_status, g_handled, g_lastId, DailyLockText()));
  }

//+------------------------------------------------------------------+
//| Interrogation du serveur                                         |
//+------------------------------------------------------------------+
void Poll()
  {
   string url = InpBridgeUrl + "/signals/" + InpToken + "?after=" + IntegerToString(g_lastId);
   char   post[];
   char   result[];
   string headers;
   ResetLastError();
   int code = WebRequest("GET", url, "", 5000, post, result, headers);
   if(code == -1)
     {
      int err = GetLastError();
      g_status = "serveur injoignable (" + IntegerToString(err) + ")";
      if(err == 4014)
         LogThrottled("WebRequest refusé : ajoute " + InpBridgeUrl + " dans Outils > Options > Expert Advisors.");
      else
         LogThrottled("WebRequest erreur " + IntegerToString(err));
      return;
     }
   if(code != 200)
     {
      g_status = "serveur HTTP " + IntegerToString(code);
      LogThrottled("le serveur répond HTTP " + IntegerToString(code) + " (token ou URL ?)");
      return;
     }
   string text = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   string lines[];
   int n = StringSplit(text, '\n', lines);
   if(n < 1)
      return;
   string h[];
   if(StringSplit(lines[0], '|', h) < 4 || h[0] != "OK")
     {
      g_status = "réponse inattendue du serveur";
      return;
     }
   long   boot  = StringToInteger(h[1]);
   double now   = StringToDouble(h[2]);
   long   last  = StringToInteger(h[3]);
   g_status = "connecté " + TimeToString(TimeLocal(), TIME_SECONDS);

   if(boot != g_boot)
     {
      bool firstRun = (g_boot == 0);
      g_boot = boot;
      // premier lancement : on ignore l'historique ; redémarrage du serveur : on repart de 0
      g_lastId = firstRun ? last : 0;
      SaveState();
      Log(firstRun ? "premier contact avec le serveur, historique ignoré"
                   : "le serveur a redémarré, file repartie de zéro");
      return;
     }

   for(int i = 1; i < n; i++)
     {
      if(StringLen(lines[i]) < 3)
         continue;
      string f[];
      if(StringSplit(lines[i], '|', f) < 11)
         continue;
      long id = StringToInteger(f[0]);
      if(id <= g_lastId)
         continue;
      // on enregistre AVANT d'exécuter : un signal n'est jamais exécuté deux fois
      g_lastId = id;
      SaveState();
      double age = now - StringToDouble(f[1]);
      if(age > InpMaxSignalAgeSec)
        {
         Log(StringFormat("signal %I64d ignoré : vieux de %.0f s (%s %s %s)", id, age, f[2], f[3], f[4]));
         continue;
        }
      g_handled++;
      Handle(id, f);
     }
  }

//+------------------------------------------------------------------+
//| Un signal                                                        |
//| f: id|t|action|side|ticker|qty|price|sl|tp|reason|tf             |
//+------------------------------------------------------------------+
void Handle(const long id, string &f[])
  {
   string action = f[2];
   string side   = f[3];
   string ticker = f[4];
   double price  = StringToDouble(f[6]);
   double sl     = StringToDouble(f[7]);
   double tp     = StringToDouble(f[8]);
   string reason = f[9];
   string tf     = f[10];

   if(InpOnlyTf != "" && tf != "" && tf != InpOnlyTf)
      return;
   string sym = MapSymbol(ticker);
   if(!SymbolSelect(sym, true))
     {
      Log(StringFormat("signal %I64d : symbole %s introuvable dans MT5 (vérifie InpSymbolSuffix / InpSymbolMap)", id, sym));
      return;
     }
   Log(StringFormat("signal %I64d : %s %s %s prix=%s sl=%s tp=%s %s", id, action, side, sym, f[6], f[7], f[8], reason));

   if(action == "buy" || action == "sell")
      Enter(id, action == "buy", sym, price, sl, tp, tf);
   else
      if(action == "close")
         CloseMatching(sym, side, tf, "close " + reason);
      else
         if(action == "exit")
           {
            if(InpSyncExits)
               CloseMatching(sym, side, tf, "exit " + reason);
           }
         else
            if(action == "modify_sl")
               ModifyMatching(sym, side, tf, price, sl, tp, reason);
  }

string MapSymbol(const string ticker)
  {
   if(InpSymbolMap != "")
     {
      string pairs[];
      int n = StringSplit(InpSymbolMap, ',', pairs);
      for(int i = 0; i < n; i++)
        {
         string kv[];
         if(StringSplit(pairs[i], '=', kv) == 2)
           {
            string k = kv[0];
            string v = kv[1];
            StringTrimLeft(k);
            StringTrimRight(k);
            StringTrimLeft(v);
            StringTrimRight(v);
            if(k == ticker)
               return v;
           }
        }
     }
   return ticker + InpSymbolSuffix;
  }

string TfComment(const string tf)
  {
   return "ML|" + tf;
  }

bool PositionMatches(const string sym, const string side, const string tf)
  {
   if(PositionGetString(POSITION_SYMBOL) != sym)
      return false;
   if(PositionGetInteger(POSITION_MAGIC) != InpMagic)
      return false;
   long type = PositionGetInteger(POSITION_TYPE);
   if(side == "buy" && type != POSITION_TYPE_BUY)
      return false;
   if(side == "sell" && type != POSITION_TYPE_SELL)
      return false;
   if(tf != "" && StringFind(PositionGetString(POSITION_COMMENT), TfComment(tf)) != 0)
      return false;
   return true;
  }

int CountMatching(const string sym, const string side, const string tf)
  {
   int c = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionMatches(sym, side, tf))
         c++;
     }
   return c;
  }

void CloseMatching(const string sym, const string side, const string tf, const string why)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionMatches(sym, side, tf))
         continue;
      if(g_trade.PositionClose(ticket, InpDeviationPoints))
         Log(StringFormat("position %I64u %s fermée (%s)", ticket, sym, why));
      else
         Log(StringFormat("échec fermeture %I64u %s : %d %s", ticket, sym,
                          g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription()));
     }
  }

//+------------------------------------------------------------------+
//| Entrée au marché                                                 |
//+------------------------------------------------------------------+
void Enter(const long id, const bool isBuy, const string sym, const double tvPrice,
           double sl, double tp, const string tf)
  {
   string lockWhy;
   if(DailyLocked(lockWhy))
     {
      Log("entrée refusée : " + lockWhy);
      return;
     }
   if(InpMaxSpreadPoints > 0 && SymbolInfoInteger(sym, SYMBOL_SPREAD) > InpMaxSpreadPoints)
     {
      Log(StringFormat("entrée refusée : spread %d > %d points", (int)SymbolInfoInteger(sym, SYMBOL_SPREAD), InpMaxSpreadPoints));
      return;
     }
   if(InpCloseOpposite)
      CloseMatching(sym, isBuy ? "sell" : "buy", tf, "signal opposé");
   if(InpOnePerSymbol && CountMatching(sym, isBuy ? "buy" : "sell", tf) > 0)
     {
      Log("entrée ignorée : une position dans ce sens est déjà ouverte sur " + sym);
      return;
     }

   MqlTick tick;
   if(!SymbolInfoTick(sym, tick))
     {
      Log("pas de cotation pour " + sym);
      return;
     }
   double entry = isBuy ? tick.ask : tick.bid;
   if(InpKeepDistances && tvPrice > 0)
     {
      double slDist = MathAbs(tvPrice - sl);
      double tpDist = (tp > 0) ? MathAbs(tp - tvPrice) : 0;
      sl = isBuy ? entry - slDist : entry + slDist;
      tp = (tpDist > 0) ? (isBuy ? entry + tpDist : entry - tpDist) : 0;
     }
   sl = NormPrice(sym, sl);
   tp = (tp > 0) ? NormPrice(sym, tp) : 0;

   // SL / TP du bon côté et assez loin (stops level du courtier)
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   double minDist = (double)SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL) * point;
   bool ok = isBuy ? (sl < tick.bid - minDist && (tp == 0 || tp > tick.ask + minDist))
                   : (sl > tick.ask + minDist && (tp == 0 || tp < tick.bid - minDist));
   if(sl <= 0 || !ok)
     {
      Log(StringFormat("entrée refusée : SL %.5f / TP %.5f invalides au prix actuel %.5f", sl, tp, entry));
      return;
     }

   double lots = CalcLots(sym, isBuy, entry, sl);
   if(lots <= 0)
      return;

   g_trade.SetTypeFillingBySymbol(sym);
   string comment = TfComment(tf);
   bool sent = isBuy ? g_trade.Buy(lots, sym, 0, sl, tp, comment)
                     : g_trade.Sell(lots, sym, 0, sl, tp, comment);
   uint rc = g_trade.ResultRetcode();
   if(sent && (rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_PLACED || rc == TRADE_RETCODE_DONE_PARTIAL))
      Log(StringFormat("%s %.2f %s @ %.5f SL %.5f TP %.5f (signal %I64d)", isBuy ? "BUY" : "SELL",
                       lots, sym, g_trade.ResultPrice(), sl, tp, id));
   else
      Log(StringFormat("ordre refusé %s %s : %d %s", isBuy ? "BUY" : "SELL", sym, rc,
                       g_trade.ResultRetcodeDescription()));
  }

double NormPrice(const string sym, const double p)
  {
   double ts = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   if(ts > 0)
      return NormalizeDouble(MathRound(p / ts) * ts, digits);
   return NormalizeDouble(p, digits);
  }

double CalcLots(const string sym, const bool isBuy, const double entry, const double sl)
  {
   double minLot = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double step   = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   double lots   = InpFixedLots;

   if(InpLotMode == LOT_RISK_PERCENT)
     {
      double risk = AccountInfoDouble(ACCOUNT_EQUITY) * InpRiskPercent / 100.0;
      double pnl  = 0;
      if(!OrderCalcProfit(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, sym, 1.0, entry, sl, pnl) || pnl == 0)
        {
         Log("impossible de calculer la perte au SL pour " + sym);
         return 0;
        }
      lots = risk / MathAbs(pnl);
     }
   lots = MathMin(lots, InpMaxLots);
   if(step > 0)
      lots = MathFloor(lots / step + 1e-9) * step;
   if(lots < minLot)
     {
      if(InpSkipBelowMinLot && InpLotMode == LOT_RISK_PERCENT)
        {
         Log(StringFormat("entrée refusée : le risque de %.2f %% donne %.4f lot, sous le minimum %.2f",
                          InpRiskPercent, lots, minLot));
         return 0;
        }
      lots = minLot;
     }
   lots = MathMin(lots, maxLot);
   return NormalizeDouble(lots, 8);
  }

//+------------------------------------------------------------------+
//| SL ajusté / break-even                                           |
//+------------------------------------------------------------------+
void ModifyMatching(const string sym, const string side, const string tf, const double tvPrice,
                    const double tvSl, const double tvTp, const string reason)
  {
   MqlTick tick;
   if(!SymbolInfoTick(sym, tick))
      return;
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   double minDist = (double)SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL) * point;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionMatches(sym, side, tf))
         continue;
      bool   isBuy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      double open  = PositionGetDouble(POSITION_PRICE_OPEN);
      double curTp = PositionGetDouble(POSITION_TP);
      double newSl = tvSl;
      double newTp = curTp;

      if(reason == "break_even")
         newSl = open;                                // BE sur le VRAI prix d'entrée MT5
      else
         if(InpKeepDistances && tvPrice > 0)
           {
            newSl = open + (tvSl - tvPrice);          // même distance que sur TradingView
            if(tvTp > 0)
               newTp = open + (tvTp - tvPrice);
           }
         else
            if(tvTp > 0)
               newTp = tvTp;

      newSl = NormPrice(sym, newSl);
      newTp = (newTp > 0) ? NormPrice(sym, newTp) : 0;
      bool ok = isBuy ? (newSl < tick.bid - minDist) : (newSl > tick.ask + minDist);
      if(!ok)
        {
         Log(StringFormat("SL %.5f non appliqué sur %I64u : trop près ou au-delà du prix", newSl, ticket));
         continue;
        }
      if(MathAbs(newSl - PositionGetDouble(POSITION_SL)) < point && MathAbs(newTp - curTp) < point)
         continue;
      if(g_trade.PositionModify(ticket, newSl, newTp))
         Log(StringFormat("position %I64u : SL %.5f TP %.5f (%s)", ticket, newSl, newTp, reason));
      else
         Log(StringFormat("échec modification %I64u : %d %s", ticket, g_trade.ResultRetcode(),
                          g_trade.ResultRetcodeDescription()));
     }
  }

//+------------------------------------------------------------------+
//| Daily Lock sur le compte réel/démo (résultat réalisé du jour)    |
//+------------------------------------------------------------------+
datetime DayStart()
  {
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   dt.hour = 0;
   dt.min = 0;
   dt.sec = 0;
   return StructToTime(dt);
  }

void TodayStats(double &realized, int &entries)
  {
   realized = 0;
   entries = 0;
   if(!HistorySelect(DayStart(), TimeCurrent() + 60))
      return;
   int n = HistoryDealsTotal();
   for(int i = 0; i < n; i++)
     {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0 || HistoryDealGetInteger(d, DEAL_MAGIC) != InpMagic)
         continue;
      long entryType = HistoryDealGetInteger(d, DEAL_ENTRY);
      if(entryType == DEAL_ENTRY_IN)
         entries++;
      realized += HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP)
                  + HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
     }
  }

bool DailyLocked(string &why)
  {
   double realized;
   int entries;
   TodayStats(realized, entries);
   double startBal = AccountInfoDouble(ACCOUNT_BALANCE) - realized;
   double pct = (startBal > 0) ? realized / startBal * 100.0 : 0;
   if(InpDailyLossPct > 0 && pct <= -InpDailyLossPct)
     {
      why = StringFormat("Daily Loss Lock (%.2f %% aujourd'hui)", pct);
      return true;
     }
   if(InpDailyProfitPct > 0 && pct >= InpDailyProfitPct)
     {
      why = StringFormat("Daily Profit Lock (+%.2f %% aujourd'hui)", pct);
      return true;
     }
   if(InpMaxTradesPerDay > 0 && entries >= InpMaxTradesPerDay)
     {
      why = StringFormat("%d entrées aujourd'hui (max %d)", entries, InpMaxTradesPerDay);
      return true;
     }
   return false;
  }

string DailyLockText()
  {
   string why;
   if(DailyLocked(why))
      return "⛔ " + why;
   return "Daily Lock : libre";
  }
//+------------------------------------------------------------------+
