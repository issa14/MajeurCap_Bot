"""Tests de risk.circuit_breaker — vérifie qu'aucun état ne peut rester bloqué
indéfiniment (c'était le bug : deadlock permanent sur BNB/USDT)."""

from datetime import datetime, timedelta, timezone

from risk.circuit_breaker import (
    ProtectionState,
    CircuitBreakerConfig,
    is_blocked,
    on_failure,
    on_success,
    should_alert,
    record_alert_sent,
)

CONFIG = CircuitBreakerConfig(failure_threshold=3, cooldown_minutes=15, realert_minutes=15)
T0 = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


def test_no_block_below_threshold():
    state = ProtectionState(failures=2, cooldown_until=None, last_alert_at=None)
    assert is_blocked(state, CONFIG, T0) is False


def test_threshold_reached_starts_cooldown():
    state = ProtectionState(failures=2, cooldown_until=None, last_alert_at=None)
    new_state = on_failure(state, CONFIG, T0)
    assert new_state.failures == 3
    assert new_state.cooldown_until == T0 + timedelta(minutes=15)
    assert is_blocked(new_state, CONFIG, T0) is True


def test_deadlock_regression_cooldown_expires_and_unblocks():
    """LE test critique : reproduit le bug BNB/USDT.

    Avant le fix : une fois failures>=3, jamais débloqué, jamais retenté.
    Après le fix : passé le cooldown, is_blocked() doit repasser à False,
    permettant une nouvelle tentative (qui pourra réussir et reset l'état).
    """
    state = ProtectionState(failures=3, cooldown_until=T0 + timedelta(minutes=15), last_alert_at=T0)

    still_in_cooldown = T0 + timedelta(minutes=10)
    assert is_blocked(state, CONFIG, still_in_cooldown) is True

    cooldown_expired = T0 + timedelta(minutes=16)
    assert is_blocked(state, CONFIG, cooldown_expired) is False


def test_success_fully_resets_state():
    state = ProtectionState(failures=5, cooldown_until=T0, last_alert_at=T0)
    reset = on_success(state)
    assert reset.failures == 0
    assert reset.cooldown_until is None
    assert reset.last_alert_at is None
    assert is_blocked(reset, CONFIG, T0) is False


def test_failure_below_threshold_does_not_set_cooldown():
    state = ProtectionState(failures=0, cooldown_until=None, last_alert_at=None)
    s1 = on_failure(state, CONFIG, T0)
    assert s1.failures == 1
    assert s1.cooldown_until is None
    s2 = on_failure(s1, CONFIG, T0)
    assert s2.failures == 2
    assert s2.cooldown_until is None


def test_alert_fires_once_at_threshold():
    state = ProtectionState(failures=2, cooldown_until=None, last_alert_at=None)
    state = on_failure(state, CONFIG, T0)  # failures=3
    assert should_alert(state, CONFIG, T0) is True


def test_alert_does_not_repeat_before_realert_window():
    """Régression : l'ancien code n'alertait qu'une fois, JAMAIS de rappel après
    3 jours de position non protégée. On vérifie ici le comportement intermédiaire
    (pas de spam avant la fenêtre), le test suivant vérifie le rappel après."""
    state = ProtectionState(failures=3, cooldown_until=T0, last_alert_at=T0)
    soon_after = T0 + timedelta(minutes=5)
    assert should_alert(state, CONFIG, soon_after) is False


def test_alert_repeats_after_realert_window():
    """LE test qui garantit qu'une position non protégée depuis des jours continue
    à alerter, au lieu de se taire après le premier message (bug initial)."""
    state = ProtectionState(failures=3, cooldown_until=T0, last_alert_at=T0)
    much_later = T0 + timedelta(minutes=20)
    assert should_alert(state, CONFIG, much_later) is True

    acked = record_alert_sent(state, much_later)
    assert should_alert(acked, CONFIG, much_later + timedelta(minutes=5)) is False
    assert should_alert(acked, CONFIG, much_later + timedelta(minutes=16)) is True
