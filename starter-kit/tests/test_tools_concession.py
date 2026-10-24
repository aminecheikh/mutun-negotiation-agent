"""tools/concession.py: recover where a supplier stops from its own price path (synthetic paths with a known
answer - the method, not practice numbers)."""

import random

import pytest

from arena_kit.agents.buyer.tools.concession import fit


def path(reservation: float, opening: float, beta: float, rounds: int, limit: int = 10) -> list[float]:
    """Prices of a supplier conceding on a time-dependent curve towards `reservation`."""
    return [round(reservation + (opening - reservation) * (1 - (t / limit) ** (1 / beta)), 2) for t in range(rounds)]


def events(prices: list[float]) -> list[dict]:
    out = []
    for p in prices:
        out += [{"from": "sales@x", "term_sheets": [{"terms": {"unit_price": p}}]}, {"from": "buyer"}]
    return out[:-1]


@pytest.mark.parametrize("beta", [0.5, 1.0, 2.0])
def test_fit_when_supplier_follows_a_concession_curve_recovers_its_stop(beta):
    prices = path(120.0, 140.0, beta, rounds=7)
    result = fit("sup:x", events(prices), floor=120.0)
    # 7 of 10 rounds seen and its own deadline unknown: a slow (Boulware) path leaves R a few % uncertain
    assert result.reservation_estimate == pytest.approx(120.0, rel=0.05)


def test_fit_when_prior_is_wrong_lets_the_prices_win():
    prices = path(120.0, 140.0, 1.0, rounds=8)
    result = fit("sup:x", events(prices), floor=100.0)  # our cost estimate is 17 % too low
    assert result.reservation_estimate == pytest.approx(120.0, rel=0.05)


def test_fit_when_supplier_stopped_moving_sees_little_room():
    result = fit("sup:x", events([140.0, 136.0, 134.0, 134.0, 134.0, 134.0]), floor=110.0)
    assert result.reservation_estimate > 128.0 and result.p_room < 0.5


def test_fit_when_supplier_is_still_moving_fast_sees_room():
    result = fit("sup:x", events([140.0, 137.0, 134.0]), floor=118.0)
    assert result.p_room > 0.5


def test_fit_when_only_the_opening_is_known_falls_back_to_the_floor_or_none():
    assert fit("sup:x", events([140.0]), floor=120.0).reservation_estimate == pytest.approx(120.0, rel=0.01)
    assert fit("sup:x", events([140.0])).reservation_estimate is None


def test_fit_when_any_path_never_estimates_above_its_last_price():
    rng = random.Random(7)
    for _ in range(200):
        opening = rng.uniform(1, 1000)
        prices = sorted((round(opening * rng.uniform(0.8, 1.0), 2) for _ in range(rng.randint(1, 9))), reverse=True)
        result = fit("sup:x", events([opening, *prices]), floor=rng.choice([None, opening * rng.uniform(0.5, 1.1)]))
        if result.reservation_estimate is not None:
            assert 0 < result.reservation_estimate <= min([opening, *prices]) + 1e-9
        assert result.p_room is None or 0 <= result.p_room <= 1
