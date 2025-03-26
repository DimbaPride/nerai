# utils/conversation_manager.py
import logging
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import traceback
from datetime import datetime

from services.context_manager import context_manager

logger = logging.getLogger(__name__)

@dataclass
class Message:
    """Representa uma mensagem na conversa."""
    role: str  # 'assistant' ou 'user'
    content: str
    timestamp: float = field(default_factory=time.time)

class ConversationManager:
    """Gerencia o histórico das conversas usando Supabase para persistência."""
    
    def __init__(self):
        # Não mantemos mais cópias em memória, tudo vai para o Supabase
        pass
    
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
        """Adiciona uma mensagem ao histórico no Supabase."""
        try:
            number = self.normalize_phone(number)
            
            # Obtenha o contexto atual do Supabase
            current_context = context_manager.get_context(number) or {}
            
            # Inicialize a lista de mensagens se não existir
            messages = current_context.get('messages', [])
            
            # Adicione a nova mensagem
            new_message = {
                'role': role,
                'content': content,
                'timestamp': time.time()
            }
            messages.append(new_message)
            
            # Limite de 50 mensagens
            if len(messages) > 50:
                messages = messages[-50:]
                
            # Atualiza o contexto com as mensagens atualizadas
            current_context['messages'] = messages
            
            # Salva no Supabase
            context_manager.save_context(number, current_context)
            
            logger.debug(f"Mensagem adicionada para {number} no Supabase. Total: {len(messages)}")
        except Exception as e:
            logger.error(f"Erro ao adicionar mensagem: {e}")
            logger.debug(traceback.format_exc())

    def add_lead_context(self, number: str, context: Dict[str, str]) -> None:
        """Adiciona ou atualiza o contexto do lead no Supabase."""
        try:
            number = self.normalize_phone(number)
            
            # Obtenha o contexto atual do Supabase
            current_context = context_manager.get_context(number) or {}
            
            # Atualize com os novos dados do lead
            lead_data = {
                'nome': context.get('nome', ''),
                'email': context.get('email', ''),
                'telefone': number,
                'empresa': context.get('empresa', ''),
                'ramo': context.get('ramo', ''),
                'lead_updated_at': datetime.now().isoformat()
            }
            
            # Mesclar com o contexto existente
            current_context.update(lead_data)
            
            # Salvar contexto atualizado no Supabase
            context_manager.save_context(number, current_context)
            
            logger.debug(f"Contexto de lead adicionado no Supabase para {number}: {lead_data}")
        except Exception as e:
            logger.error(f"Erro ao adicionar contexto de lead: {e}")
            logger.debug(traceback.format_exc())
    
    def get_history(self, number: str) -> str:
        """Recupera o histórico formatado do Supabase para o agente."""
        try:
            number = self.normalize_phone(number)
            history_parts = []
            
            # Obtenha o contexto completo do Supabase
            full_context = context_manager.get_context(number) or {}
            
            # Adiciona contexto do lead se disponível
            if 'nome' in full_context:
                context = (
                    f"Contexto do Lead:\n"
                    f"Nome: {full_context.get('nome', '')}\n"
                    f"Empresa: {full_context.get('empresa', '')}\n"
                    f"Ramo: {full_context.get('ramo', '')}\n"
                    f"Email: {full_context.get('email', '')}\n"
                )
                history_parts.append(context)
                logger.debug(f"Adicionado contexto ao histórico para {number}")
            else:
                logger.debug(f"Nenhum contexto encontrado para {number}")
            
            # Adiciona dados de agendamento se disponíveis
            if 'booking_id' in full_context or 'attendee_id' in full_context:
                booking_info = (
                    f"Informações de Agendamento:\n"
                    f"Booking ID: {full_context.get('booking_id', 'N/A')}\n"
                    f"Attendee ID: {full_context.get('attendee_id', 'N/A')}\n"
                )
                history_parts.append(booking_info)
                logger.debug(f"Adicionado informações de agendamento para {number}")
            
            # Adiciona mensagens
            messages = full_context.get('messages', [])
            for msg in messages:
                role = "Livia" if msg.get('role') == 'assistant' else "Cliente"
                history_parts.append(f"{role}: {msg.get('content', '')}")
            logger.debug(f"Adicionadas {len(messages)} mensagens ao histórico para {number}")
            
            full_history = "\n".join(history_parts)
            logger.debug(f"Histórico completo do Supabase para {number}")
            return full_history
            
        except Exception as e:
            logger.error(f"Erro ao obter histórico do Supabase: {e}")
            logger.debug(traceback.format_exc())
            return ""
        
    def clear_history(self, number: str) -> None:
        """Limpa o histórico de um número específico no Supabase."""
        try:
            number = self.normalize_phone(number)
            
            # Remover apenas as mensagens, mantendo dados do lead e agendamento
            current_context = context_manager.get_context(number) or {}
            if 'messages' in current_context:
                current_context['messages'] = []
                
            context_manager.save_context(number, current_context)
            logger.debug(f"Histórico de mensagens limpo para {number} no Supabase")
        except Exception as e:
            logger.error(f"Erro ao limpar histórico no Supabase: {e}")
            logger.debug(traceback.format_exc())

    def get_lead_context(self, number: str) -> Optional[Dict[str, Any]]:
        """Retorna o contexto do lead do Supabase se existir."""
        number = self.normalize_phone(number)
        try:
            context = context_manager.get_context(number) or {}
            if 'nome' in context:
                return {
                    'name': context.get('nome', ''),
                    'email': context.get('email', ''),
                    'phone': number,
                    'company': context.get('empresa', ''),
                    'business': context.get('ramo', '')
                }
            return None
        except Exception as e:
            logger.error(f"Erro ao obter contexto de lead do Supabase: {e}")
            logger.debug(traceback.format_exc())
            return None

# Instância global do gerenciador
conversation_manager = ConversationManager()