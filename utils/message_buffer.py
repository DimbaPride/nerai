#message_buffer.py
import time
import asyncio
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from agents.agent_setup import agent_executor
from utils.smart_message_processor import send_message_in_chunks
from utils.conversation_manager import conversation_manager  # Adicionado import


logger = logging.getLogger(__name__)

# Dicionário global para armazenar o status de presença e últimas atividades
presence_status: Dict[str, Dict[str, Any]] = {}

def update_presence(number: str, presence_data: Dict[str, Any]) -> None:
    """
    Atualiza o status de presença de um número baseado no webhook recebido.
    Armazena também o timestamp da última atualização.
    """
    try:
        normalized_number = number.split('@')[0]
        last_known = presence_data.get("lastKnownPresence", "available")
        
        presence_status[normalized_number] = {
            "status": last_known,
            "last_update": time.time()
        }
        
        logger.debug(f"Status de presença atualizado para {normalized_number}: {last_known}")
        
        # Atualiza o timestamp do buffer se necessário
        from utils.message_buffer import message_buffer
        if normalized_number in message_buffer._message_buffer and last_known in {"recording", "composing"}:
            message_buffer._message_buffer[normalized_number]["last_activity"] = time.time()
            logger.debug(f"Timestamp do buffer atualizado para {normalized_number}")
    except Exception as e:
        logger.error(f"Erro ao atualizar presença: {e}")

async def is_user_available(number: str) -> bool:
    """
    Verifica se o usuário não está digitando ou gravando.
    Considera também o tempo desde a última atualização de status.
    """
    try:
        normalized_number = number.split('@')[0]
        user_presence = presence_status.get(normalized_number, {})
        current_status = user_presence.get("status", "available")
        last_update = user_presence.get("last_update", 0)
        
        # Se não recebermos atualização de status por mais de 30 segundos,
        # consideramos o usuário como disponível
        if time.time() - last_update > 30:
            return True
            
        return current_status not in ["composing", "recording"]
    except Exception as e:
        logger.error(f"Erro ao verificar disponibilidade: {e}")
        return True

async def wait_for_user_available(number: str, timeout: int = 5) -> bool:
    """
    Aguarda até que o usuário esteja disponível por 'timeout' segundos consecutivos.
    Implementa verificação contínua com intervalo curto para maior precisão.
    """
    try:
        normalized_number = number.split('@')[0]
        start_time = None
        check_interval = 0.1  # 100ms de intervalo para verificação
        
        async def check_activity():
            nonlocal start_time
            if await is_user_available(normalized_number):
                if start_time is None:
                    start_time = time.time()
                    logger.debug(f"Iniciando contagem de disponibilidade para {normalized_number}")
                elif time.time() - start_time >= timeout:
                    logger.debug(f"Usuário {normalized_number} disponível por {timeout} segundos")
                    return True
            else:
                if start_time is not None:
                    logger.debug(f"Reiniciando contagem de disponibilidade para {normalized_number}")
                start_time = None
            return False

        while True:
            if await check_activity():
                return True
            await asyncio.sleep(check_interval)
            
    except Exception as e:
        logger.error(f"Erro ao aguardar disponibilidade: {e}")
        return True

async def send_message_with_presence_check(message: str, number: str) -> bool:
    """
    Envia a mensagem apenas quando o usuário não estiver digitando/gravando
    e após esperar o tempo de timeout. Implementa retry em caso de falha.
    """
    try:
        max_retries = 3
        retry_delay = 1.0
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Aguardando disponibilidade do usuário {number}...")
                await wait_for_user_available(number, timeout=10)
                
                # Verifica novamente antes de enviar
                if await is_user_available(number):
                    logger.info(f"Enviando mensagem para {number}")
                    return await send_message_in_chunks(message, number)
                
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    
            except Exception as e:
                logger.error(f"Tentativa {attempt + 1} falhou: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    
        return False
    except Exception as e:
        logger.error(f"Erro ao enviar mensagem: {e}")
        return False

@dataclass
class MessageBufferConfig:
    """
    Configuração para o buffer de mensagens.
    """
    max_buffer_size: int = 100
    check_interval: float = 0.1
    presence_timeout: int = 2

@dataclass
class ConversationMessage:
    """
    Representa uma mensagem de uma conversa.
    """
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)

class MessageBuffer:
    """
    Gerencia o buffer de mensagens e o histórico de conversas.
    """
    def __init__(self, config: Optional[MessageBufferConfig] = None):
        self.config = config or MessageBufferConfig()
        self._conversation_history: Dict[str, List[ConversationMessage]] = {}
        self._message_buffer: Dict[str, Dict[str, Any]] = {}

    def _initialize_buffer(self, number: str) -> None:
        if number not in self._message_buffer:
            self._message_buffer[number] = {
                "messages": [],
                "last_activity": time.time(),
                "processing": False
            }

    def _cleanup_buffer(self, number: str) -> None:
        if number in self._message_buffer:
            self._message_buffer.pop(number)

    def add_to_history(self, number: str, role: str, content: str) -> None:
        """
        Adiciona mensagem ao histórico local e ao conversation_manager.
        """
        # Adiciona ao histórico local do MessageBuffer
        if number not in self._conversation_history:
            self._conversation_history[number] = []
        
        self._conversation_history[number].append(
            ConversationMessage(role=role, content=content)
        )
        
        # Adiciona também ao conversation_manager
        conversation_manager.add_message(number, content, role=role)
        
        logger.debug(f"Mensagem adicionada aos históricos para {number}")
        logger.debug(f"Local history size: {len(self._conversation_history[number])}")

    async def _process_message(self, message: str, number: str) -> bool:
        try:
            self.add_to_history(number, "user", message)
            
            # Obter o histórico do conversation_manager
            history = conversation_manager.get_history(number)
            
            # Usar await com ainvoke
            result = await agent_executor.ainvoke({
                "input": message,
                "history": history,
                "whatsapp_number": number
            })
            
            response = result.get("output", "Desculpe, ocorreu um erro. Tente novamente.")
            self.add_to_history(number, "assistant", response)
            
            # Usa o sistema de verificação de presença para enviar a resposta
            return await send_message_with_presence_check(response, number)
            
        except Exception as e:
            logger.error(f"Erro no processamento: {e}")
            await send_message_with_presence_check("Desculpe, ocorreu um erro. Tente novamente.", number)
            return False

    async def _wait_and_process(self, number: str) -> None:
        """
        Aguarda e processa mensagens quando o usuário estiver disponível.
        """
        try:
            while True:
                buffer = self._message_buffer.get(number)
                if not buffer:
                    return

                last_activity_snapshot = buffer["last_activity"]
                
                # Aguarda o usuário ficar disponível
                await wait_for_user_available(number, timeout=self.config.presence_timeout)
                
                buffer = self._message_buffer.get(number)
                if not buffer:
                    return
                    
                if buffer["last_activity"] > last_activity_snapshot:
                    logger.debug(f"Nova atividade detectada para {number}, reiniciando espera")
                    continue
                else:
                    break

            buffer = self._message_buffer.get(number)
            if not buffer:
                return
                
            messages = buffer["messages"]
            self._cleanup_buffer(number)
            
            if messages:
                full_context = " ".join(messages)
                logger.info(f"Processando contexto para {number}: {full_context}")
                await self._process_message(full_context, number)
                
        except Exception as e:
            logger.error(f"Erro no wait_and_process: {e}")
            self._cleanup_buffer(number)

    async def handle_message(self, message: str, number: str) -> None:
        try:
            if number == '5511911043825':
                logger.info(f"Mensagem do agente {number} ignorada.")
                return

            self._initialize_buffer(number)
            buffer = self._message_buffer[number]

            if len(buffer["messages"]) >= self.config.max_buffer_size:
                logger.warning(f"Buffer cheio para {number}. Limpando...")
                self._cleanup_buffer(number)
                self._initialize_buffer(number)
                buffer = self._message_buffer[number]

            buffer["messages"].append(message)
            buffer["last_activity"] = time.time()

            if not buffer["processing"]:
                buffer["processing"] = True
                asyncio.create_task(self._wait_and_process(number))
                
        except Exception as e:
            logger.error(f"Erro no buffer de mensagens: {str(e)}")
            await send_message_with_presence_check("Desculpe, ocorreu um erro. Tente novamente.", number)
            self._cleanup_buffer(number)

# Instância global do buffer
message_buffer = MessageBuffer()

async def handle_message_with_buffer(message: str, number: str) -> None:
    await message_buffer.handle_message(message, number)

async def process_message(message: str, number: str) -> bool:
    return await message_buffer._process_message(message, number)