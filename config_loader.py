import yaml
import logging
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_PATH = Path("config.yaml")

def load_config() -> dict:
    """Charge la configuration depuis le fichier YAML."""
    if not CONFIG_PATH.exists():
        log.error(f"Fichier de configuration introuvable : {CONFIG_PATH}")
        raise FileNotFoundError("config.yaml est requis.")
    
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# Une instance partagée pour les accès simples (optionnel)
_config = None

def get_config() -> dict:
    global _config
    if _config is None:
        _config = load_config()
    return _config

def reload_config() -> dict:
    """Force le rechargement de la configuration depuis le disque."""
    global _config
    _config = load_config()
    log.info("Configuration rechargée.")
    return _config
