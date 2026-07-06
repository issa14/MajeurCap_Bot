"""sync_engine.protection — Recréation des ordres SL/TP manquants sur l'exchange.

Déplacé depuis trade_manager.py::_recreate_missing_orders() à l'étape 4 du
refactor. Logique inchangée depuis l'extraction de l'étape 3 — seul le nom de
la fonction change (_recreate_missing_orders → enforce_protection) et son
emplacement (fichier dédié plutôt qu'imbriquée dans trade_manager.py).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from database import db
from telegram_utils import send_telegram
from exchange.normalize import get_raw_order_type
from risk.circuit_breaker import (
    ProtectionState, SL_CONFIG, TP_CONFIG,
    is_blocked, on_failure, should_alert,
)
from sync_engine.constants import TOLERANCE

log = logging.getLogger("sync_engine.protection")


async def enforce_protection(
    pos: dict,
    pos_id: int,
    db_sym: str,
    direction: str,
    exchange_qty: float,
    sl_price: float,
    tp1_price: float,
    tp2_price: float,
    sl_found: bool,
    tp1_found: bool,
    tp2_found: bool,
    tp1_hit: bool,
    exchange,
    config: dict,
    to_futures_symbol,
    get_stop_price,
) -> None:
    """Recrée sur l'exchange les ordres SL/TP1/TP2 manquants, avec circuit breaker
    à cooldown et garde d'idempotence.

    to_futures_symbol et get_stop_price sont injectés en paramètres (au lieu
    d'être importés depuis trade_manager, ce qui créerait une dépendance
    circulaire sync_engine → trade_manager → sync_engine). trade_manager.py
    passe ses fonctions _to_futures_symbol et _get_stop_price existantes.
    """
    # ── Recréer les ordres manquants ──
    sl_side = "sell" if direction == "LONG" else "buy"
    order_specs = []

    if not sl_found:
        order_specs.append({"label": "SL",  "price": sl_price,  "qty": exchange_qty,         "ord_type": "stop_market"})
    if not tp1_hit and pos.get("tp1_order_id") and not tp1_found:
        order_specs.append({"label": "TP1", "price": tp1_price, "qty": exchange_qty * 0.5,   "ord_type": "take_profit_market"})
    if pos.get("tp2_order_id") and not tp2_found:
        order_specs.append({"label": "TP2", "price": tp2_price, "qty": exchange_qty,         "ord_type": "take_profit_market"})

    for spec in order_specs:
        if spec["price"] <= 0 or spec["qty"] <= 0:
            continue

        # ── Circuit breaker par label (cooldown temporel, jamais permanent) ──
        now = datetime.now(timezone.utc)

        def _parse_dt(iso_str):
            return datetime.fromisoformat(iso_str) if iso_str else None

        if spec["label"] == "SL":
            state = ProtectionState(
                failures=pos.get("sl_sync_failures", 0),
                cooldown_until=_parse_dt(pos.get("sl_cooldown_until")),
                last_alert_at=_parse_dt(pos.get("last_sl_alert_at")),
            )
            if is_blocked(state, SL_CONFIG, now):
                log.warning(
                    f"sync_all {db_sym} — SL en cooldown jusqu'à {state.cooldown_until.isoformat()}"
                )
                continue
        else:
            state = ProtectionState(
                failures=pos.get("tp_sync_failures", 0),
                cooldown_until=_parse_dt(pos.get("tp_cooldown_until")),
                last_alert_at=None,
            )
            if is_blocked(state, TP_CONFIG, now):
                log.warning(
                    f"sync_all {db_sym} — TP en cooldown jusqu'à {state.cooldown_until.isoformat()}"
                )
                continue

        futures_sym = to_futures_symbol(db_sym)

        # ══════════════════════════════════════════════════════════════════
        # GARDE D'IDEMPOTENCE — Check-then-Create
        # Re-fetch les ordres ouverts AVANT de créer, pour éviter
        # les doublons si le matching précédent a échoué.
        # ══════════════════════════════════════════════════════════════════
        try:
            fresh_orders = await exchange.fetch_open_orders(futures_sym)
            already_exists = None
            for o in fresh_orders:
                o_type = (get_raw_order_type(o) or "").lower()
                o_sp = get_stop_price(o)
                o_ro = o.get("reduceOnly", False) or o.get("info", {}).get("reduceOnly") == "true"
                if not o_sp or not o_ro:
                    continue
                o_qty = float(o.get("amount", 0))
                # Matching par type + stopPrice (±0.5%) + qty (±15%)
                if o_type == spec["ord_type"] and abs(o_sp - spec["price"]) / max(spec["price"], 1) < 0.005:
                    if abs(o_qty - spec["qty"]) / max(spec["qty"], 1) <= 0.15:
                        already_exists = o
                        break
            if already_exists:
                o_id = str(already_exists["id"])
                col = f"{spec['label'].lower()}_order_id"
                db.update_position(pos_id, {col: o_id})
                db.bump_reconcile_version(pos_id)
                db.insert_reconcile_log(pos_id, "ORDER_ALREADY_EXISTS", {
                    "label": spec["label"],
                    "existing_id": o_id,
                    "stop_price": get_stop_price(already_exists),
                })
                log.info(f"sync_all {db_sym} — {spec['label']} déjà présent (id={o_id}), création ignorée")
                if spec["label"] == "SL":
                    db.reset_sync_failure(pos_id)
                else:
                    db.reset_tp_sync_failure(pos_id)
                continue  # ← PAS de création
        except Exception as precheck_err:
            log.warning(f"sync_all {db_sym} — échec pre-check idempotence: {precheck_err}, on tente la création quand même")

        # ── Création de l'ordre ──
        try:
            qty_precise = float(exchange.amount_to_precision(db_sym, spec["qty"]))
            new_order = await exchange.create_order(
                symbol=futures_sym, type=spec["ord_type"], side=sl_side,
                amount=qty_precise, price=None,
                params={"stopPrice": spec["price"], "reduceOnly": True},
            )
            col = f"{spec['label'].lower()}_order_id"
            db.update_position(pos_id, {col: str(new_order["id"])})
            db.bump_reconcile_version(pos_id)
            db.insert_reconcile_log(pos_id, "ORDER_RECREATED", {
                "label": spec["label"],
                "new_id": str(new_order["id"]),
                "price": spec["price"],
                "qty": spec["qty"],
            })
            log.info(f"sync_all {db_sym} — {spec['label']} recréé: {new_order['id']}")
            if spec["label"] == "SL":
                db.reset_sync_failure(pos_id)
            else:
                db.reset_tp_sync_failure(pos_id)
            asyncio.create_task(send_telegram(
                f"🔧 {db_sym} — {spec['label']} recréé automatiquement sur Binance.", config
            ))
        except Exception as e:
            err_str = str(e)
            log.error(f"sync_all {db_sym} — échec recréation {spec['label']}: {err_str}")

            # -4045 cleanup + retry
            if "-4045" in err_str or "Reach max stop order limit" in err_str:
                log.warning(f"sync_all {db_sym} — -4045, cleanup orphelins...")
                cleaned = False
                try:
                    orders_raw = await exchange.fetch_open_orders(futures_sym)
                    for o in orders_raw:
                        o_stop = get_stop_price(o)
                        ro = o.get("reduceOnly", False) or o.get("info", {}).get("reduceOnly") == "true"
                        if not ro:
                            continue
                        matches = any(
                            s["price"] > 0 and abs(o_stop - s["price"]) / max(s["price"], 1) < TOLERANCE
                            for s in order_specs
                        )
                        if not matches:
                            try:
                                await exchange.cancel_order(str(o["id"]), futures_sym)
                                log.info(f"sync_all {db_sym} — orphelin annulé: {o['id']}")
                                cleaned = True
                            except Exception as ce:
                                log.warning(f"sync_all {db_sym} — échec annulation orphelin {o['id']}: {ce}")
                except Exception as fe:
                    log.warning(f"sync_all {db_sym} — échec fetch pour cleanup: {fe}")

                if cleaned:
                    try:
                        qty_precise = float(exchange.amount_to_precision(db_sym, spec["qty"]))
                        new_order = await exchange.create_order(
                            symbol=futures_sym, type=spec["ord_type"], side=sl_side,
                            amount=qty_precise, price=None,
                            params={"stopPrice": spec["price"], "reduceOnly": True},
                        )
                        col = f"{spec['label'].lower()}_order_id"
                        db.update_position(pos_id, {col: str(new_order["id"])})
                        db.bump_reconcile_version(pos_id)
                        db.insert_reconcile_log(pos_id, "ORDER_RECREATED_AFTER_CLEANUP", {
                            "label": spec["label"], "new_id": str(new_order["id"]),
                        })
                        log.info(f"sync_all {db_sym} — {spec['label']} recréé après cleanup: {new_order['id']}")
                        if spec["label"] == "SL":
                            db.reset_sync_failure(pos_id)
                        else:
                            db.reset_tp_sync_failure(pos_id)
                        asyncio.create_task(send_telegram(
                            f"🔧 {db_sym} — {spec['label']} recréé (après nettoyage).", config
                        ))
                        continue
                    except Exception as re:
                        log.error(f"sync_all {db_sym} — échec retry post-cleanup: {re}")

            # ── Circuit breaker (cooldown, avec ré-alerte périodique) ──
            if spec["label"] == "SL":
                new_failures = db.increment_sync_failure(pos_id)
                new_state = on_failure(state, SL_CONFIG, now)
                log.warning(f"sync_all {db_sym} — échec SL #{new_failures}/{SL_CONFIG.failure_threshold}")
                if new_state.cooldown_until:
                    db.set_sl_cooldown(pos_id, new_state.cooldown_until.isoformat())
                if should_alert(new_state, SL_CONFIG, now):
                    db.set_last_sl_alert(pos_id)
                    asyncio.create_task(send_telegram(
                        f"🚨 CRITICAL {db_sym} — SL non recréé ({new_failures} échecs) !\n"
                        f"Position SANS stop-loss. Cooldown jusqu'à {new_state.cooldown_until}.\n"
                        f"Qty: {exchange_qty} | Direction: {direction}\n"
                        f"⚠️ Intervention manuelle requise.", config
                    ))
                    db.insert_reconcile_log(pos_id, "SL_CIRCUIT_BREAKER", {
                        "failures": new_failures,
                        "last_price": sl_price,
                        "cooldown_until": new_state.cooldown_until.isoformat() if new_state.cooldown_until else None,
                    })
            else:
                new_failures = db.increment_tp_sync_failure(pos_id)
                new_state = on_failure(state, TP_CONFIG, now)
                log.warning(f"sync_all {db_sym} — échec TP #{new_failures}/{TP_CONFIG.failure_threshold}")
                if new_state.cooldown_until:
                    db.set_tp_cooldown(pos_id, new_state.cooldown_until.isoformat())
                if should_alert(new_state, TP_CONFIG, now):
                    asyncio.create_task(send_telegram(
                        f"⚠️ {db_sym} — TP non recréé ({new_failures} échecs).\n"
                        f"La surveillance logicielle (check_position) prend le relais.\n"
                        f"TP1: {tp1_price} | TP2: {tp2_price}\n"
                        f"Cooldown jusqu'à {new_state.cooldown_until}.",
                        config
                    ))
                    db.insert_reconcile_log(pos_id, "TP_CIRCUIT_BREAKER", {
                        "failures": new_failures,
                        "cooldown_until": new_state.cooldown_until.isoformat() if new_state.cooldown_until else None,
                    })

            asyncio.create_task(send_telegram(
                f"⚠️ {db_sym} — Échec recréation {spec['label']}.\nErreur: {err_str}", config
            ))
