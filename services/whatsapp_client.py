#whatsapp_client.py
import re
import time
import logging
import json
import traceback
import aiohttp
import asyncio
from typing import Optional, Dict, Any, Union
from dataclasses import dataclass
from enum import Enum

# Reduzir logs das requisições HTTP
logging.getLogger('aiohttp').setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

class MessageType(Enum):
    """Tipos de mensagens suportados."""
    TEXT = "text"
    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"
    LOCATION = "location"
    STICKER = "sticker"  # Adicionado suporte para stickers
    REACTION = "reaction"  # Adicionado suporte para reações

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
            
            # Log para ajudar no diagnóstico
            logger.info(f"Preparando envio para número formatado: {formatted_number} (original: {number})")
            
            # Simula digitação se solicitado
            if simulate_typing and delay > 0:                
                await asyncio.sleep(delay / 1000)
            
            endpoint = self._get_endpoint(message_type)
            payload = self._build_payload(text, formatted_number, delay, metadata)
            
            # Log o payload completo
            logger.debug(f"Payload sendo enviado: {json.dumps(payload, ensure_ascii=False)}")
            
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
            MessageType.LOCATION: "message/sendLocation",
            MessageType.STICKER: "message/sendSticker",  # Adicionado endpoint para stickers
            MessageType.REACTION: "message/sendReaction"  # Corrigido endpoint para reações
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
                    response_text = await response.text()
                    logger.debug(f"Resposta bruta da API: {response_text}")
                    
                    if response.status >= 400:
                        logger.error(f"Erro HTTP {response.status}: {response_text}")
                        # Se for erro 400, adiciona mais detalhes
                        if response.status == 400:
                            logger.error(f"Bad Request para número: {payload.get('number')}. Verifique se este número está autorizado na plataforma.")
                    
                    # Continua tratando normalmente
                    try:
                        result = json.loads(response_text)
                    except:
                        logger.error("Resposta não é um JSON válido")
                        if attempt == self.config.max_retries - 1:
                            return False
                        continue
                    
                    if response.ok and (result.get('key') and 
                                        result.get('status') in ['PENDING', 'SENT', 'DELIVERED']):
                        return True
                    
                    error_msg = result.get('error', 'Desconhecido')
                    logger.error(f"API retornou erro: {error_msg}")
                    
                    if attempt < self.config.max_retries - 1:
                        continue
                    return False

            except aiohttp.ClientError as e:
                logger.error(f"Erro na requisição HTTP (tentativa {attempt + 1}): {e}")
                if attempt == self.config.max_retries - 1:
                    return False
            except Exception as e:
                logger.error(f"Erro inesperado (tentativa {attempt + 1}): {e}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                if attempt == self.config.max_retries - 1:
                    return False

            wait_time = self.config.retry_delay * (2 ** attempt)
            await asyncio.sleep(wait_time)

        return False

    def _format_number(self, number: str) -> str:
        """
        Formata número para padrão WhatsApp conforme a API espera.
        
        Possíveis formatos suportados:
        - "55DDNNNNNNNNN"
        - "55DDNNNNNNNNN@c.us" (algumas APIs precisam deste formato)
        """
        # Remove todos os caracteres não numéricos
        number = re.sub(r"\D", "", number)
        
        # Adiciona o código do país se necessário
        if not number.startswith(self.config.default_country_code) and 10 <= len(number) <= 11:
            number = f"{self.config.default_country_code}{number}"
        
        # IMPORTANTE: Verifique o formato que sua API específica espera
        # Se a API precisar do sufixo @c.us, descomente a linha abaixo:
        # number = f"{number}@c.us"
        
        logger.debug(f"Número formatado: {number}")
        return number

    def validate_number(self, number: str) -> bool:
        """Valida formato do número."""
        formatted = self._format_number(number)
        return bool(re.match(r'^\d{12,13}$', formatted))

    async def close(self):
        """Fecha a sessão HTTP."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def send_sticker(
        self,
        sticker_url: str,
        number: str,
        delay: float = 0
    ) -> bool:
        """
        Envia uma figurinha para o número de destino.
        
        Args:
            sticker_url: URL da figurinha (formato WebP)
            number: Número do destinatário
            delay: Tempo em segundos para simular digitação antes do envio
            
        Returns:
            bool: Sucesso do envio
        """
        try:
            # Normaliza o número
            formatted_number = self._format_number(number)
            logger.info(f"Enviando figurinha para {formatted_number}")
            
            # Simula digitação se necessário
            if delay > 0:
                logger.debug(f"Simulando digitação por {delay}s")
                await asyncio.sleep(delay / 1000)  # Converte ms para segundos
                
            # Construir endpoint para figurinha
            endpoint = self._get_endpoint(MessageType.STICKER)
            
            # Construir payload para figurinha
            # Formato segundo a documentação: https://docs.evolution-api.com/send-message-sticker
            payload = {
                "number": f"{formatted_number}@s.whatsapp.net",
                "sticker": sticker_url
            }
            
            # Log detalhado para debug
            logger.info(f"Enviando figurinha: '{sticker_url}' para {formatted_number}")
            logger.debug(f"Payload completo para sticker: {json.dumps(payload, ensure_ascii=False)}")
            logger.debug(f"Endpoint: {endpoint}")
            
            # Envia a requisição
            result = await self._make_request(endpoint, payload)
            
            if not result:
                logger.error("Falha ao enviar sticker - resposta negativa da API")
                
            return result
            
        except Exception as e:
            logger.error(f"Erro ao enviar figurinha: {e}")
            logger.error(f"Detalhes adicionais: URL={sticker_url}, número={number}")
            return False

    async def send_reaction(
        self,
        message_id: str,
        emoji: str,
        number: str
    ) -> bool:
        """
        Envia uma reação a uma mensagem específica.
        
        Args:
            message_id: ID da mensagem a reagir
            emoji: Emoji de reação (ex: "👍", "❤️", "😂")
            number: Número do destinatário
            
        Returns:
            bool: Sucesso do envio
        """
        try:
            formatted_number = self._format_number(number)
            logger.info(f"Enviando reação '{emoji}' para mensagem {message_id}")
            
            # Construir endpoint
            endpoint = self._get_endpoint(MessageType.REACTION)
            
            # Obter timestamp atual em ms
            current_timestamp_ms = str(int(time.time() * 1000))
            
            # Construir payload para reação (formato corrigido com base nos erros)
            payload = {
                "key": {
                    "remoteJid": f"{formatted_number}@s.whatsapp.net",
                    "fromMe": False,
                    "id": message_id
                },
                "reaction": emoji
            }
            
            # Log detalhado para debug
            logger.info(f"Enviando reação usando payload corrigido")
            logger.debug(f"Payload para reação: {json.dumps(payload, ensure_ascii=False)}")
            logger.debug(f"Endpoint: {endpoint}")
            
            # Envia a requisição
            result = await self._make_request(endpoint, payload)
            
            if not result:
                logger.error("Falha ao enviar reação - resposta negativa da API")
                
            return result
            
        except Exception as e:
            logger.error(f"Erro ao enviar reação: {e}")
            return False

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