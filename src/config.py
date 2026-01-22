"""
Configuration module for Polymarket Arbitrage Bot.
Loads settings from environment variables with validation.
"""

import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

# Load .env file if present
load_dotenv()


@dataclass
class PolymarketConfig:
    """Polymarket API configuration."""
    api_key: str
    api_secret: str
    api_passphrase: str
    
    # API endpoints
    clob_url: str = "https://clob.polymarket.com"
    gamma_url: str = "https://gamma-api.polymarket.com"
    ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


@dataclass
class WalletConfig:
    """Wallet and blockchain configuration."""
    private_key: str
    wallet_address: str
    polygon_rpc_url: str
    
    # Chain ID for Polygon Mainnet
    chain_id: int = 137


@dataclass
class TradingConfig:
    """Trading parameters and thresholds."""
    min_edge_bps: int  # Minimum edge in basis points
    max_position_size: float  # Maximum USDC per trade
    max_concurrent_orders: int  # Max open orders
    
    # Fee estimates
    clob_taker_fee_bps: int = 20  # 0.2% taker fee
    clob_maker_fee_bps: int = 0   # 0% maker fee
    merge_gas_cost_usd: float = 0.02  # Estimated merge gas


@dataclass
class RiskConfig:
    """Risk control settings."""
    kill_switch: bool
    simulation_mode: bool  # Dry run - detect but don't execute
    min_wallet_balance: float
    cooldown_seconds: int


@dataclass
class LogConfig:
    """Logging configuration."""
    log_level: str
    json_logging: bool


@dataclass
class AzureConfig:
    """Azure-specific configuration."""
    keyvault_name: Optional[str]


@dataclass
class Config:
    """Main configuration container."""
    polymarket: PolymarketConfig
    wallet: WalletConfig
    trading: TradingConfig
    risk: RiskConfig
    logging: LogConfig
    azure: AzureConfig


def get_env(key: str, default: Optional[str] = None, required: bool = True) -> str:
    """Get environment variable with validation."""
    value = os.getenv(key, default)
    if required and not value:
        raise ValueError(f"Required environment variable {key} is not set")
    return value or ""


def get_env_bool(key: str, default: bool = False) -> bool:
    """Get boolean environment variable."""
    value = os.getenv(key, str(default)).lower()
    return value in ("true", "1", "yes")


def get_env_int(key: str, default: int) -> int:
    """Get integer environment variable."""
    value = os.getenv(key, str(default))
    return int(value)


def get_env_float(key: str, default: float) -> float:
    """Get float environment variable."""
    value = os.getenv(key, str(default))
    return float(value)


def load_config() -> Config:
    """Load and validate configuration from environment."""
    
    # Check for Azure Key Vault first
    keyvault_name = os.getenv("AZURE_KEYVAULT_NAME")
    if keyvault_name:
        _load_secrets_from_keyvault(keyvault_name)
    
    return Config(
        polymarket=PolymarketConfig(
            api_key=get_env("POLYMARKET_API_KEY"),
            api_secret=get_env("POLYMARKET_API_SECRET"),
            api_passphrase=get_env("POLYMARKET_API_PASSPHRASE"),
        ),
        wallet=WalletConfig(
            private_key=get_env("PRIVATE_KEY"),
            wallet_address=get_env("WALLET_ADDRESS"),
            polygon_rpc_url=get_env("POLYGON_RPC_URL"),
        ),
        trading=TradingConfig(
            min_edge_bps=get_env_int("MIN_EDGE_BPS", 50),
            max_position_size=get_env_float("MAX_POSITION_SIZE", 100),
            max_concurrent_orders=get_env_int("MAX_CONCURRENT_ORDERS", 5),
        ),
        risk=RiskConfig(
            kill_switch=get_env_bool("KILL_SWITCH", False),
            simulation_mode=get_env_bool("SIMULATION_MODE", True),  # Default to simulation
            min_wallet_balance=get_env_float("MIN_WALLET_BALANCE", 50),
            cooldown_seconds=get_env_int("COOLDOWN_SECONDS", 30),
        ),
        logging=LogConfig(
            log_level=get_env("LOG_LEVEL", "INFO", required=False),
            json_logging=get_env_bool("JSON_LOGGING", True),
        ),
        azure=AzureConfig(
            keyvault_name=keyvault_name,
        ),
    )


def _load_secrets_from_keyvault(keyvault_name: str) -> None:
    """Load secrets from Azure Key Vault into environment."""
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        
        vault_url = f"https://{keyvault_name}.vault.azure.net"
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=vault_url, credential=credential)
        
        # Map Key Vault secret names to environment variables
        secret_mappings = {
            "polymarket-api-key": "POLYMARKET_API_KEY",
            "polymarket-api-secret": "POLYMARKET_API_SECRET",
            "polymarket-api-passphrase": "POLYMARKET_API_PASSPHRASE",
            "wallet-private-key": "PRIVATE_KEY",
            "polygon-rpc-url": "POLYGON_RPC_URL",
        }
        
        for secret_name, env_var in secret_mappings.items():
            try:
                secret = client.get_secret(secret_name)
                if secret.value:
                    os.environ[env_var] = secret.value
            except Exception:
                pass  # Secret not found, will use env var
                
    except ImportError:
        pass  # Azure SDK not available
    except Exception as e:
        print(f"Warning: Failed to load secrets from Key Vault: {e}")
