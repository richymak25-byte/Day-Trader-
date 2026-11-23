---
name: trader-autopilot
description: >-
  Paper Autopilot. Runs the whole day-trading chain unattended on Alpaca's
  PAPER account: every minute it checks your symbols for an opening-range
  breakout (trader-entry), sets the stop and target (trader-exit), replays
  the trade (trader-timemachine) and keeps it only if the target comes first
  more often than the stop, sizes it (trader-budget), runs trader-risk, and
  places the bracket order on the paper account. Syncs fills, never trades a
  symbol twice a day, and sells whatever is still open at flat_by (15:55).
  Paper only: the API host is fixed to paper-api.alpaca.markets. Use when
  the user says "automate my trading", "autopilot", "automatisation",
  "run it on its own", "trade for me on paper", "paper trade automatically",
  or "backtest live on paper".
argument-hint: "[status|run|loop]"
---

# trader-autopilot

The rest of the pack never places an order without the user's yes. The
autopilot is the one exception, and it buys that exception by never touching
real money: it talks only to `https://paper-api.alpaca.markets`, a constant
in the code with no flag, config key or environment variable to change it.
Its job is to let a strategy run for days or weeks on paper so the user can
judge it on real results before they ever risk a dollar.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/autopilot.py" status                 # rules, today's trades, safety OK or STOP
python3 "${CLAUDE_SKILL_DIR}/autopilot.py" run --dry-run          # one tick, the whole chain, nothing sent
python3 "${CLAUDE_SKILL_DIR}/autopilot.py" run                    # one tick on the paper account
python3 "${CLAUDE_SKILL_DIR}/autopilot.py" loop --every 60        # a tick a minute until the close
```

`--now "2026-10-19 10:20"` pins the clock (New York). `--offline DIR` reads
saved charts (`<SYM>.json` for today's 5-minute bars, `<SYM>-60d5m.json.gz`
for the time machine) and implies `--dry-run`.

## Setup

1. The user makes **paper trading** keys in the Alpaca dashboard and exports
   them in their own shell, never in the chat and never in a file in the repo:
   `export ALPACA_API_KEY=...` and `export ALPACA_SECRET_KEY=...`
   (`APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` work too).
2. `trader/config.json` (start from `trader-budget/config.example.json`)
   keeps `"paper": true` and gets the `autopilot` block from
   `autopilot.example.json` here. It is off until the user sets `enabled`:

```json
"autopilot": {
  "enabled": true,
  "symbols": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "META", "AMZN", "GOOGL", "TSLA"],
  "setup": "orb", "or_minutes": 15, "interval": "5m",
  "stop": "or-mid", "target": "2R",
  "min_edge": 0.0, "min_hist_trades": 0, "min_hist_avg_r": null,
  "replays": 2000, "max_trigger_age_min": 10, "max_entries_per_tick": 1
}
```

3. `autopilot.py status` must print `safety: OK`. Then `run --dry-run`.
4. Run `loop` on a computer that stays on during market hours (or a cron
   line that calls `run` every minute from 9:45 to 16:00 New York).

## One tick

1. **Safety.** Stops for good (exit 2) unless `paper` is true, `broker` is
   alpaca, the autopilot is enabled, short, margin and options are off, a
   max loss per trade and a daily loss limit are set, the keys are in the
   environment and Alpaca's account number starts with `PA` (paper).
2. **Clock.** Alpaca's market clock; closed means nothing to do.
3. **Sync.** Today's filled orders that the autopilot placed (the entry and
   its stop-loss / take-profit legs) are recorded once with `ticket.py fill`,
   so `trader/portfolio.json` and trader-risk see real cash.
4. **Out by the close.** At or after `flat_by`: for every autopilot symbol
   still held, cancel its bracket legs, write the sell with
   `ticket.py flatten`, pass it through trader-risk and sell at market.
5. **Hunt.** Before `flat_by`, for each symbol not held and not traded today,
   until `max_trades_per_day`:
   - `entry.py check`: triggered, and no older than `max_trigger_age_min`.
   - `exit.py plan`: the stop and the target (refusals skip the symbol).
   - `timemachine.py run`: `p_target - p_stop >= min_edge`, the history has
     at least `min_hist_trades` past trades, and (if set) their average R is
     at least `min_hist_avg_r`.
   - `size.py --write`: one bracket order inside the max loss.
   - `check.py`: PASS, or nothing is sent.
   - Place it on the paper account with a `client_order_id` of
     `autopilot-<date>-<fingerprint>-entry` (Alpaca rejects a duplicate), and
     record it in `trader/state/broker.json` (approved by "autopilot", then
     placed, with its max loss) so trader-risk keeps counting the day's
     trades and loss.

Every decision (placed, skip and why, blocked, fill, error) is appended to
`trader/state/autopilot.jsonl`. A failed order is not retried that day.

## What to tell the user

- It is paper money. Results on paper ignore some slippage and every
  emotion; treat a good paper month as a reason to keep testing, not proof.
- Read the journal with them: how many trades, how many hit the target, the
  stop, the close, and the average R. Compare with what the time machine
  predicted.
- Ctrl+C stops the loop. Open paper positions keep their bracket stop and
  target at Alpaca; anything still open after the close is the user's to close.

## What it never does

- Never trades a live account. There is no switch for it. If the user wants
  live trading, the order goes through `/trader-broker` and their yes, one
  order at a time.
- Never asks for, reads, stores or repeats API keys.
- Never shorts, uses margin, options or crypto.
- Never trades a symbol twice in one day or chases a breakout older than
  `max_trigger_age_min`.
- Never skips trader-risk: a BLOCK means nothing is sent.

Not financial advice. Most people who day trade lose money; the autopilot
exists so the user can find out on paper, without a yes per order, whether
this strategy is one of the exceptions.
