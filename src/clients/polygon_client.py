"""
Polygon blockchain client for on-chain operations.
Handles token merging and balance queries.
"""

import asyncio
from dataclasses import dataclass
from typing import Optional
import time

from web3 import Web3
from eth_account import Account

# Handle different web3.py versions for PoA middleware
try:
    from web3.middleware import ExtraDataToPOAMiddleware
    POA_MIDDLEWARE = ExtraDataToPOAMiddleware
except ImportError:
    try:
        from web3.middleware import geth_poa_middleware
        POA_MIDDLEWARE = geth_poa_middleware
    except ImportError:
        POA_MIDDLEWARE = None

from ..utils.logger import get_logger

logger = get_logger("polygon")


# CTF Exchange contract address on Polygon
CTF_EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

# Simplified ABI for merge operation
CTF_EXCHANGE_ABI = [
    {
        "inputs": [
            {"name": "conditionId", "type": "bytes32"},
            {"name": "amount", "type": "uint256"}
        ],
        "name": "mergePositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"name": "account", "type": "address"},
            {"name": "id", "type": "uint256"}
        ],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# USDC contract on Polygon
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function"
    }
]


@dataclass
class TransactionResult:
    """Result of a blockchain transaction."""
    success: bool
    tx_hash: str
    gas_used: int
    gas_cost_wei: int
    gas_cost_usd: float  # Estimated USD cost
    error: Optional[str] = None


@dataclass
class WalletBalance:
    """Wallet balance information."""
    usdc_balance: float
    matic_balance: float
    timestamp: float


class PolygonClient:
    """
    Client for Polygon blockchain operations.
    
    Handles:
    - Token merge operations (CTF Exchange)
    - Balance queries (USDC, MATIC)
    - Gas estimation
    """
    
    def __init__(
        self,
        rpc_url: str,
        private_key: str,
        wallet_address: str
    ):
        """
        Initialize Polygon client.
        
        Args:
            rpc_url: Polygon RPC endpoint URL
            private_key: Wallet private key
            wallet_address: Wallet address
        """
        self.rpc_url = rpc_url
        self.private_key = private_key
        self.wallet_address = Web3.to_checksum_address(wallet_address)
        
        self._web3: Optional[Web3] = None
        self._account = None
        self._ctf_contract = None
        self._usdc_contract = None
        
        # Gas price cache
        self._gas_price_gwei: float = 30.0
        self._matic_price_usd: float = 0.50  # Default MATIC price
    
    async def initialize(self) -> None:
        """Initialize Web3 connection and contracts."""
        logger.info("Initializing Polygon client")
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._setup_web3)
        
        logger.info("Polygon client initialized")
    
    def _setup_web3(self) -> None:
        """Set up Web3 instance and contracts."""
        self._web3 = Web3(Web3.HTTPProvider(self.rpc_url))
        
        # Add PoA middleware for Polygon (handle different web3.py versions)
        if POA_MIDDLEWARE:
            try:
                self._web3.middleware_onion.inject(POA_MIDDLEWARE, layer=0)
            except Exception:
                pass  # Middleware might already be added or not needed
        
        # Verify connection
        if not self._web3.is_connected():
            raise RuntimeError(f"Failed to connect to Polygon RPC: {self.rpc_url}")
        
        # Set up account
        self._account = Account.from_key(self.private_key)
        
        # Initialize contracts
        self._ctf_contract = self._web3.eth.contract(
            address=Web3.to_checksum_address(CTF_EXCHANGE_ADDRESS),
            abi=CTF_EXCHANGE_ABI
        )
        
        self._usdc_contract = self._web3.eth.contract(
            address=Web3.to_checksum_address(USDC_ADDRESS),
            abi=USDC_ABI
        )
    
    async def get_balance(self) -> WalletBalance:
        """Get current wallet balances."""
        loop = asyncio.get_event_loop()
        
        matic_wei = await loop.run_in_executor(
            None,
            lambda: self._web3.eth.get_balance(self.wallet_address)
        )
        
        usdc_raw = await loop.run_in_executor(
            None,
            lambda: self._usdc_contract.functions.balanceOf(self.wallet_address).call()
        )
        
        return WalletBalance(
            usdc_balance=usdc_raw / 1e6,  # USDC has 6 decimals
            matic_balance=self._web3.from_wei(matic_wei, 'ether'),
            timestamp=time.time()
        )
    
    async def get_token_balance(self, token_id: str) -> float:
        """
        Get balance of a specific conditional token.
        
        Args:
            token_id: Token ID (as decimal string)
        
        Returns:
            Token balance
        """
        loop = asyncio.get_event_loop()
        
        try:
            # Convert token ID to uint256
            token_id_int = int(token_id)
            
            balance = await loop.run_in_executor(
                None,
                lambda: self._ctf_contract.functions.balanceOf(
                    self.wallet_address,
                    token_id_int
                ).call()
            )
            
            return balance / 1e6  # Assuming 6 decimals like USDC
            
        except Exception as e:
            logger.error(f"Failed to get token balance: {e}")
            return 0.0
    
    async def merge_positions(
        self,
        condition_id: str,
        amount: float
    ) -> TransactionResult:
        """
        Merge YES and NO tokens back to USDC.
        
        Args:
            condition_id: Market condition ID (hex string)
            amount: Amount to merge (in token units)
        
        Returns:
            TransactionResult with tx hash and costs
        """
        logger.info(f"Merging positions: {amount} tokens for condition {condition_id}")
        
        loop = asyncio.get_event_loop()
        
        try:
            # Convert amount to wei (6 decimals)
            amount_wei = int(amount * 1e6)
            
            # Convert condition ID to bytes32
            if condition_id.startswith("0x"):
                condition_bytes = bytes.fromhex(condition_id[2:])
            else:
                condition_bytes = bytes.fromhex(condition_id)
            
            # Pad to 32 bytes
            condition_bytes32 = condition_bytes.rjust(32, b'\x00')
            
            # Build transaction
            nonce = await loop.run_in_executor(
                None,
                lambda: self._web3.eth.get_transaction_count(self.wallet_address)
            )
            
            gas_price = await loop.run_in_executor(
                None,
                lambda: self._web3.eth.gas_price
            )
            
            # Estimate gas
            tx_params = {
                'from': self.wallet_address,
                'nonce': nonce,
                'gasPrice': gas_price,
            }
            
            gas_estimate = await loop.run_in_executor(
                None,
                lambda: self._ctf_contract.functions.mergePositions(
                    condition_bytes32,
                    amount_wei
                ).estimate_gas(tx_params)
            )
            
            # Add 20% buffer to gas estimate
            gas_limit = int(gas_estimate * 1.2)
            
            # Build and sign transaction
            tx = self._ctf_contract.functions.mergePositions(
                condition_bytes32,
                amount_wei
            ).build_transaction({
                'from': self.wallet_address,
                'nonce': nonce,
                'gas': gas_limit,
                'gasPrice': gas_price,
            })
            
            signed_tx = await loop.run_in_executor(
                None,
                lambda: self._account.sign_transaction(tx)
            )
            
            # Send transaction
            tx_hash = await loop.run_in_executor(
                None,
                lambda: self._web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            )
            
            tx_hash_hex = tx_hash.hex()
            logger.info(f"Merge transaction sent: {tx_hash_hex}")
            
            # Wait for receipt
            receipt = await loop.run_in_executor(
                None,
                lambda: self._web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            )
            
            gas_used = receipt['gasUsed']
            gas_cost_wei = gas_used * gas_price
            gas_cost_usd = self._calculate_gas_cost_usd(gas_cost_wei)
            
            if receipt['status'] == 1:
                logger.info(
                    f"Merge successful",
                    extra={
                        "tx_hash": tx_hash_hex,
                        "gas_used": gas_used,
                        "gas_cost_usd": gas_cost_usd
                    }
                )
                
                return TransactionResult(
                    success=True,
                    tx_hash=tx_hash_hex,
                    gas_used=gas_used,
                    gas_cost_wei=gas_cost_wei,
                    gas_cost_usd=gas_cost_usd
                )
            else:
                return TransactionResult(
                    success=False,
                    tx_hash=tx_hash_hex,
                    gas_used=gas_used,
                    gas_cost_wei=gas_cost_wei,
                    gas_cost_usd=gas_cost_usd,
                    error="Transaction reverted"
                )
                
        except Exception as e:
            logger.error(f"Merge failed: {e}")
            return TransactionResult(
                success=False,
                tx_hash="",
                gas_used=0,
                gas_cost_wei=0,
                gas_cost_usd=0.0,
                error=str(e)
            )
    
    def _calculate_gas_cost_usd(self, gas_cost_wei: int) -> float:
        """Calculate gas cost in USD."""
        gas_cost_matic = self._web3.from_wei(gas_cost_wei, 'ether')
        return float(gas_cost_matic) * self._matic_price_usd
    
    async def estimate_merge_gas(self) -> float:
        """Estimate gas cost for a merge operation in USD."""
        loop = asyncio.get_event_loop()
        
        try:
            gas_price = await loop.run_in_executor(
                None,
                lambda: self._web3.eth.gas_price
            )
            
            # Typical merge uses ~80,000 gas
            estimated_gas = 80000
            gas_cost_wei = estimated_gas * gas_price
            
            return self._calculate_gas_cost_usd(gas_cost_wei)
            
        except Exception as e:
            logger.error(f"Failed to estimate gas: {e}")
            return 0.02  # Default estimate
    
    async def update_matic_price(self, price_usd: float) -> None:
        """Update the MATIC/USD price for gas calculations."""
        self._matic_price_usd = price_usd
