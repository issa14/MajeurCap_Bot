import asyncio
from dashboard import get_dashboard_text
from config_loader import get_config
import re

async def initialize():
    print("Calcul de l'Equity initiale...")
    text = await get_dashboard_text()
    # Recherche de la ligne "  🏦  Equity      :   76233.16 USD"
    match = re.search(r"🏦  Equity\s+:\s+([\d\.]+)", text)
        
    if match:
        equity = match.group(1)
        with open("init_equity.txt", "w") as f:
            f.write(equity)
        print(f"Equity initiale enregistrée : {equity} USD")
    else:
        print("Erreur : Impossible de trouver l'Equity dans le dashboard.")

asyncio.run(initialize())
