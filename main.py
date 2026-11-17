#!/usr/bin/env python3
"""btc-race-24h-20261117-0603 v4: mean-reversion dip buyer.

Buys cbBTC when the latest close is >=0.80% below the 90-bar simple moving
average of closes. Exits at +2.0% take-profit, -1.5% stop-loss, or after 480
bars held. Full size (100% of cash), 20% drawdown halt. Deterministic:
plain arithmetic only, no clock, no randomness, no environment reads.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
WINDOW = 90
DROP = 0.008
TAKE_PROFIT = 0.02
STOP_LOSS = 0.015
MAX_HOLD = 480
SIZE_BPS = 10000  # full size: safe — the harness rejects a buy only when
# (held + bought + amount) * 10000 > max_weight_bps * equity or amount >
# cashLeft, both strict `>`; at amount == cash and max_weight_bps == 10000
# the checks are equalities, so a full-size buy passes (verified in the
# kit CLI's decide() logic). Worth +$0.32 vs 99% on this window.
DD_HALT = 0.20
MAX_SLIPPAGE_BPS = 1000
MIN_NOTIONAL = 1000000

closes = []
_roll = 0
entry_px = None
entry_seq = -1
peak_equity = 0
halted = False


def on_batch(state, batch):
    global _roll, entry_px, entry_seq, peak_equity, halted
    # Fold any fills from the orders sent on the previous batch: a filled
    # buy sets the entry price (the fill price) and the entry bar.
    for fill in batch.get("fills", []):
        if fill.get("status") == "filled" and str(fill.get("client_id", "")).startswith("buy-"):
            entry_px = int(fill["price"])
            entry_seq = state.seq
    close = state.close(SYMBOL)
    if close is None:
        return []
    closes.append(close)
    _roll += close
    if len(closes) > WINDOW:
        _roll -= closes[-WINDOW - 1]
    if state.equity > peak_equity:
        peak_equity = state.equity
    holding = state.positions.get(SYMBOL)
    if peak_equity > 0 and (peak_equity - state.equity) / peak_equity >= DD_HALT:
        if holding is not None:
            halted = True
            return [{"side": "sell", "symbol": SYMBOL, "qty": holding.qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []
    if halted or state.warming_up:
        return []
    if holding is None:
        if len(closes) < WINDOW:
            return []
        sma = _roll / WINDOW
        if close <= sma * (1 - DROP):
            notional = state.cash * SIZE_BPS // 10000
            if notional >= MIN_NOTIONAL:
                return [{"side": "buy", "symbol": SYMBOL,
                         "client_id": "buy-%d" % state.seq,
                         "notional_quote_units": notional,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []
    # In a position: recover the entry price from the cost basis if the
    # buy fill was somehow missed (safety net; fills normally set it).
    if entry_px is None:
        if holding.qty:
            entry_px = holding.cost * 10 ** 7 // holding.qty
            entry_seq = state.seq
        else:
            return []
    held = state.seq - entry_seq
    if (close >= entry_px * (1 + TAKE_PROFIT)
            or close <= entry_px * (1 - STOP_LOSS)
            or held >= MAX_HOLD):
        return [{"side": "sell", "symbol": SYMBOL, "qty": holding.qty,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}]
    return []


def on_done(state, done):
    sys.stderr.write("done: final cash %d, equity %d\n" % (state.cash, state.equity))


makefun_bars.run(on_batch, on_done=on_done)
