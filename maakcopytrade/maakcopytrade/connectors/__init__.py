"""Connector registry and the form fields the dashboard shows for each one."""
from .mt5 import MT5Connector
from .projectx import ProjectXConnector
from .simulator import SimConnector
from .tradovate import TradovateConnector

REGISTRY = {"sim": SimConnector, "tradovate": TradovateConnector, "projectx": ProjectXConnector,
            "mt5": MT5Connector}

SIM_FIELDS = [
    {"key": "balance", "label": "Solde initial", "type": "number"},
    {"key": "commission", "label": "Commission par contrat/lot et par cote", "type": "number"},
    {"key": "autotrade", "label": "Bot de demo (trade tout seul, pour un compte maitre)", "type": "bool"},
    {"key": "latency_ms", "label": "Latence simulee (ms)", "type": "number"},
]

EDITION_CONNECTORS = {
    "futures": [
        {"id": "sim", "label": "Simulateur (paper)", "fields": SIM_FIELDS},
        {"id": "tradovate", "label": "Tradovate (Apex, MFFU, TPT, Bulenox...)", "fields": [
            {"key": "env", "label": "Environnement", "type": "select", "options": ["demo", "live"]},
            {"key": "username", "label": "Utilisateur", "type": "text"},
            {"key": "password", "label": "Mot de passe", "type": "password"},
            {"key": "cid", "label": "API cid", "type": "text"},
            {"key": "sec", "label": "API secret", "type": "password"},
            {"key": "app_id", "label": "App ID", "type": "text"},
            {"key": "device_id", "label": "Device ID", "type": "text"},
            {"key": "account", "label": "Nom du compte (vide = premier)", "type": "text"},
        ]},
        {"id": "projectx", "label": "ProjectX (TopstepX...)", "fields": [
            {"key": "base_url", "label": "URL Gateway", "type": "text", "placeholder": "https://api.topstepx.com"},
            {"key": "username", "label": "Utilisateur", "type": "text"},
            {"key": "api_key", "label": "Cle API", "type": "password"},
            {"key": "account", "label": "Nom ou id du compte", "type": "text"},
        ]},
    ],
    "cfd": [
        {"id": "sim", "label": "Simulateur (paper)", "fields": SIM_FIELDS + [
            {"key": "hedging", "label": "Compte hedging (tickets)", "type": "bool"},
            {"key": "symbol_suffix", "label": "Suffixe symboles du simulateur", "type": "text"},
        ]},
        {"id": "mt5", "label": "MetaTrader 5 (FTMO, FundedNext, The5ers...)", "fields": [
            {"key": "terminal_path", "label": "Chemin terminal64.exe (un terminal par compte)", "type": "text"},
            {"key": "login", "label": "Login", "type": "text"},
            {"key": "password", "label": "Mot de passe", "type": "password"},
            {"key": "server", "label": "Serveur", "type": "text"},
            {"key": "deviation", "label": "Deviation max (points)", "type": "number"},
        ]},
    ],
}


def make(connector_id, params, edition):
    cls = REGISTRY.get(connector_id)
    if not cls:
        raise ValueError("connecteur inconnu: %s" % connector_id)
    return cls(params, edition)
