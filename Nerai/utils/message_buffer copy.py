#message_buffer.py
import time
import asyncio
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from agents.agent_setup import agent_executor

from utils.smart_message_processor import send_message_in_chunks
from utils.conversation_manager import conversation_manager

logger = logging.getLogger(__name__)

@dataclass
class MessageBufferConfig:
    """Configuração para buffer de mensagens."""
    buffer_time: int = 10  # segundos
    max_buffer_size: int = 100
    error_message: str = "Desculpe, ocorreu um erro. Tente novamente."

@dataclass
class ConversationMessage:
    """Representa uma mensagem na conversa."""
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)

class MessageBuffer:
    """Gerencia buffer de mensagens e histórico de conversas."""
    def __init__(self, config: Optional[MessageBufferConfig] = None):
        self.config = config or MessageBufferConfig()
        self._conversation_history: Dict[str, List[ConversationMessage]] = {}
        self._message_buffer: Dict[str, Dict[str, Any]] = {}

    def _initialize_buffer(self, number: str) -> None:
        """Inicializa buffer para um número se necessário."""
        if number not in self._message_buffer:
            self._message_buffer[number] = {
                "messages": [],
                "last_activity": time.time(),
                "processing": False
            }

    def _cleanup_buffer(self, number: str) -> None:
        """Remove buffer de um número."""
        if number in self._message_buffer:
            self._message_buffer.pop(number)

    async def _process_message(self, message: str, number: str) -> bool:
        """Processa uma mensagem individual."""
        try:
            # Adiciona mensagem ao histórico usando o conversation_manager
            conversation_manager.add_message(number, message, role='user')
            
            # Obtém histórico completo para o agente
            history = conversation_manager.get_history(number)
            logger.debug(f"Histórico para {number}: {history}")
            
            # Invoca o agente com o histórico
            result = await agent_executor.ainvoke({
                "input": message,
                "history": history
            })
            
            # Processa resposta
            response = result.get("output", self.config.error_message)
            
            # Adiciona resposta ao histórico
            conversation_manager.add_message(number, response, role='assistant')
            
            # Log da resposta para debug
            logger.debug(f"Resposta gerada para {number}: {response}")
            
            # Envia a resposta
            return await send_message_in_chunks(response, number)
            
        except Exception as e:
            logger.error(f"Erro no processamento: {str(e)}", exc_info=True)
            await send_message_in_chunks(self.config.error_message, number)
            return False

    async def handle_message(self, message: str, number: str) -> None:
        """Manipula nova mensagem com sistema de buffer."""
        try:
            # Ignora mensagens do próprio agente
            if number == '5511911043825':  # Número do agente
                logger.info(f"Mensagem do agente {number} ignorada.")
                return

            # Inicializa ou atualiza buffer
            self._initialize_buffer(number)
            buffer = self._message_buffer[number]
            
            # Verifica tamanho do buffer
            if len(buffer["messages"]) >= self.config.max_buffer_size:
                logger.warning(f"Buffer cheio para {number}. Limpando...")
                self._cleanup_buffer(number)
                self._initialize_buffer(number)
                buffer = self._message_buffer[number]
            
            # Adiciona mensagem ao buffer
            buffer["messages"].append(message)
            buffer["last_activity"] = time.time()

            # Se já está processando, retorna
            if buffer["processing"]:
                return

            # Marca como processando
            buffer["processing"] = True

            try:
                # Aguarda próximas mensagens
                while (time.time() - buffer["last_activity"] < self.config.buffer_time):
                    await asyncio.sleep(1)

                # Recupera e processa mensagens
                messages = buffer["messages"]
                self._cleanup_buffer(number)

                if messages:
                    full_context = " ".join(messages)
                    logger.info(f"Processando contexto de {number}: {full_context}")
                    await self._process_message(full_context, number)

            finally:
                # Garante que flag de processamento seja limpa
                if number in self._message_buffer:
                    self._message_buffer[number]["processing"] = False
                    
        except Exception as e:
            logger.error(f"Erro no buffer de mensagens: {str(e)}")
            await send_message_in_chunks(self.config.error_message, number)
            self._cleanup_buffer(number)

# Cria instância global do buffer
message_buffer = MessageBuffer()

# Funções de interface para manter compatibilidade
async def handle_message_with_buffer(message: str, number: str) -> None:
    """Função de interface para manipular mensagens."""
    await message_buffer.handle_message(message, number)

async def process_message(message: str, number: str) -> bool:
    """Função de interface para processar mensagens."""
    return await message_buffer._process_message(message, number)