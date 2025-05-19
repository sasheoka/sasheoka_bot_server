# utils/snag_api_client.py
import aiohttp
import logging
import json
import asyncio
from typing import Optional, Dict, List, Any

# Константы
SNAG_API_BASE_URL = "https://admin.snagsolutions.io"
ACCOUNTS_ENDPOINT = "/api/loyalty/accounts"
# Эндпоинт для ПОЛУЧЕНИЯ транзакций
TRANSACTION_ENTRIES_ENDPOINT = "/api/loyalty/transaction_entries" # Переименовал для ясности
# Эндпоинт для СОЗДАНИЯ транзакций (согласно api.md)
CREATE_TRANSACTION_ENDPOINT = "/api/loyalty/transactions" # НОВАЯ КОНСТАНТА
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
        # Убрал ошибку для org/website ID, так как некоторые эндпоинты могут не требовать их глобально
        # if not organization_id or not website_id: logger.error(f"[{self._client_name}] SnagApiClient initialized without Organization ID or Website ID!")

        self._session = session
        self._api_key = api_key
        self._organization_id = organization_id
        self._website_id = website_id
        self._base_url = SNAG_API_BASE_URL

        # Эндпоинты, к которым автоматически добавляются organizationId и websiteId в URL query params
        self._endpoints_with_org_site_in_query = [
            ACCOUNTS_ENDPOINT, TRANSACTION_ENTRIES_ENDPOINT, CURRENCIES_ENDPOINT,
            REFERRALS_ENDPOINT, RULES_ENDPOINT, BADGES_ENDPOINT,
            # CREATE_TRANSACTION_ENDPOINT сюда не добавляем, если orgId/websiteId идут в теле JSON
            # или если они вообще не нужны для POST /api/loyalty/transactions в URL, а только в теле.
            # Если они нужны и в URL, и API их там ожидает для POST, то можно добавить.
            # Но обычно для POST они идут в теле, если это не часть пути.
            # В твоем случае, если они нужны в теле, мы их добавим в tx_data в ProgressTransferCog.
            # Если они нужны в URL для этого POST эндпоинта, добавь CREATE_TRANSACTION_ENDPOINT сюда.
        ]
        # Эндпоинты, где orgId/websiteId могут быть ожидаемы в теле JSON (для POST/PUT)
        # Мы будем добавлять их в `json_data` вручную при необходимости в вызывающем коде (когах)
        # или можно сделать логику здесь, если это общий паттерн.
        # Пока оставим добавление в когах для большей гибкости.


    async def _make_request(self, method: str, endpoint: str, params: Optional[Dict] = None,
                            json_data: Optional[Dict] = None, timeout: int = 20) -> Optional[Dict]:
        if not self._api_key:
            logger.error(f"[{self._client_name}] Cannot make API request to {endpoint}: API Key is missing.")
            return None
        
        request_params = params.copy() if params else {}
        
        # Добавляем orgId и websiteId в URL query параметры для GET запросов или тех, что в списке
        if endpoint in self._endpoints_with_org_site_in_query:
            if self._organization_id: request_params.setdefault('organizationId', self._organization_id)
            if self._website_id: request_params.setdefault('websiteId', self._website_id)
        
        # Если это POST/PUT и json_data существует, и если для этого эндпоинта нужны orgId/websiteId в теле:
        # Это более продвинутая логика, пока оставим добавление orgId/websiteId в json_data на стороне когов,
        # так как не все POST/PUT запросы могут их требовать или ожидать в одном и том же месте.
        # Например, для create_transaction они могут быть частью tx_data.
        # if method in ["POST", "PUT"] and json_data is not None:
        #     if self._organization_id and 'organizationId' not in json_data:
        #         json_data['organizationId'] = self._organization_id
        #     if self._website_id and 'websiteId' not in json_data:
        #         json_data['websiteId'] = self._website_id


        headers = {SNAG_API_KEY_HEADER: self._api_key, "Content-Type": "application/json"} # Добавил Content-Type
        url = f"{self._base_url}{endpoint}"
        response_text = ""
        try:
            # Для POST/PUT/PATCH с json_data, aiohttp автоматически устанавливает Content-Type: application/json
            # но явное указание не повредит.
            logger.debug(f"[{self._client_name}] API Req: {method} {url} | Params: {request_params} | JSON: {json_data}")
            async with self._session.request(
                method, url, headers=headers, params=request_params, json=json_data, # json=json_data автоматически сериализует в JSON
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                response_text = await response.text()
                # Логируем перед raise_for_status, чтобы увидеть тело ответа при ошибке
                log_level = logging.INFO if response.ok else logging.ERROR
                logger.log(log_level, f"[{self._client_name}] API Resp {method} {endpoint}: Status {response.status} | Response text (first 500): {response_text[:500]}")
                
                response.raise_for_status() # Вызовет исключение для 4xx/5xx ответов

                if response.status == 204: # No Content
                    return {"success": True, "status": 204} # Возвращаем маркер успеха
                
                # Пытаемся декодировать JSON только если есть тело ответа
                return json.loads(response_text) if response_text else {}
        except json.JSONDecodeError:
             status_code = response.status if 'response' in locals() and hasattr(response, 'status') else 'N/A'
             logger.error(f"[{self._client_name}] JSON Decode Error for {endpoint}. Status: {status_code}. Raw Text: {response_text[:200]}...")
             return None # Или можно вернуть специальный объект ошибки
        except asyncio.TimeoutError:
            logger.error(f"[{self._client_name}] API Request {method} {endpoint}: Request timed out after {timeout} seconds.")
            return None
        except aiohttp.ClientResponseError as e:
            # response_text уже содержит тело ответа, которое было залогировано выше
            logger.error(f"[{self._client_name}] API Request {method} {endpoint}: HTTP Error {e.status} - {e.message}. Full response logged above.")
            return {"error": True, "status": e.status, "message": e.message, "raw_response": response_text[:1000]} # Возвращаем ошибку
        except aiohttp.ClientConnectionError as e:
             logger.error(f"[{self._client_name}] API Request {method} {endpoint}: Connection Error - {e}")
             return None
        except Exception as e:
            logger.exception(f"[{self._client_name}] Unexpected API Error for {endpoint}")
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
        if direction: params['direction'] = direction # API может не поддерживать этот фильтр для GET transaction_entries
        if starting_after: params['startingAfter'] = starting_after
        # Используем TRANSACTION_ENTRIES_ENDPOINT для получения списка
        return await self._make_request("GET", TRANSACTION_ENTRIES_ENDPOINT, params=params, timeout=30)

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

    async def get_loyalty_rules(self, limit: int = 100, starting_after: Optional[str] = None,
                                include_deleted: bool = False, is_active: Optional[bool] = None,
                                hide_in_ui: Optional[bool] = None) -> Optional[Dict]:
        params = {
            'limit': limit,
            'includeDeleted': str(include_deleted).lower()
        }
        if starting_after: params['startingAfter'] = starting_after
        if is_active is not None: params['isActive'] = str(is_active).lower()
        if hide_in_ui is not None: params['hideInUi'] = str(hide_in_ui).lower()
        return await self._make_request("GET", RULES_ENDPOINT, params=params)

    # --- НОВЫЕ МЕТОДЫ ДЛЯ СОЗДАНИЯ ТРАНЗАКЦИЙ И ВЫДАЧИ БЕЙДЖЕЙ ---
    async def create_transaction(self, tx_data: Dict[str, Any]) -> Optional[Dict]:
        """
        Creates a new loyalty transaction.
        Endpoint: POST /api/loyalty/transactions
        `tx_data` should be a dictionary containing all required parameters for the transaction body.
        Example: {"walletAddress": "0x...", "loyaltyRuleId": "...", "amount": "10", ...}
        organizationId and websiteId should be included in tx_data if required by the API in the body.
        """
        # Используем CREATE_TRANSACTION_ENDPOINT для POST запроса
        # Если orgId и websiteId требуются в ТЕЛЕ JSON, они должны быть в `tx_data`
        # Если они требуются в URL query string для этого POST эндпоинта, то
        # CREATE_TRANSACTION_ENDPOINT нужно добавить в self._endpoints_with_org_site_in_query
        # и они добавятся в `params`.
        # Но для POST обычно параметры тела передаются через `json_data`.
        # Предполагаем, что org/web ID если нужны, то они в tx_data.
        
        # Клонируем tx_data, чтобы не изменять оригинал, если будем добавлять org/website ID
        final_tx_data = tx_data.copy()

        # Если API ожидает organizationId и websiteId в теле для этого эндпоинта,
        # и они не были переданы в tx_data, добавляем их из клиента.
        # Это нужно делать, только если API *требует* их в теле для POST /api/loyalty/transactions
        if self._organization_id and 'organizationId' not in final_tx_data:
            # Уточни по документации, нужны ли они в теле для этого POST запроса.
            # Если да, то раскомментируй следующие строки:
            # logger.debug(f"Adding organizationId to create_transaction payload for {self._client_name}")
            # final_tx_data['organizationId'] = self._organization_id
            pass # Пока не добавляем, предполагаем, что если они нужны, то переданы в tx_data
        
        if self._website_id and 'websiteId' not in final_tx_data:
            # Аналогично для websiteId
            # logger.debug(f"Adding websiteId to create_transaction payload for {self._client_name}")
            # final_tx_data['websiteId'] = self._website_id
            pass

        return await self._make_request("POST", CREATE_TRANSACTION_ENDPOINT, json_data=final_tx_data)

    async def reward_badge(self, badge_id: str, data: Dict[str, Any]) -> Optional[Dict]:
        """
        Rewards a badge to a user.
        Endpoint: POST /api/loyalty/badges/{id}/reward
        `data` typically contains `walletAddress`.
        Example: {"walletAddress": "0x..."}
        organizationId и websiteId (если нужны) для этого эндпоинта обычно идут в URL query params,
        так как BADGES_ENDPOINT находится в _endpoints_with_org_site_in_query.
        Если они нужны и в теле, их нужно добавить в `data`.
        """
        endpoint = f"{BADGES_ENDPOINT}/{badge_id}/reward"
        
        final_data = data.copy()
        # Если API ожидает organizationId и websiteId в теле для этого эндпоинта:
        # (аналогично create_transaction)
        # if self._organization_id and 'organizationId' not in final_data:
        #     final_data['organizationId'] = self._organization_id
        # if self._website_id and 'websiteId' not in final_data:
        #     final_data['websiteId'] = self._website_id
            
        return await self._make_request("POST", endpoint, json_data=final_data)
    
    async def complete_loyalty_rule(self, rule_id: str, data: Dict[str, Any]) -> Optional[Dict]:
        """
        Completes a loyalty rule for a user and potentially rewards them.
        Endpoint: POST /api/loyalty/rules/{id}/complete
        `data` should contain parameters like walletAddress, amount (optional override), etc.
        """
        endpoint = f"{RULES_ENDPOINT}/{rule_id}/complete"
        
        # Копируем data, чтобы не изменять оригинал, если будем добавлять org/website ID
        final_data = data.copy()

        # Если API ожидает organizationId и websiteId в теле для этого эндпоинта,
        # и они не были переданы в data, добавляем их из клиента.
        # УТОЧНИ по документации или тестированием, нужны ли они в теле для этого POST.
        # Обычно для таких операций они могут быть не нужны, если правило уже привязано к org/site.
        if self._organization_id and 'organizationId' not in final_data:
            # logger.debug(f"Adding organizationId to complete_loyalty_rule payload for {self._client_name}")
            # final_data['organizationId'] = self._organization_id
            pass # Пока предполагаем, что не нужны в теле, если не указано явно в документации для этого эндпоинта
        
        if self._website_id and 'websiteId' not in final_data:
            # logger.debug(f"Adding websiteId to complete_loyalty_rule payload for {self._client_name}")
            # final_data['websiteId'] = self._website_id
            pass
            
        return await self._make_request("POST", endpoint, json_data=final_data)