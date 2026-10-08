"""Tests for edge_decay_check (step 2 of the 2026-10-08 investigation)."""
from __future__ import annotations

from microbot import edge_decay_check as ed


def _t(idx, r=1.0):
    return {"entry_idx": idx, "r_multiple": r}


def test_newest_block_is_last_block_bars():
    # 300 bars, blocks of 100: last bar idx 299 -> block 0 = idx 200..299
    b = ed.bucket_by_block([_t(299), _t(200), _t(199), _t(100), _t(99), _t(0)], 300, 100)
    assert [t["entry_idx"] for t in b[0]] == [299, 200]
    assert [t["entry_idx"] for t in b[1]] == [199, 100]
    assert [t["entry_idx"] for t in b[2]] == [99, 0]


def test_oldest_block_can_be_partial():
    b = ed.bucket_by_block([_t(0), _t(49)], 250, 100)   # idx 0..49 -> block 2
    assert set(b) == {2}


def test_block_label():
    dates = [f"d{i}" for i in range(250)]
    assert ed.block_label(dates, 250, 0, 100) == "d150..d249"
    assert ed.block_label(dates, 250, 2, 100) == "d0..d49"


def test_pool_and_summarize():
    a = {0: [_t(9, 1.0)], 1: [_t(1, -1.0)]}
    b = {0: [_t(8, -1.0)]}
    pooled = ed.pool_blocks([a, b])
    assert len(pooled[0]) == 2 and len(pooled[1]) == 1
    s = ed.summarize({"strat": pooled})
    assert s["strat"][0]["expectancy_r"] == 0.0
    assert s["strat"][1]["expectancy_r"] == -1.0
