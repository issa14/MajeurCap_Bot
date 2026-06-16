import yaml

with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

binance = config.get("binance_demo", {})
api_key = binance.get("api_key")
api_secret = binance.get("api_secret")

print(f"API_KEY length: {len(str(api_key)) if api_key else 'None'}")
print(f"API_KEY type: {type(api_key)}")
print(f"API_KEY snippet: '{str(api_key)[:5]}...{str(api_key)[-5:] if api_key else ''}'")
print(f"API_SECRET length: {len(str(api_secret)) if api_secret else 'None'}")
print(f"API_SECRET type: {type(api_secret)}")
print(f"API_SECRET snippet: '{str(api_secret)[:5]}...{str(api_secret)[-5:] if api_secret else ''}'")
