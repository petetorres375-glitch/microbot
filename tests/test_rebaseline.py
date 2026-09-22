"""
Tests for microbot/rebaseline.py's pure R-stats aggregation.
No network, no DB — feeds strategy_r_stats() made-up trade dicts.

Run: python -m pytest tests/test_rebaseline.py -q
"""
from __future__ import annotations

from microbot.rebaseline import strategy_r_stats


def _trade(r):
    return {"r_multiple": r}


def test_empty_trades():
    stats = strategy_r_stats([])
    assert stats == {"trades": 0, "win_rate": 0.0, "avg_win_r": 0.0,
                      "avg_loss_r": 0.0, "expectancy_r": 0.0,
                      "worst_trade_r": 0.0, "tail_avg_r": 0.0}


def test_all_winners():
    trades = [_trade(1.0), _trade(2.0)]
    stats = strategy_r_stats(trades)
    assert stats["trades"] == 2
    assert stats["win_rate"] == 1.0
    assert stats["avg_win_r"] == 1.5
    assert stats["avg_loss_r"] == 0.0
    assert stats["expectancy_r"] == 1.5
    # "worst" among a set of all winners is still just the smallest one.
    assert stats["worst_trade_r"] == 1.0
    assert stats["tail_avg_r"] == 1.0


def test_all_losers():
    trades = [_trade(-1.0), _trade(-0.5)]
    stats = strategy_r_stats(trades)
    assert stats["trades"] == 2
    assert stats["win_rate"] == 0.0
    assert stats["avg_win_r"] == 0.0
    assert stats["avg_loss_r"] == -0.75
    assert stats["expectancy_r"] == -0.75
    assert stats["worst_trade_r"] == -1.0
    assert stats["tail_avg_r"] == -1.0


def test_mixed_matches_known_expectancy():
    # 60% win rate, +1R wins, -1R losses -> expectancy = 0.6*1 + 0.4*-1 = 0.2
    trades = [_trade(1.0)] * 6 + [_trade(-1.0)] * 4
    stats = strategy_r_stats(trades)
    assert stats["trades"] == 10
    assert stats["win_rate"] == 0.6
    assert stats["avg_win_r"] == 1.0
    assert stats["avg_loss_r"] == -1.0
    assert abs(stats["expectancy_r"] - 0.2) < 1e-9


def test_zero_r_multiple_counts_toward_total_not_win_or_loss():
    trades = [_trade(1.0), _trade(0.0), _trade(-1.0)]
    stats = strategy_r_stats(trades)
    assert stats["trades"] == 3
    assert stats["win_rate"] == round(1 / 3, 4)
    assert stats["avg_win_r"] == 1.0
    assert stats["avg_loss_r"] == -1.0
    assert stats["expectancy_r"] == 0.0


def test_worst_trade_r_with_small_pool_tail_is_just_the_worst_trade():
    # n=11 -> ceil(11 * 0.05) = 1, so the "tail" is just the single worst trade.
    trades = [_trade(1.0)] * 9 + [_trade(-0.5), _trade(-2.0)]
    stats = strategy_r_stats(trades)
    assert stats["trades"] == 11
    assert stats["worst_trade_r"] == -2.0
    assert stats["tail_avg_r"] == -2.0


def test_tail_avg_r_with_larger_pool_averages_bottom_five_percent():
    # n=100 -> ceil(100 * 0.05) = 5. Worst 5 losers are -10..-6, avg = -8.0.
    winners = [_trade(1.0)] * 90
    losers = [_trade(-r) for r in range(1, 11)]  # -1, -2, ..., -10
    trades = winners + losers
    stats = strategy_r_stats(trades)
    assert stats["trades"] == 100
    assert stats["worst_trade_r"] == -10.0
    assert stats["tail_avg_r"] == -8.0
