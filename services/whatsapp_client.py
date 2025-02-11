#whatsapp_client.py
import re
import time
import logging
import json
import aiohttp
import asyncio
from typing import Optional, Dict, Any, Union
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class MessageType(Enum):
    """Tipos de mensagens suportados."""
    TEXT = "text"
    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"
    LOCATION = "location"

@dataclass
class WhatsAppConfig:
    """Configuração para cliente WhatsApp."""
    api_key: str
    api_url: str
    instance: str
    max_retries: int = 3
    retry_delay: int = 1
    timeout: int = 30
    default_country_code: str = "55"

class WhatsAppError(Exception):
    """Exceção base para erros do WhatsApp."""
    pass

class MessageError(WhatsAppError):
    """Erro no envio de mensagem."""
    pass

class APIError(WhatsAppError):
    """Erro na API do WhatsApp."""
    pass

class WhatsAppClient:
    """Cliente para API do WhatsApp."""
    
    def __init__(self, config: WhatsAppConfig):
        """
        Inicializa o cliente WhatsApp.
        
        Args:
            config: Configurações do cliente
        """
        self.config = config
        self.api_url = config.api_url.rstrip('/')
        self.headers = {
            "apikey": config.api_key,
            "Content-Type": "application/json"
        }
        self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        """Lazy loading da sessão HTTP."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers)
        return self._session

    

    async def send_message(
        self,
        text: str,
        number: str,
        message_type: MessageType = MessageType.TEXT,
        delay: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
        simulate_typing: bool = True
    ) -> bool:
        """
        Envia mensagem via WhatsApp.
        """
        try:
            formatted_number = self._format_number(number)
            
            # Simula digitação se solicitado
            if simulate_typing and delay > 0:                
                await asyncio.sleep(delay / 1000)
            
            endpoint = self._get_endpoint(message_type)
            payload = self._build_payload(text, formatted_number, delay, metadata)
            
            return await self._make_request(endpoint, payload)

        except Exception as e:
            logger.error(f"Erro ao enviar mensagem: {e}")
            return False

    def _get_endpoint(self, message_type: MessageType) -> str:
        """Retorna o endpoint apropriado para o tipo de mensagem."""
        endpoints = {
            MessageType.TEXT: "message/sendText",
            MessageType.IMAGE: "message/sendImage",
            MessageType.DOCUMENT: "message/sendDocument",
            MessageType.AUDIO: "message/sendAudio",
            MessageType.VIDEO: "message/sendVideo",
            MessageType.LOCATION: "message/sendLocation"
        }
        return f"{self.api_url}/{endpoints[message_type]}/{self.config.instance}"

    def _build_payload(
        self,
        text: str,
        number: str,
        delay: int = 0,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Constrói o payload da mensagem."""
        payload = {
            "number": number,
            "text": text,
            "delay": delay,
            "presenceType": "composing"  # Adiciona status de digitação
        }
        if metadata:
            payload.update(metadata)
        return payload

    async def _make_request(self, url: str, payload: Dict[str, Any]) -> bool:
        """
        Faz requisição para a API com retry.
        """
        for attempt in range(self.config.max_retries):
            try:
                async with self.session.post(
                    url,
                    json=payload,
                    timeout=self.config.timeout
                ) as response:
                    response.raise_for_status()
                    result = await response.json()
                    
                    logger.debug(f"Resposta da API: {result}")
                    
                    if (result.get('key') and 
                        result.get('status') in ['PENDING', 'SENT', 'DELIVERED']):
                        return True
                    
                    error_msg = result.get('error', 'Desconhecido')
                    logger.error(f"API retornou erro: {error_msg}")
                    
                    if attempt < self.config.max_retries - 1:
                        continue
                    return False

            except Exception as e:
                logger.error(f"Erro na requisição (tentativa {attempt + 1}): {e}")
                if attempt == self.config.max_retries - 1:
                    return False

            wait_time = self.config.retry_delay * (2 ** attempt)
            await asyncio.sleep(wait_time)

        return False

    def _format_number(self, number: str) -> str:
        """
        Formata número para padrão WhatsApp.
        """
        number = re.sub(r"\D", "", number)
        if not number.startswith(self.config.default_country_code) and 10 <= len(number) <= 11:
            number = f"{self.config.default_country_code}{number}"
        return number

    def validate_number(self, number: str) -> bool:
        """Valida formato do número."""
        formatted = self._format_number(number)
        return bool(re.match(r'^\d{12,13}$', formatted))

    async def close(self):
        """Fecha a sessão HTTP."""
        if self._session and not self._session.closed:
            await self._session.close()

def create_whatsapp_client(
    api_key: str,
    api_url: str,
    instance: str,
    **kwargs
) -> WhatsAppClient:
    """Cria uma instância do cliente com configuração padrão."""
    config = WhatsAppConfig(
        api_key=api_key,
        api_url=api_url,
        instance=instance,
        **kwargs
    )
    return WhatsAppClient(config)