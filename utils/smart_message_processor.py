#smart_message_processor.py
import random
import asyncio
import logging
import os
from typing import List, Optional
from dataclasses import dataclass
from langchain.prompts import PromptTemplate

from services.whatsapp_client import WhatsAppClient, create_whatsapp_client
from services.llm import llm_groq

logger = logging.getLogger(__name__)

@dataclass
class MessageProcessorConfig:
    """Configuração para processamento de mensagens."""
    min_delay: int = 1000  # ms
    max_delay: int = 3000  # ms
    chars_per_second: float = 60.0
    variation_percent: float = 0.1
    max_chunk_size: int = 1000
    question_pause: float = 1  # segundos
    exclamation_pause: float = 0.8  # segundos
    default_pause: float = 0.5 # segundos

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
        self.format_prompt = PromptTemplate.from_template("""
        Formate o texto a seguir em partes naturais para envio via WhatsApp.
        Regras:
        - Divida em partes que façam sentido semanticamente
        - Mantenha o contexto em cada parte
        - Retorne apenas as partes separadas por |||
        - Não adicione numeração ou marcadores
        - Use um único asterisco para negrito (Ex: palavra)
        - Não use emojis
        - Quebre mensagens longas em partes menores
        
        Texto: {text}
        """)

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

    async def _format_message(self, text: str) -> List[str]:
        """
        Formata mensagem em chunks usando IA.
        """
        try:
            # Formata o prompt corretamente
            formatted_prompt = self.format_prompt.format(text=text)
            
            # Faz a chamada ao LLM
            response = await llm_groq.ainvoke(formatted_prompt)
            
            # Extrai o conteúdo
            formatted_text = response.content if hasattr(response, 'content') else str(response)
            
            # Divide e limpa os chunks
            chunks = [
                chunk.strip()
                for chunk in formatted_text.split("|||")
                if chunk and chunk.strip()
            ]
            
            if not chunks:
                logger.warning("Nenhum chunk válido gerado, usando texto original")
                return [text]
                
            return chunks
            
        except Exception as e:
            logger.error(f"Erro na formatação: {e}", exc_info=True)
        return [text]

    async def send_message(self, text: str, number: str) -> bool:
        """
        Processa e envia mensagem de forma natural.
        """
        try:
            # Formata mensagem em chunks
            chunks = await self._format_message(text)
            logger.debug(f"Chunks gerados: {chunks}")
            
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

# Cria instância do processador
whatsapp_client = create_whatsapp_client(
    api_key=os.getenv("EVOLUTION_API_KEY"),
    api_url=os.getenv("EVOLUTION_API_URL"),
    instance=os.getenv("INSTANCE_NAME", "nerai")
)

message_processor = SmartMessageProcessor(whatsapp_client)

# Função de interface para manter compatibilidade
async def send_message_in_chunks(text: str, number: str) -> bool:
    """Função de interface para envio de mensagens."""
    return await message_processor.send_message(text, number)