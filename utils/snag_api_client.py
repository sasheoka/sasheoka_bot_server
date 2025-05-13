# utils/snag_api_client.py
import aiohttp
import logging
import json
import asyncio
from typing import Optional, Dict, List, Any

# Константы
SNAG_API_BASE_URL = "https://admin.snagsolutions.io"
ACCOUNTS_ENDPOINT = "/api/loyalty/accounts"
TRANSACTIONS_ENDPOINT = "/api/loyalty/transaction_entries"
CURRENCIES_ENDPOINT = "/api/loyalty/currencies"
REFERRALS_ENDPOINT = "/api/referral/users"
RULES_ENDPOINT = "/api/loyalty/rules" # Для списка всех правил/квестов
BADGES_ENDPOINT = "/api/loyalty/badges"

SNAG_API_KEY_HEADER = "X-API-KEY"
logger = logging.getLogger(__name__)

class SnagApiClient:
    def __init__(self, session: aiohttp.ClientSession, api_key: Optional[str],
                 organization_id: Optional[str], website_id: Optional[str],
                 client_name: str = "SnagClient"):
        
        self._client_name = client_name
        if not api_key: logger.error(f"[{self._client_name}] SnagApiClient initialized without API Key!")
        if not organization_id or not website_id: logger.error(f"[{self._client_name}] SnagApiClient initialized without Organization ID or Website ID!")

        self._session = session
        self._api_key = api_key
        self._organization_id = organization_id
        self._website_id = website_id
        self._base_url = SNAG_API_BASE_URL

        self._endpoints_with_org_site = [
            ACCOUNTS_ENDPOINT, TRANSACTIONS_ENDPOINT, CURRENCIES_ENDPOINT,
            REFERRALS_ENDPOINT, RULES_ENDPOINT, BADGES_ENDPOINT
        ]

    async def _make_request(self, method: str, endpoint: str, params: Optional[Dict] = None,
                            json_data: Optional[Dict] = None, timeout: int = 20) -> Optional[Dict]:
        if not self._api_key:
            logger.error(f"[{self._client_name}] Cannot make API request to {endpoint}: API Key is missing.")
            return None
        request_params = params.copy() if params else {}
        if endpoint in self._endpoints_with_org_site:
            if self._organization_id: request_params.setdefault('organizationId', self._organization_id)
            if self._website_id: request_params.setdefault('websiteId', self._website_id)
        
        # Эта проверка должна быть только для тех эндпоинтов, где ID обязательны всегда.
        # Некоторые эндпоинты могут принимать их опционально.
        # Для /api/loyalty/rules, orgId и websiteId являются опциональными согласно документации SDK,
        # но если они есть, то фильтруют правила. Будем их добавлять, если они есть у клиента.
        # if endpoint in self._endpoints_with_org_site and \
        #    ('organizationId' not in request_params or 'websiteId' not in request_params):
        #     logger.error(f"[{self._client_name}] Cannot make API request to {endpoint}: Org/Website ID missing for this endpoint.")
        #     return None

        headers = {SNAG_API_KEY_HEADER: self._api_key}
        url = f"{self._base_url}{endpoint}"
        response_text = ""
        try:
            logger.debug(f"[{self._client_name}] API Req: {method} {url} | Params: {request_params} | JSON: {json_data}")
            async with self._session.request(
                method, url, headers=headers, params=request_params, json=json_data,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                response_text = await response.text()
                logger.info(f"[{self._client_name}] API Resp {method} {endpoint}: Status {response.status}")
                response.raise_for_status()
                if response.status == 204: return {"success": True, "status": 204}
                return json.loads(response_text) if response_text else {}
        except json.JSONDecodeError:
             status_code = response.status if 'response' in locals() and hasattr(response, 'status') else 'N/A'
             logger.error(f"[{self._client_name}] JSON Decode Error for {endpoint}. Status: {status_code}. Text: {response_text[:200]}...")
             return None
        except asyncio.TimeoutError:
            logger.error(f"[{self._client_name}] API Request {method} {endpoint}: Request timed out after {timeout} seconds.")
            return None
        except aiohttp.ClientResponseError as e:
            logger.error(f"[{self._client_name}] API Request {method} {endpoint}: HTTP Error {e.status} - {e.message}. Response: {response_text[:500]}...")
            return None
        except aiohttp.ClientConnectionError as e:
             logger.error(f"[{self._client_name}] API Request {method} {endpoint}: Connection Error - {e}")
             return None
        except Exception as e:
            logger.exception(f"[{self._client_name}] API Error for {endpoint}")
            return None

    async def get_account_by_wallet(self, wallet_address: str) -> Optional[Dict]:
        params = {'limit': 1, 'walletAddress': wallet_address}
        return await self._make_request("GET", ACCOUNTS_ENDPOINT, params=params)
    async def get_account_by_social(self, handle_type: str, handle_value: str) -> Optional[Dict]:
         if handle_type not in ["discordUser", "twitterUser"]:
             logger.error(f"[{self._client_name}] Unsupported social handle type: {handle_type}")
             return None
         params = {'limit': 1, handle_type: handle_value}
         return await self._make_request("GET", ACCOUNTS_ENDPOINT, params=params)
    async def get_all_accounts_for_wallet(self, wallet_address: str, limit: int = 100) -> Optional[Dict]:
        params = {'walletAddress': wallet_address, 'limit': limit}
        return await self._make_request("GET", ACCOUNTS_ENDPOINT, params=params)
    async def get_transaction_entries(self, wallet_address: Optional[str] = None,
                                      rule_id: Optional[str] = None, direction: Optional[str] = None,
                                      limit: int = 100, starting_after: Optional[str] = None) -> Optional[Dict]:
        params = {'limit': limit}
        if wallet_address: params['walletAddress'] = wallet_address
        if rule_id: params['loyaltyRuleId'] = rule_id
        if direction: params['direction'] = direction
        if starting_after: params['startingAfter'] = starting_after
        return await self._make_request("GET", TRANSACTIONS_ENDPOINT, params=params, timeout=30)
    async def get_currencies(self, limit: int = 100, include_deleted: bool = False) -> Optional[Dict]:
        params = {'limit': limit, 'includeDeleted': str(include_deleted).lower()}
        return await self._make_request("GET", CURRENCIES_ENDPOINT, params=params)
    async def get_referrals(self, referrer_wallet: str, limit: int = 50,
                           starting_after: Optional[str] = None, include_eligibility: bool = True) -> Optional[Dict]:
         params = {'walletAddress': referrer_wallet, 'limit': limit, 'includeEligibility': str(include_eligibility).lower()}
         if starting_after: params['startingAfter'] = starting_after
         return await self._make_request("GET", REFERRALS_ENDPOINT, params=params)
    async def get_badges_by_wallet(self, wallet_address: str, limit: int = 100,
                                   starting_after: Optional[str] = None,
                                   include_deleted: bool = False) -> Optional[Dict]:
        params = {'walletAddress': wallet_address, 'limit': limit, 'includeDeleted': str(include_deleted).lower()}
        if starting_after: params['startingAfter'] = starting_after
        return await self._make_request("GET", BADGES_ENDPOINT, params=params)

    # <<< НОВЫЙ МЕТОД ДЛЯ ПОЛУЧЕНИЯ ВСЕХ ПРАВИЛ (КВЕСТОВ) >>>
    async def get_loyalty_rules(self, limit: int = 100, starting_after: Optional[str] = None,
                                include_deleted: bool = False, is_active: Optional[bool] = None,
                                hide_in_ui: Optional[bool] = None) -> Optional[Dict]:
        """Получает список правил лояльности (квестов)."""
        params = {
            'limit': limit,
            'includeDeleted': str(include_deleted).lower()
        }
        if starting_after:
            params['startingAfter'] = starting_after
        if is_active is not None: # API может ожидать true/false как строки
            params['isActive'] = str(is_active).lower()
        if hide_in_ui is not None: # API может ожидать true/false как строки
            params['hideInUi'] = str(hide_in_ui).lower()
        
        # RULES_ENDPOINT уже в self._endpoints_with_org_site, так что orgId и websiteId подставятся
        return await self._make_request("GET", RULES_ENDPOINT, params=params)