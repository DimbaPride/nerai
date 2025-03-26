import logging
import traceback
import threading
from typing import Dict, Any, Optional
from datetime import datetime

# Reduzir verbosidade dos logs de Supabase
logging.getLogger('postgrest').setLevel(logging.ERROR)
logging.getLogger('httpcore.http2').setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

class SupabaseContextManager:
    """
    Gerencia o contexto das conversas com persistência no Supabase.
    """
    
    def __init__(self):
        """Inicializa o gerenciador de contexto usando Supabase."""
        try:
            from config import SUPABASE_CLIENT
            self.supabase = SUPABASE_CLIENT
            self.table_name = "context"
            self._current_number = None
            self._lock = threading.Lock()  # Para operações thread-safe
            
            # Verificar conexão inicial
            test = self.supabase.table(self.table_name).select("count").limit(1).execute()
            logger.info(f"Gerenciador de contexto inicializado com Supabase: {test}")
            
        except Exception as e:
            logger.error(f"Erro ao inicializar SupabaseContextManager: {e}")
            logger.error(traceback.format_exc())
            raise
    
    def set_current_number(self, phone_number: str) -> None:
        """Define o número atual de WhatsApp."""
        if not phone_number:
            logger.warning("Tentativa de definir número em branco")
            return
            
        with self._lock:
            self._current_number = phone_number
            logger.debug(f"Número atual definido: {phone_number}")
            
            # Persistir no Supabase usando chave especial
            try:
                self.supabase.table(self.table_name).upsert({
                    "phone_number": "CURRENT_WHATSAPP",
                    "context": {"value": phone_number},
                    "updated_at": datetime.now().isoformat()
                }).execute()
                logger.debug(f"Número atual persistido no Supabase: {phone_number}")
            except Exception as e:
                logger.warning(f"Erro ao persistir número atual no Supabase: {e}")
        
    def get_current_number(self) -> Optional[str]:
        """Obtém o número atual de WhatsApp."""
        # Primeiro tenta da memória para performance
        if self._current_number:
            return self._current_number
            
        # Caso não exista em memória, tenta recuperar do Supabase
        try:
            result = self.supabase.table(self.table_name).select("context").eq("phone_number", "CURRENT_WHATSAPP").execute()
            if result.data and len(result.data) > 0:
                context = result.data[0].get("context", {})
                self._current_number = context.get("value")
                logger.debug(f"Número atual recuperado do Supabase: {self._current_number}")
                return self._current_number
        except Exception as e:
            logger.warning(f"Erro ao recuperar número atual do Supabase: {e}")
            
        return None
        
    def get_context(self, phone_number: str) -> Dict[str, Any]:
        """Recupera o contexto para um número de telefone específico."""
        if not phone_number:
            logger.warning("Tentativa de obter contexto para número em branco")
            return {}
            
        # Não permita acesso às chaves especiais do sistema
        if phone_number == "CURRENT_WHATSAPP":
            logger.debug("Tentativa de acessar chave do sistema diretamente")
            return {}
            
        try:
            result = self.supabase.table(self.table_name).select("context").eq("phone_number", phone_number).execute()
            if result.data and len(result.data) > 0:
                context = result.data[0].get("context", {})
                logger.debug(f"Contexto recuperado para {phone_number} com {len(context)} chaves")
                return context
                
            logger.debug(f"Nenhum contexto encontrado para {phone_number}")
            return {}
        except Exception as e:
            logger.error(f"Erro ao recuperar contexto para {phone_number}: {e}")
            logger.debug(traceback.format_exc())
            return {}
        
    def save_context(self, phone_number: str, context: Dict[str, Any]) -> None:
        """Salva o contexto completo para um número de telefone."""
        if not phone_number:
            logger.warning("Tentativa de salvar contexto para número em branco")
            return
            
        # Não permita modificar chaves do sistema
        if phone_number == "CURRENT_WHATSAPP":
            logger.warning("Tentativa de modificar chave do sistema utilizando save_context")
            return
            
        try:
            logger.info(f"Salvando contexto para {phone_number} no Supabase...")
            
            result = self.supabase.table(self.table_name).upsert({
                "phone_number": phone_number,
                "context": context,
                "updated_at": datetime.now().isoformat()
            }).execute()
            
            if hasattr(result, 'data') and result.data:
                logger.debug(f"Contexto salvo com sucesso para {phone_number}")
            else:
                logger.warning(f"Resposta inesperada do Supabase ao salvar contexto para {phone_number}")
                
        except Exception as e:
            logger.error(f"Erro ao salvar contexto para {phone_number} no Supabase: {e}")
            logger.error(f"Detalhes do erro: {traceback.format_exc()}")
        
    def update_context(self, phone_number: str, updates: Dict[str, Any]) -> None:
        """Atualiza parcialmente o contexto para um número de telefone."""
        if not phone_number:
            logger.warning("Tentativa de atualizar contexto para número em branco")
            return
            
        # Não permita modificar chaves do sistema
        if phone_number == "CURRENT_WHATSAPP":
            logger.warning("Tentativa de modificar chave do sistema utilizando update_context")
            return
            
        try:
            # Obter contexto atual
            current = self.get_context(phone_number)
            
            # Mesclar com atualizações
            merged = {**current, **updates}
            
            # Se os dados incluem informações de agendamento, registrar
            if "booking_id" in updates or "attendee_id" in updates:
                logger.info(f"Atualizando dados de agendamento para {phone_number}: {updates}")
                updates["booking_updated_at"] = datetime.now().isoformat()
            
            # Salvar contexto atualizado
            self.save_context(phone_number, merged)
            
            logger.debug(f"Contexto atualizado para {phone_number} com chaves: {list(updates.keys())}")
        except Exception as e:
            logger.error(f"Erro ao atualizar contexto para {phone_number}: {e}")
            logger.error(f"Detalhes do erro: {traceback.format_exc()}")

# Instância global do gerenciador de contexto
context_manager = SupabaseContextManager()