# utils/conversation_manager.py
import logging
import time
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class Message:
    """Representa uma mensagem na conversa."""
    role: str  # 'assistant' ou 'user'
    content: str
    timestamp: float = field(default_factory=time.time)

@dataclass
class LeadContext:
    """Contexto do lead."""
    name: str
    email: str
    phone: str
    company: str
    business: str
    timestamp: float = field(default_factory=time.time)

class ConversationManager:
    """Gerencia o histórico das conversas."""
    
    def __init__(self):
        self._conversations: Dict[str, List[Message]] = {}
        self._lead_context: Dict[str, LeadContext] = {}
    
    def normalize_phone(self, phone_number: str) -> str:
        """
        Padroniza o formato do número de telefone.
        Remove caracteres especiais e garante o formato correto.
        """
        # 1. Primeiro limpa todos os caracteres especiais
        phone = phone_number.strip()
        phone = phone.replace("+", "")
        phone = phone.replace("@c.us", "")
        phone = phone.replace("(", "").replace(")", "")
        phone = phone.replace("-", "").replace(" ", "")
        
        # 2. Remove qualquer "55" do início para evitar duplicação
        while phone.startswith("55"):
            phone = phone[2:]
        
        # 3. Adiciona o prefixo 55 uma única vez
        if not phone.startswith("55"):
            phone = "55" + phone
        
        # 4. Adiciona 9 após DDD se necessário (para números de 8 dígitos)
        if len(phone) == 12:  # 55 + DDD + 8 dígitos
            ddd = phone[2:4]
            numero = phone[4:]
            phone = "55" + ddd + "9" + numero
        
        logger.debug(f"Número normalizado de {phone_number} para {phone}")
        return phone
        
    def add_message(self, number: str, content: str, role: str = 'assistant') -> None:
        """Adiciona uma mensagem ao histórico."""
        number = self.normalize_phone(number)
        if number not in self._conversations:
            self._conversations[number] = []
            
        self._conversations[number].append(Message(
            role=role,
            content=content,
            timestamp=time.time()
        ))
        
        # Limite de 50 mensagens por conversa
        if len(self._conversations[number]) > 50:
            self._conversations[number] = self._conversations[number][-50:]
            
        logger.debug(f"Mensagem adicionada para {number}. Total: {len(self._conversations[number])}")

    def add_lead_context(self, number: str, context: Dict[str, str]) -> None:
        """Adiciona ou atualiza o contexto do lead."""
        try:
            number = self.normalize_phone(number)
            self._lead_context[number] = LeadContext(
                name=context.get('nome', ''),
                email=context.get('email', ''),
                phone=number,
                company=context.get('empresa', ''),
                business=context.get('ramo', '')
            )
            logger.debug(f"Contexto adicionado para {number}: {context}")
        except Exception as e:
            logger.error(f"Erro ao adicionar contexto: {e}")
    
    def get_history(self, number: str) -> str:
        """Recupera o histórico formatado para o agente."""
        try:
            number = self.normalize_phone(number)
            history_parts = []
            
            # Adiciona contexto se disponível
            if number in self._lead_context:
                lead = self._lead_context[number]
                logger.debug(f"Contexto encontrado para {number}: {lead}")
                context = (
                    f"Contexto do Lead:\n"
                    f"Nome: {lead.name}\n"
                    f"Empresa: {lead.company}\n"
                    f"Ramo: {lead.business}\n"
                )
                history_parts.append(context)
            else:
                logger.debug(f"Nenhum contexto encontrado para {number}")
            
            # Adiciona mensagens
            if number in self._conversations:
                for msg in self._conversations[number]:
                    role = "Livia" if msg.role == 'assistant' else "Cliente"
                    history_parts.append(f"{role}: {msg.content}")
            
            full_history = "\n".join(history_parts)
            logger.debug(f"Histórico completo para {number}: {full_history}")
            return full_history
            
        except Exception as e:
            logger.error(f"Erro ao obter histórico: {e}")
            return ""
        
    def clear_history(self, number: str) -> None:
        """Limpa o histórico de um número específico."""
        number = self.normalize_phone(number)
        if number in self._conversations:
            del self._conversations[number]
        if number in self._lead_context:
            del self._lead_context[number]
        logger.debug(f"Histórico limpo para {number}")

    def get_lead_context(self, number: str) -> Optional[LeadContext]:
        """Retorna o contexto do lead se existir."""
        number = self.normalize_phone(number)
        return self._lead_context.get(number)

# Instância global do gerenciador
conversation_manager = ConversationManager()