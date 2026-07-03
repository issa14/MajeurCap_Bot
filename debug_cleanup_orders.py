#!/usr/bin/env python3
"""
Script de diagnostic et nettoyage des ordres stop/TP orphelins sur Binance Testnet.
À exécuter ponctuellement pour casser une boucle de "Reach max stop order limit".

Utilisation :
    python debug_cleanup_orders.py              # diagnostic (affiche tous les ordres)
    python debug_cleanup_orders.py --cleanup    # annule TOUS les ordres stop/TP ouverts
    python debug_cleanup_orders.py --fix BNB/USDT  # annule les ordres sur un symbole et recrée SL+TP
"""

import asyncio
import argparse
import logging
import sys
from typing import Optional

sys.path.insert(0, ".")
from execution import init_trading_exchange
from database import db
from config_loader import get_config

log = logging.getLogger("debug_cleanup")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


async def diagnostic(exchange) -> dict:
    """
    Affiche TOUS les ordres stop/TP ouverts sur le compte testnet.
    Retourne un dict symbole → liste d'ordres.
    """
    await exchange.load_markets()

    # Récupérer toutes les positions (pour récupérer les symboles actifs)
    positions = await exchange.fetch_positions()
    active_symbols = [p["symbol"] for p in positions if float(p.get("contracts", 0)) != 0]

    all_orders = []
    # Interroger chaque symbole actif, PLUS les symboles en DB
    db_symbols = [p["symbol"] for p in db.get_active_positions() if p.get("status") != "closed"]
    all_symbols_raw = set(active_symbols + db_symbols)
    # Ajouter les variantes de format
    all_symbols: set[str] = set()
    for s in all_symbols_raw:
        all_symbols.add(s)
        if s.endswith("/USDT"):
            all_symbols.add(f"{s}:USDT")

    print(f"\n{'='*70}")
    print(f"Symboles à interroger : {len(all_symbols)}")
    for sym in sorted(all_symbols):
        print(f"  - {sym}")
    print(f"{'='*70}\n")

    for sym in sorted(all_symbols):
        try:
            orders = await exchange.fetch_open_orders(sym)
            stop_tp = [o for o in orders if o.get("type") in ("stop_market", "take_profit_market")]
            if stop_tp:
                all_orders.extend(stop_tp)
                print(f"--- {sym} ({len(stop_tp)} ordres stop/TP) ---")
                for o in stop_tp:
                    typ = o.get("type", "?")
                    stop_price = o.get("stopPrice", "N/A")
                    qty = o.get("amount", "N/A")
                    rid = o.get("reduceOnly", False) or o.get("info", {}).get("reduceOnly") == "true"
                    print(f"  ID={o['id']}  type={typ}  stopPrice={stop_price}  qty={qty}  reduceOnly={rid}  status={o.get('status')}")
        except Exception as e:
            log.warning(f"fetch_open_orders({sym}) échoué : {e}")

    if not all_orders:
        print("✅ Aucun ordre stop/TP trouvé.")
    else:
        print(f"\n⚠️  Total : {len(all_orders)} ordres stop/TP ouverts (limite Binance = 10 par symbole)")

    # Grouper par symbole normalisé
    by_symbol: dict[str, list] = {}
    for o in all_orders:
        sym = o.get("symbol", "").replace(":USDT", "")
        by_symbol.setdefault(sym, []).append(o)

    return by_symbol


async def cancel_all(exchange, orders_by_symbol: dict) -> None:
    """Annule TOUS les ordres stop/TP listés dans orders_by_symbol."""
    total = 0
    for sym, orders in orders_by_symbol.items():
        for o in orders:
            try:
                await exchange.cancel_order(o["id"], sym)
                total += 1
                log.info(f"Annulé : {sym} id={o['id']} type={o.get('type')}")
            except Exception as e:
                log.error(f"Échec annulation {sym} id={o['id']} : {e}")
    log.info(f"Total annulés : {total}")


async def fix_symbol(exchange, symbol: str) -> None:
    """
    Pour un symbole donné avec une position active en DB :
    1. Annule TOUS les ordres stop/TP ouverts sur ce symbole
    2. Recrée SL + TP1 + TP2 d'après la DB
    """
    positions = [p for p in db.get_active_positions() if p["symbol"] == symbol and p.get("status") != "closed"]
    if not positions:
        log.error(f"Aucune position active en DB pour {symbol}")
        return

    pos = positions[0]
    direction = pos["direction"]
    sl_price = pos.get("sl_price") or pos.get("sl") or 0
    tp1_price = pos.get("tp1_price") or pos.get("tp1") or 0
    tp2_price = pos.get("tp2_price") or pos.get("tp2") or 0
    qty = pos.get("current_quantity") or pos.get("quantity") or 0
    sl_side = "sell" if direction == "LONG" else "buy"

    if qty <= 0:
        log.error(f"Quantité invalide pour {symbol}: {qty}")
        return

    # 1. Annuler tous les ordres existants
    log.info(f"1. Annulation de tous les ordres stop/TP sur {symbol}...")
    for sym_variant in (symbol, f"{symbol}:USDT"):
        try:
            orders = await exchange.fetch_open_orders(sym_variant)
            for o in orders:
                if o.get("type") in ("stop_market", "take_profit_market"):
                    try:
                        await exchange.cancel_order(o["id"], sym_variant)
                        log.info(f"   Annulé : {sym_variant} id={o['id']}")
                    except Exception as e:
                        log.warning(f"   Échec annulation {o['id']} : {e}")
        except Exception as e:
            log.warning(f"   fetch_open_orders({sym_variant}) ignoré : {e}")

    # 2. Recréer les ordres
    log.info(f"2. Recréation SL/TP pour {symbol} (qty={qty}, sl={sl_price}, tp1={tp1_price}, tp2={tp2_price})")
    await exchange.load_markets()

    async def create_stop(label: str, price: float, amount: float, ord_type: str) -> Optional[str]:
        if price <= 0 or amount <= 0:
            log.warning(f"   {label} ignoré : price={price}, amount={amount}")
            return None
        try:
            qty_precise = float(exchange.amount_to_precision(symbol, amount))
            order = await exchange.create_order(
                symbol=symbol,
                type=ord_type,
                side=sl_side,
                amount=qty_precise,
                price=None,
                params={"stopPrice": price, "reduceOnly": True},
            )
            log.info(f"   {label} créé : id={order['id']} stopPrice={price} qty={qty_precise}")
            return str(order["id"])
        except Exception as e:
            log.error(f"   {label} ÉCHEC : {e}")
            return None

    sl_id = await create_stop("SL", sl_price, qty, "stop_market")
    tp1_id = await create_stop("TP1", tp1_price, qty * 0.5, "take_profit_market")
    tp2_id = await create_stop("TP2", tp2_price, qty, "take_profit_market")

    # 3. Mettre à jour la DB
    updates = {}
    if sl_id:
        updates["sl_order_id"] = sl_id
    if tp1_id:
        updates["tp1_order_id"] = tp1_id
    if tp2_id:
        updates["tp2_order_id"] = tp2_id
    if updates:
        db.update_position(pos["id"], updates)
        log.info(f"3. DB mise à jour : {updates}")

    log.info("✅ Correction terminée.")


async def main():
    parser = argparse.ArgumentParser(description="Diagnostic / nettoyage ordres stop/TP Binance Testnet")
    parser.add_argument("--cleanup", action="store_true", help="Annuler TOUS les ordres stop/TP ouverts")
    parser.add_argument("--fix", metavar="SYMBOL", help="Nettoyer et recréer SL+TP pour un symbole (ex: BNB/USDT)")
    args = parser.parse_args()

    config = get_config()
    auto_exec = config.get("execution", {}).get("auto_execute", False)
    if not auto_exec:
        print("⚠️  auto_execute est désactivé dans la config. Activez-le pour pouvoir trader.")

    exchange = await init_trading_exchange()
    try:
        print("\n🔍 Diagnostic des ordres stop/TP ouverts...")
        orders_by_symbol = await diagnostic(exchange)

        if args.cleanup:
            print("\n🧹 ANNULATION de tous les ordres stop/TP...")
            await cancel_all(exchange, orders_by_symbol)

        if args.fix:
            symbol = args.fix
            if not symbol.endswith("/USDT"):
                symbol = f"{symbol}/USDT"
            print(f"\n🔧 Correction de {symbol} (annulation + recréation SL/TP)...")
            await fix_symbol(exchange, symbol)

        if not args.cleanup and not args.fix:
            print("\n💡 Pour nettoyer : python debug_cleanup_orders.py --cleanup")
            print("💡 Pour corriger un symbole : python debug_cleanup_orders.py --fix BNB/USDT")

    finally:
        await exchange.close()


if __name__ == "__main__":
    asyncio.run(main())