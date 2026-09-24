from __future__ import annotations
import json
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.integrations.http_client import shared_ssl_context

REQUEST_TIMEOUT: float = 15.0
# Живой прокси подключается < 1 с; мёртвый не должен держать кошелёк 15 с.
CONNECT_TIMEOUT: float = 5.0
SOL_DECIMALS: int = 9
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
SOL_MINT = "So11111111111111111111111111111111111111112"
DEXSCREENER_URL = "https://api.dexscreener.com/tokens/v1/solana"
DEXSCREENER_BATCH = 30

_TOKENS_FILE = Path(__file__).parent.parent / "data" / "solana_tokens.json"

def _load_known_tokens() -> dict[str, str]:
    try:
        return json.loads(_TOKENS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

KNOWN_TOKENS: dict[str, str] = _load_known_tokens()


@dataclass
class WalletData:
    sol_balance: float
    sol_usd: float
    tokens: list[dict]
    total_usd: float


# HTTP-клиенты на прокси, общие для всех потоков (httpx.Client потокобезопасен).
# Раньше на каждую проверку кошелька создавалось два клиента, а создание
# клиента — ~0.3–0.85 с синхронной работы (SSL-контексты): под нагрузкой это
# было основным тормозом. Теперь SSL-контекст общий, а соединения к RPC и
# DexScreener переиспользуются (keep-alive) между кошельками.
_CLIENTS_LOCK = threading.Lock()
_CLIENTS: dict[str | None, httpx.Client] = {}


def _client_for(proxy: str | None) -> httpx.Client:
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(proxy)
        if client is None:
            client = _CLIENTS[proxy] = httpx.Client(
                proxy=proxy, timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=CONNECT_TIMEOUT),
                verify=shared_ssl_context(),
            )
        return client


class SolanaClient:
    """Stateless sync Solana client. One instance per wallet check."""

    def __init__(self, rpc_url: str, proxy: str | None = None) -> None:
        self._rpc_url = rpc_url
        self._proxy = proxy

    def get_wallet_data(self, address: str) -> WalletData:
        client = _client_for(self._proxy)
        lamports = self._get_balance(client, address)
        spl_accounts = self._get_token_accounts(client, address)

        spl_accounts = [a for a in spl_accounts if a["uiAmount"]]

        mints = [a["mint"] for a in spl_accounts]
        prices = self._get_prices(client, mints + [SOL_MINT])

        sol_balance = lamports / (10 ** SOL_DECIMALS)
        sol_price = prices.get(SOL_MINT, 0.0)
        sol_usd = sol_balance * sol_price

        tokens: list[dict] = []
        for acc in spl_accounts:
            mint = acc["mint"]
            amount = acc["uiAmount"]
            price = prices.get(mint, 0.0)
            value = amount * price
            symbol = KNOWN_TOKENS.get(mint, mint[:6])
            tokens.append({
                "symbol": symbol,
                "mint": mint,
                "amount": amount,
                "price": price,
                "value": round(value, 2),
            })

        total_usd = sol_usd + sum(t["value"] for t in tokens)
        return WalletData(
            sol_balance=sol_balance,
            sol_usd=round(sol_usd, 2),
            tokens=tokens,
            total_usd=round(total_usd, 2),
        )

    def _get_balance(self, client: httpx.Client, address: str) -> int:
        resp = client.post(
            self._rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "getBalance",
                  "params": [address]},
        )
        return resp.json()["result"]["value"]

    def _get_token_accounts(self, client: httpx.Client, address: str) -> list[dict]:
        resp = client.post(
            self._rpc_url,
            json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    address,
                    {"programId": TOKEN_PROGRAM_ID},
                    {"encoding": "jsonParsed"},
                ],
            },
        )
        accounts = []
        for entry in resp.json()["result"]["value"]:
            info = entry["account"]["data"]["parsed"]["info"]
            ui_amount = info["tokenAmount"]["uiAmount"]
            accounts.append({"mint": info["mint"], "uiAmount": ui_amount or 0.0})
        return accounts

    def _get_prices(self, client: httpx.Client, mints: list[str]) -> dict[str, float]:
        if not mints:
            return {}
        prices: dict[str, float] = {}
        for i in range(0, len(mints), DEXSCREENER_BATCH):
            batch = mints[i:i + DEXSCREENER_BATCH]
            resp = client.get(f"{DEXSCREENER_URL}/{','.join(batch)}")
            for pair in resp.json():
                mint = pair.get("baseToken", {}).get("address", "")
                price_usd = pair.get("priceUsd")
                if mint and price_usd and mint not in prices:
                    try:
                        prices[mint] = float(price_usd)
                    except (ValueError, TypeError):
                        pass
        return prices
