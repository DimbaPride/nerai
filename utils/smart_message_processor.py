#smart_message_processor.py
import random
import asyncio
import logging
import os
from typing import List, Optional
from dataclasses import dataclass

from services.whatsapp_client import WhatsAppClient, create_whatsapp_client

logger = logging.getLogger(__name__)

@dataclass
class MessageProcessorConfig:
    """Configuração para processamento de mensagens."""
    min_delay: int = 1000  # ms
    max_delay: int = 3000  # ms
    chars_per_second: float = 60.0
    variation_percent: float = 0.1
    max_chunk_size: int = 1000  # Tamanho máximo por mensagem
    question_pause: float = 1.0  # segundos
    exclamation_pause: float = 0.8  # segundos
    default_pause: float = 0.5  # segundos

class SmartMessageProcessor:
    """Processador inteligente de mensagens."""
    
    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        config: Optional[MessageProcessorConfig] = None
    ):
        """
        Inicializa o processador de mensagens.
        
        Args:
            whatsapp_client: Cliente WhatsApp para envio
            config: Configurações opcionais
        """
        self.client = whatsapp_client
        self.config = config or MessageProcessorConfig()

    def calculate_typing_delay(self, text_length: int) -> int:
        """
        Calcula delay para simular digitação natural.
        
        Args:
            text_length: Comprimento do texto
            
        Returns:
            int: Delay em milissegundos
        """
        try:
            base_delay = (text_length / self.config.chars_per_second) * 1000
            variation = base_delay * self.config.variation_percent
            delay = base_delay + random.uniform(-variation, variation)
            return int(max(
                self.config.min_delay,
                min(delay, self.config.max_delay)
            ))
        except Exception:
            return self.config.min_delay

    def _calculate_pause(self, chunk: str) -> float:
        """
        Calcula pausa apropriada após chunk.
        
        Args:
            chunk: Texto do chunk
            
        Returns:
            float: Pausa em segundos
        """
        if '?' in chunk:
            return self.config.question_pause
        elif '!' in chunk:
            return self.config.exclamation_pause
        return self.config.default_pause

    async def send_message(self, text: str, number: str) -> bool:
        """
        Envia mensagem, dividida por quebras de linha duplas.
        
        Args:
            text: Texto a ser enviado
            number: Número do destinatário
            
        Returns:
            bool: Sucesso do envio
        """
        try:
            # Dividir por parágrafos ou quebras de linha duplas - é a forma mais natural
            chunks = [chunk.strip() for chunk in text.split("\n\n") if chunk.strip()]
            
            # Se não houver quebras naturais, deixar como uma única mensagem
            if not chunks:
                chunks = [text]
            
            logger.debug(f"Mensagem dividida em {len(chunks)} partes")
            
            # Envia cada chunk
            for i, chunk in enumerate(chunks):
                # Calcula delay de digitação
                typing_delay = self.calculate_typing_delay(len(chunk))
                logger.debug(f"Delay calculado para chunk {i+1}: {typing_delay}ms")
                
                # Envia mensagem com simulação de digitação
                try:
                    success = await self.client.send_message(
                        text=chunk,
                        number=number,
                        delay=typing_delay,
                        simulate_typing=True
                    )
                    if not success:
                        logger.error(f"Falha ao enviar chunk {i+1}")
                        return False
                        
                except Exception as e:
                    logger.error(f"Erro ao enviar chunk {i+1}: {e}")
                    return False
                    
                # Pausa entre chunks se não for o último
                if i < len(chunks) - 1:
                    pause = self._calculate_pause(chunk)
                    logger.debug(f"Pausa entre chunks: {pause}s")
                    await asyncio.sleep(pause)
                    
            return True
            
        except Exception as e:
            logger.error(f"Erro no envio: {e}", exc_info=True)
            return False

    async def send_sticker(self, sticker_url: str, number: str) -> bool:
        """
        Envia uma figurinha para o usuário.
        
        Args:
            sticker_url: URL da figurinha (formato WebP)
            number: Número do destinatário
            
        Returns:
            bool: Sucesso do envio
        """
        try:
            # Calcula delay para simular naturalmente o envio
            delay = self.calculate_typing_delay(50)  # Usamos valor fixo só para simular alguma demora
            logger.debug(f"Enviando figurinha para {number}")
            
            # Enviar a figurinha usando o cliente WhatsApp
            return await self.client.send_sticker(
                sticker_url=sticker_url,
                number=number,
                delay=delay
            )
        except Exception as e:
            logger.error(f"Erro ao enviar figurinha: {e}")
            return False

    async def send_reaction(self, message_id: str, emoji: str, number: str) -> bool:
        """
        Envia uma reação a uma mensagem.
        
        Args:
            message_id: ID da mensagem a reagir
            emoji: Emoji de reação (👍, ❤️, 😂, etc.)
            number: Número do destinatário
            
        Returns:
            bool: Sucesso do envio
        """
        try:
            logger.debug(f"Enviando reação '{emoji}' para mensagem {message_id}")
            
            # Enviar a reação usando o cliente WhatsApp
            return await self.client.send_reaction(
                message_id=message_id,
                emoji=emoji,
                number=number
            )
        except Exception as e:
            logger.error(f"Erro ao enviar reação: {e}")
            return False

# Cria instância do processador
whatsapp_client = create_whatsapp_client(
    api_key=os.getenv("EVOLUTION_API_KEY"),
    api_url=os.getenv("EVOLUTION_API_URL"),
    instance=os.getenv("INSTANCE_NAME", "nerai")
)

message_processor = SmartMessageProcessor(whatsapp_client)

# Funções de interface para manter compatibilidade
async def send_message_in_chunks(text: str, number: str) -> bool:
    """Função de interface para envio de mensagens."""
    return await message_processor.send_message(text, number)

async def send_sticker_to_user(sticker_url: str, number: str) -> bool:
    """Função de interface para envio de figurinhas."""
    return await message_processor.send_sticker(sticker_url, number)

async def send_reaction_to_message(message_id: str, emoji: str, number: str) -> bool:
    """Função de interface para envio de reações."""
    return await message_processor.send_reaction(message_id, emoji, number)