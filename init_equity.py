import asyncio
from dashboard import get_equity_data
from config_loader import get_config

async def initialize():
    print("Calcul de l'Equity initiale...")
    config = get_config()
    equity = await get_equity_data(config)
        
    if equity:
        with open("init_equity.txt", "w") as f:
            f.write(str(equity))
        print(f"Equity initiale enregistrée : {equity} USD")
    else:
        print("Erreur : Impossible de calculer l'Equity.")

asyncio.run(initialize())
