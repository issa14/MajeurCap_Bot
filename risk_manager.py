"""
Risk Manager (v1)
Calcule la taille de position en fonction du risque défini dans config.yaml.
"""

import logging

log = logging.getLogger(__name__)

def get_active_positions_count(current_positions: list) -> int:
    return len([p for p in current_positions if p.get("status") not in ("closed",)])

def get_current_exposure_pct(current_positions: list, capital: float) -> float:
    used_capital = 0.0
    if current_positions:
        for pos in current_positions:
            if pos.get("status") not in ("closed",):
                entry = pos.get("entry") or pos.get("entry_price") or 0
                used_capital += pos.get("quantity", 0) * entry
    return (used_capital / capital) * 100.0

def calculate_position_size(
    signal: dict,
    config: dict,
    current_positions: list = None,
    capital_override: float = None,   # ← capital live depuis l'exchange (prioritaire)
) -> float:
    """
    Retourne la quantité à acheter/vendre (en unités de base) pour respecter le risque.
    Si capital_override est fourni (solde live), il remplace la valeur statique de config.yaml.
    """
    risk_cfg = config.get("risk", {})
    capital = capital_override if capital_override is not None else risk_cfg.get("capital", 1000)
    base_risk = risk_cfg.get("risk_per_trade", 1.0) / 100.0
    max_exposure_pct = risk_cfg.get("max_exposure", 30.0) / 100.0

    # Dynamic Position Sizing based on ADX (trend strength)
    dynamic_adx = risk_cfg.get("dynamic_sizing_adx", True)
    if dynamic_adx and "adx" in signal:
        adx = signal["adx"]
        # ADX reference is 30. Scale risk between 0.5x and 1.5x of base risk.
        multiplier = max(0.5, min(1.5, adx / 30.0))
        risk_per_trade_pct = base_risk * multiplier
        log.info(f"Dynamic Position Sizing (ADX={adx:.1f}): Risk adjusted to {risk_per_trade_pct*100:.2f}% (multiplier={multiplier:.2f})")
    else:
        if dynamic_adx and "adx" not in signal:
            log.warning(
                "dynamic_sizing_adx est activé mais la clé 'adx' est absente du signal "
                "pour ce symbole — sizing de base appliqué (risk_per_trade=%.2f%%)",
                base_risk * 100
            )
        risk_per_trade_pct = base_risk
    sl_pct = signal.get("sl_pct", 1.0) / 100.0   # ex: 3.52% -> 0.0352
    entry_price = signal["entry"]
    leverage = risk_cfg.get("leverage", 1)

    # Capital encore disponible (en tenant compte des positions déjà ouvertes)
    used_capital = 0.0
    if current_positions:
        for pos in current_positions:
            if pos.get("status") not in ("closed",):
                entry = pos.get("entry") or pos.get("entry_price") or 0
                used_capital += pos.get("quantity", 0) * entry

    available_capital = capital - used_capital
    if available_capital <= 0:
        log.warning("Plus de capital disponible")
        return 0.0

    # Risque monétaire maximum sur ce trade
    risk_amount = capital * risk_per_trade_pct   # ex: 1000 * 0.01 = 10 USDT

    # Taille de position futures = risque / (distance SL en % × prix)
    # Le levier NE DOIT PAS réduire la quantité ici : la perte réelle en cas de SL touché
    # dépend uniquement de (quantité × prix × distance SL en %), pas du levier. Le levier
    # détermine seulement la MARGE nécessaire pour ouvrir cette quantité (notional / levier),
    # pas le risque encouru. Cohérent avec backtest_multi.py qui multiplie pnl_pct par leverage.
    if sl_pct == 0:
        return 0.0
    position_size = risk_amount / (sl_pct * entry_price)   # en unités (ex: BTC)
    required_margin = (position_size * entry_price) / leverage
    log.info(f"Position sizing: risk={risk_amount:.2f} USDT, sl={sl_pct*100:.2f}%, leverage={leverage}x → qty={position_size:.6f}, marge requise≈{required_margin:.2f} USDT")

    # Vérifier l'exposition maximale (si dépassée, réduire)
    max_exposure_amount = capital * max_exposure_pct
    new_total_exposure = used_capital + (position_size * entry_price)
    if new_total_exposure > max_exposure_amount:
        log.warning("Exposition maximale dépassée, réduction de la taille")
        position_size = (max_exposure_amount - used_capital) / entry_price
        if position_size <= 0:
            return 0.0

    return round(position_size, 6)


def can_open_position(current_positions: list, config: dict) -> bool:
    """Vérifie si on peut ouvrir une nouvelle position (nombre max)."""
    max_pos = config.get("risk", {}).get("max_positions", 5)
    active = [p for p in current_positions if p.get("status") not in ("closed",)]
    if len(active) >= max_pos:
        log.warning(f"Nombre max de positions atteint ({max_pos})")
        return False
    return True
