import asyncio
from dashboard import get_dashboard_text

async def debug():
    text = await get_dashboard_text()
    print("--- TEXT ---")
    print(text)
    print("--- END TEXT ---")

asyncio.run(debug())
