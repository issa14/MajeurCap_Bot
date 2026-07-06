"""risk.circuit_breaker — État de protection SL/TP avec cooldown temporel.

Remplace le blocage permanent précédent : atteindre le seuil d'échecs bloque
temporairement les tentatives de recréation (cooldown), mais garantit toujours
une sortie automatique — jamais de deadlock où le compteur ne peut plus être
remis à zéro.

Logique pure, sans I/O — testable sans DB ni exchange. L'appelant (trade_manager)
lit/écrit l'état depuis SQLite et n'a qu'à consommer ce module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass(frozen=True)
class ProtectionState:
    """Snapshot de l'état de protection pour un label (SL ou TP) d'une position."""
    failures: int
    cooldown_until: Optional[datetime]   # None = pas de cooldown actif
    last_alert_at: Optional[datetime]    # None = jamais alerté


@dataclass(frozen=True)
class CircuitBreakerConfig:
    failure_threshold: int
    cooldown_minutes: int
    realert_minutes: int   # ré-alerter toutes les X minutes tant que bloqué


SL_CONFIG = CircuitBreakerConfig(failure_threshold=3, cooldown_minutes=15, realert_minutes=15)
TP_CONFIG = CircuitBreakerConfig(failure_threshold=5, cooldown_minutes=15, realert_minutes=15)


def is_blocked(state: ProtectionState, config: CircuitBreakerConfig, now: datetime) -> bool:
    """True si une tentative de recréation doit être SKIPPÉE ce cycle.

    Ne bloque que si (a) le seuil est atteint ET (b) on est encore dans la
    fenêtre de cooldown. Passé le cooldown, retourne False — une nouvelle
    tentative doit être faite, qu'elle réussisse ou échoue à nouveau.
    """
    if state.failures < config.failure_threshold:
        return False
    if state.cooldown_until is None:
        return False
    return now < state.cooldown_until


def on_failure(state: ProtectionState, config: CircuitBreakerConfig, now: datetime) -> ProtectionState:
    """Calcule le nouvel état après un échec de recréation.

    Si le seuil vient d'être atteint ou dépassé, démarre/renouvelle le cooldown.
    Le cooldown est toujours relancé à chaque nouvel échec au-delà du seuil,
    pour éviter de retenter à un rythme trop rapproché si Binance est indisponible.
    """
    new_failures = state.failures + 1
    cooldown_until = state.cooldown_until
    if new_failures >= config.failure_threshold:
        cooldown_until = now + timedelta(minutes=config.cooldown_minutes)
    return ProtectionState(
        failures=new_failures,
        cooldown_until=cooldown_until,
        last_alert_at=state.last_alert_at,
    )


def on_success(state: ProtectionState) -> ProtectionState:
    """Reset complet après une recréation réussie (ou détection 'déjà présent')."""
    return ProtectionState(failures=0, cooldown_until=None, last_alert_at=None)


def should_alert(state: ProtectionState, config: CircuitBreakerConfig, now: datetime) -> bool:
    """True si une alerte Telegram doit être (re)envoyée ce cycle.

    Contrairement à l'ancien comportement (alerte unique, jamais répétée), on
    ré-alerte toutes les `realert_minutes` tant que la position reste bloquée —
    une position sans protection depuis 3 jours doit continuer à alerter, pas
    se taire après le premier message.
    """
    if state.failures < config.failure_threshold:
        return False
    if state.last_alert_at is None:
        return True
    return now >= state.last_alert_at + timedelta(minutes=config.realert_minutes)


def record_alert_sent(state: ProtectionState, now: datetime) -> ProtectionState:
    return ProtectionState(
        failures=state.failures,
        cooldown_until=state.cooldown_until,
        last_alert_at=now,
    )
