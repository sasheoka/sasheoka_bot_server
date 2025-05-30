# utils/snag_api_client.py
import aiohttp
import logging
import json
import asyncio
from typing import Optional, Dict, List, Any

# Константы
SNAG_API_BASE_URL = "https://admin.snagsolutions.io"
ACCOUNTS_ENDPOINT = "/api/loyalty/accounts"
TRANSACTION_ENTRIES_ENDPOINT = "/api/loyalty/transaction_entries"
CREATE_TRANSACTION_ENDPOINT = "/api/loyalty/transactions"
CURRENCIES_ENDPOINT = "/api/loyalty/currencies"
REFERRALS_ENDPOINT = "/api/referral/users"
RULES_ENDPOINT = "/api/loyalty/rules"
BADGES_ENDPOINT = "/api/loyalty/badges"

SNAG_API_KEY_HEADER = "X-API-KEY"
logger = logging.getLogger(__name__)

class SnagApiClient:
    def __init__(self, session: aiohttp.ClientSession, api_key: Optional[str],
                 organization_id: Optional[str], website_id: Optional[str],
                 client_name: str = "SnagClient"):
        
        self._client_name = client_name
        if not api_key: logger.error(f"[{self._client_name}] SnagApiClient initialized without API Key!")
        
        self._session = session
        self._api_key = api_key
        self._organization_id = organization_id # Глобальный для клиента
        self._website_id = website_id         # Глобальный для клиента
        self._base_url = SNAG_API_BASE_URL

        self._endpoints_with_org_site_in_query = [
            ACCOUNTS_ENDPOINT, TRANSACTION_ENTRIES_ENDPOINT, CURRENCIES_ENDPOINT,
            REFERRALS_ENDPOINT, RULES_ENDPOINT, BADGES_ENDPOINT,
        ]

    async def _make_request(self, method: str, endpoint: str, params: Optional[Dict] = None,
                            json_data: Optional[Dict] = None, timeout: int = 20) -> Optional[Dict]:
        if not self._api_key:
            logger.error(f"[{self._client_name}] Cannot make API request to {endpoint}: API Key is missing.")
            return None
        
        request_params = params.copy() if params is not None else {} 
        
        # Проверяем базовый эндпоинт для добавления глобальных orgId/websiteId из клиента,
        # только если они не были явно переданы в params для этого запроса.
        base_endpoint_for_check_str = "/".join(endpoint.split('/')[0:4])
        if base_endpoint_for_check_str in self._endpoints_with_org_site_in_query:
            if self._organization_id and 'organizationId' not in request_params:
                 request_params['organizationId'] = self._organization_id
            if self._website_id and 'websiteId' not in request_params:
                 request_params['websiteId'] = self._website_id
        
        headers = {SNAG_API_KEY_HEADER: self._api_key, "Content-Type": "application/json"}
        url = f"{self._base_url}{endpoint}"
        response_text = ""
        try:
            logger.debug(f"[{self._client_name}] API Req: {method} {url} | Params: {request_params} | JSON: {json_data}")
            async with self._session.request(
                method, url, headers=headers, params=request_params, json=json_data,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                response_text = await response.text()
                log_level = logging.INFO if response.ok else logging.ERROR
                logger.log(log_level, f"[{self._client_name}] API Resp {method} {url.split('?')[0]}: Status {response.status} | Resp text (first 300): {response_text[:300]}")
                response.raise_for_status() 
                if response.status == 204:
                    return {"success": True, "status": 204, "message": "Operation successful, no content returned."}
                return json.loads(response_text) if response_text else {}
        except json.JSONDecodeError:
             status_code = response.status if 'response' in locals() and hasattr(response, 'status') else 'N/A'
             logger.error(f"[{self._client_name}] JSON Decode Error for {endpoint}. Status: {status_code}. Raw Text (first 200): {response_text[:200]}...")
             return {"error": True, "status": "JSONDecodeError", "message": "Failed to decode JSON response.", "raw_response": response_text[:1000]}
        except asyncio.TimeoutError:
            logger.error(f"[{self._client_name}] API Request {method} {endpoint}: Request timed out after {timeout} seconds.")
            return {"error": True, "status": "TimeoutError", "message": f"Request timed out after {timeout} seconds."}
        except aiohttp.ClientResponseError as e:
            logger.error(f"[{self._client_name}] HTTP Error {e.status} for {method} {endpoint} - {e.message}. Full response was logged.")
            return {"error": True, "status": e.status, "message": e.message, "raw_response": response_text[:1000]}
        except aiohttp.ClientConnectionError as e:
             logger.error(f"[{self._client_name}] API Request {method} {endpoint}: Connection Error - {e}")
             return {"error": True, "status": "ConnectionError", "message": f"Connection Error: {e}"}
        except Exception as e:
            logger.exception(f"[{self._client_name}] Unexpected API Error for {endpoint}")
            return {"error": True, "status": "Exception", "message": f"Unexpected error: {e}"}

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
                                      limit: int = 100, starting_after: Optional[str] = None,
                                      exclude_deleted_currency: bool = True) -> Optional[Dict]:
        params = {'limit': limit}
        if wallet_address: params['walletAddress'] = wallet_address
        if rule_id: params['loyaltyRuleId'] = rule_id
        if direction: params['direction'] = direction
        if starting_after: params['startingAfter'] = starting_after
        params['excludeDeletedCurrency'] = str(exclude_deleted_currency).lower()
        return await self._make_request("GET", TRANSACTION_ENTRIES_ENDPOINT, params=params, timeout=30)

    async def get_currencies(self, limit: int = 100, include_deleted: bool = True) -> Optional[Dict]:
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

    async def get_loyalty_rules(self, 
                                limit: int = 100, 
                                starting_after: Optional[str] = None,
                                include_deleted: bool = False, 
                                is_active: Optional[bool] = None,
                                hide_in_ui: Optional[bool] = None, 
                                loyalty_rule_id: Optional[str] = None,
                                organization_id_filter: Optional[str] = None, # <--- ИЗМЕНЕНО: Используем правильные имена ключей для API
                                website_id_filter: Optional[str] = None       # <--- ИЗМЕНЕНО: Используем правильные имена ключей для API
                                ) -> Optional[Dict]:
        params = {'limit': limit, 'includeDeleted': str(include_deleted).lower()}
        if starting_after: params['startingAfter'] = starting_after
        if is_active is not None: params['isActive'] = str(is_active).lower()
        if hide_in_ui is not None: params['hideInUi'] = str(hide_in_ui).lower()
        if loyalty_rule_id: params['loyaltyRuleId'] = loyalty_rule_id
        
        # Явно добавляем фильтры в params, если они переданы.
        # Имена ключей в params должны точно совпадать с именами query-параметров, ожидаемых API.
        if organization_id_filter:
            params['organizationId'] = organization_id_filter # Ключ 'organizationId' для API
        if website_id_filter:
            params['websiteId'] = website_id_filter         # Ключ 'websiteId' для API
            
        return await self._make_request("GET", RULES_ENDPOINT, params=params)

    async def get_loyalty_rule_details(self, rule_id: str, organization_id_filter: Optional[str] = None, website_id_filter: Optional[str] = None) -> Optional[Dict[str, Any]]:
        response = await self.get_loyalty_rules(
            loyalty_rule_id=rule_id, 
            limit=1,
            organization_id_filter=organization_id_filter, # Передаем дальше
            website_id_filter=website_id_filter         # Передаем дальше
        )
        if response and not response.get("error") and isinstance(response.get("data"), list):
            if response["data"]:
                return response["data"][0] 
            else:
                logger.warning(f"[{self._client_name}] No rule found with ID {rule_id} using list endpoint filter.")
                # Возвращаем структуру, похожую на ошибку API, для единообразия
                return {"error": True, "status": 404, "message": f"Rule with ID {rule_id} not found via filtered list."} 
        elif response and response.get("error"):
            return response # Передаем ошибку от _make_request или get_loyalty_rules
        
        logger.error(f"[{self._client_name}] Unexpected response or format when fetching rule ID {rule_id}: {response}")
        return {"error": True, "status": "InternalFormatError", "message": "Unexpected response format from API when fetching rule details."}

    async def create_transaction(self, tx_data: Dict[str, Any]) -> Optional[Dict]:
        final_tx_data = tx_data.copy()
        # Не добавляем org/website ID автоматически сюда, предполагаем, что они переданы в tx_data, если нужны API в теле
        return await self._make_request("POST", CREATE_TRANSACTION_ENDPOINT, json_data=final_tx_data)

    async def reward_badge(self, badge_id: str, data: Dict[str, Any]) -> Optional[Dict]:
        endpoint = f"{BADGES_ENDPOINT}/{badge_id}/reward"
        final_data = data.copy()
        return await self._make_request("POST", endpoint, json_data=final_data)
    
    async def complete_loyalty_rule(self, rule_id: str, data: Dict[str, Any]) -> Optional[Dict]:
        endpoint = f"{RULES_ENDPOINT}/{rule_id}/complete"
        final_data = data.copy()
        return await self._make_request("POST", endpoint, json_data=final_data)

    async def update_loyalty_rule(self, rule_id: str, update_data: Dict[str, Any]) -> Optional[Dict]:
        endpoint = f"{RULES_ENDPOINT}/{rule_id}"
        return await self._make_request("POST", endpoint, json_data=update_data)