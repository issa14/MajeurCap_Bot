"""sync_engine.constants — Constantes partagées de la synchronisation exchange.

TOLERANCE était dupliquée à l'identique (0.003) dans deux endroits de
trade_manager.py (_recreate_missing_orders et sync_all). Centralisée ici pour
qu'une seule modification future s'applique partout.
"""

TOLERANCE = 0.003  # 0.3% — tolérance de matching de prix entre DB et exchange
