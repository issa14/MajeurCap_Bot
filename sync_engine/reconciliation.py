"""sync_engine.reconciliation — Recherche des ordres SL/TP existants sur l'exchange.

Déplacé depuis trade_manager.py::_reconcile_sl_tp_orders() à l'étape 6 du
refactor. Logique inchangée depuis l'extraction de l'étape 5 — seul le nom
(_reconcile_sl_tp_orders → reconcile_sl_tp_orders) et l'emplacement changent.
"""

from __future__ import annotations

import logging
from typing import Optional

from exchange.normalize import get_raw_order_type
from sync_engine.constants import TOLERANCE

log = logging.getLogger("sync_engine.reconciliation")


async def reconcile_sl_tp_orders(
    pos: dict,
    db_sym: str,
    norm_sym: str,
    sl_price: float,
    tp1_price: float,
    tp2_price: float,
    exchange_qty: float,
    all_open_orders: list,
    exchange,
    to_futures_symbol,
    get_stop_price,
    normalize_symbol,
) -> dict:
    """Recherche les ordres SL/TP1/TP2 existants sur l'exchange, en 2 tiers :
    Tier 1 = lookup par order_id stocké en DB (fetch_order direct)
    Tier 2 = fallback matching par stopPrice + type + qty sur les ordres ouverts

    to_futures_symbol, get_stop_price, normalize_symbol sont injectés en
    paramètres (au lieu d'être importés depuis trade_manager, ce qui créerait
    une dépendance circulaire sync_engine → trade_manager → sync_engine).
    trade_manager.py passe ses fonctions _to_futures_symbol, _get_stop_price,
    _normalize_symbol existantes.

    Retourne un dict avec les clés :
      sl_found, tp1_found, tp2_found (bool)
      sl_order_id_to_save, tp1_order_id_to_save, tp2_order_id_to_save (str | None)
      tp1_hit (bool)
    """

    def _match_order_by_price(
        open_orders: list,
        target_price: float,
        order_type: str,
        expected_qty_hint: float = None,
        tolerance: float = TOLERANCE,
    ) -> Optional[dict]:
        """Matche un ordre ouvert par stopPrice + type (± tolérance)."""
        for o in open_orders:
            o_type = (get_raw_order_type(o) or "").lower()
            o_sp = get_stop_price(o)
            if not o_sp or o_type != order_type:
                continue
            if abs(o_sp - target_price) / max(target_price, 1) < tolerance:
                if expected_qty_hint is not None:
                    o_qty = float(o.get("amount", 0))
                    if abs(o_qty - expected_qty_hint) / max(expected_qty_hint, 1) > 0.15:
                        continue
                return o
        return None

    open_orders_for_sym = [
        o for o in all_open_orders
        if normalize_symbol(o.get("symbol", "")) == norm_sym
        and (o.get("reduceOnly") is True or o.get("info", {}).get("reduceOnly") == "true")
    ]

    sl_found = False
    tp1_found = False
    tp2_found = False
    sl_order_id_to_save = None
    tp1_order_id_to_save = None
    tp2_order_id_to_save = None

    # ── Tier 1 : lookup par order_id stocké en DB ──
    async def _fetch_order_if_exists(order_id, futures_sym):
        """fetch_order() avec gestion 404. Retourne l'ordre ou None."""
        if not order_id:
            return None
        try:
            return await exchange.fetch_order(str(order_id), futures_sym)
        except Exception:
            return None

    # SL
    sl_db_id = pos.get("sl_order_id")
    if sl_db_id and sl_price > 0:
        sl_order = await _fetch_order_if_exists(sl_db_id, to_futures_symbol(db_sym))
        if sl_order:
            sl_status = sl_order.get("status", "")
            o_sp = get_stop_price(sl_order)
            if sl_status in ("open", "new") and abs(o_sp - sl_price) / max(sl_price, 1) < TOLERANCE:
                sl_found = True
                sl_order_id_to_save = str(sl_order["id"])
                log.info(f"sync_all {db_sym} — SL trouvé par ID: {sl_order_id_to_save} (status={sl_status})")
            else:
                log.info(f"sync_all {db_sym} — SL {sl_db_id} status={sl_status} stopPrice={o_sp} — sera recréé")
        else:
            log.info(f"sync_all {db_sym} — SL {sl_db_id} introuvable (404/annulé)")

    # TP1
    tp1_db_id = pos.get("tp1_order_id")
    tp1_hit = pos.get("tp1_status") == "FILLED"
    if tp1_db_id and tp1_price > 0 and not tp1_hit:
        tp1_order = await _fetch_order_if_exists(tp1_db_id, to_futures_symbol(db_sym))
        if tp1_order:
            tp1_status = tp1_order.get("status", "")
            o_sp = get_stop_price(tp1_order)
            if tp1_status in ("open", "new") and abs(o_sp - tp1_price) / max(tp1_price, 1) < TOLERANCE:
                tp1_found = True
                tp1_order_id_to_save = str(tp1_order["id"])
                log.info(f"sync_all {db_sym} — TP1 trouvé par ID: {tp1_order_id_to_save}")
            else:
                log.info(f"sync_all {db_sym} — TP1 {tp1_db_id} status={tp1_status} — sera recréé")
        else:
            log.info(f"sync_all {db_sym} — TP1 {tp1_db_id} introuvable")

    # TP2
    tp2_db_id = pos.get("tp2_order_id")
    if tp2_db_id and tp2_price > 0:
        tp2_order = await _fetch_order_if_exists(tp2_db_id, to_futures_symbol(db_sym))
        if tp2_order:
            tp2_status = tp2_order.get("status", "")
            o_sp = get_stop_price(tp2_order)
            if tp2_status in ("open", "new") and abs(o_sp - tp2_price) / max(tp2_price, 1) < TOLERANCE:
                tp2_found = True
                tp2_order_id_to_save = str(tp2_order["id"])
                log.info(f"sync_all {db_sym} — TP2 trouvé par ID: {tp2_order_id_to_save}")
            else:
                log.info(f"sync_all {db_sym} — TP2 {tp2_db_id} status={tp2_status} — sera recréé")
        else:
            log.info(f"sync_all {db_sym} — TP2 {tp2_db_id} introuvable")

    # ── Tier 2 : fallback matching par prix sur open_orders ──
    if not sl_found and sl_price > 0:
        matched = _match_order_by_price(open_orders_for_sym, sl_price, "stop_market", exchange_qty)
        if matched:
            sl_found = True
            sl_order_id_to_save = str(matched["id"])
            log.info(f"sync_all {db_sym} — SL trouvé par prix (fallback): {sl_order_id_to_save}")

    if not tp1_found and tp1_price > 0 and not tp1_hit:
        matched = _match_order_by_price(open_orders_for_sym, tp1_price, "take_profit_market", exchange_qty * 0.5)
        if matched:
            tp1_found = True
            tp1_order_id_to_save = str(matched["id"])
            log.info(f"sync_all {db_sym} — TP1 trouvé par prix (fallback): {tp1_order_id_to_save}")

    if not tp2_found and tp2_price > 0:
        matched = _match_order_by_price(open_orders_for_sym, tp2_price, "take_profit_market")
        if matched:
            tp2_found = True
            tp2_order_id_to_save = str(matched["id"])
            log.info(f"sync_all {db_sym} — TP2 trouvé par prix (fallback): {tp2_order_id_to_save}")

    return {
        "sl_found": sl_found,
        "tp1_found": tp1_found,
        "tp2_found": tp2_found,
        "sl_order_id_to_save": sl_order_id_to_save,
        "tp1_order_id_to_save": tp1_order_id_to_save,
        "tp2_order_id_to_save": tp2_order_id_to_save,
        "tp1_hit": tp1_hit,
    }
