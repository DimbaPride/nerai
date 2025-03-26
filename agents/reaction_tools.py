"""
Ferramentas para reações a mensagens do WhatsApp.
"""

import logging
from typing import Dict, List, Any, Optional
import asyncio
from langchain.tools import BaseTool

from utils.smart_message_processor import send_reaction_to_message

logger = logging.getLogger(__name__)

# Mapeamento de descrições para emojis de reação
REACTION_MAP = {
    # Positivas
    "curtir": "👍",
    "like": "👍",
    "gostar": "👍", 
    "positivo": "👍",
    "sim": "👍",
    
    # Amor
    "coração": "❤️",
    "love": "❤️",
    "amar": "❤️",
    "adorar": "❤️",
    
    # Risada
    "rir": "😂",
    "risada": "😂",
    "engraçado": "😂",
    "haha": "😂",
    
    # Surpresa
    "surpresa": "😮",
    "surpreso": "😮",
    "uau": "😮",
    "surpreender": "😮",
    "espanto": "😮",
    
    # Tristeza
    "triste": "😢",
    "tristeza": "😢",
    "chorar": "😢",
    "sad": "😢",
    
    # Gratidão
    "agradecer": "🙏",
    "obrigado": "🙏",
    "gratidão": "🙏",
    "thanks": "🙏",
    
    # Celebração
    "comemorar": "🎉",
    "celebrar": "🎉",
    "festa": "🎉",
    "parabéns": "🎉",
    
    # Outros
    "palmas": "👏",
    "aplaudir": "👏",
    "fogo": "🔥",
    "excelente": "🔥",
    "ok": "👌",
    "perfeito": "👌"
}

# Lista de todos os emojis suportados para reações
SUPPORTED_EMOJIS = [
    "👍", "❤️", "😂", "😮", "😢", "🙏", "🎉", "👏", "🔥", "👌"
]

class ReactionTool(BaseTool):
    """Ferramenta para reagir a mensagens via WhatsApp."""
    
    name: str = "send_reaction"
    description: str = "Reage a uma mensagem do cliente com um emoji"
    return_direct: bool = True
    
    def __init__(self):
        """Inicializa a ferramenta de reação."""
        super().__init__()
        self._whatsapp_number = None
        self._last_message_id = None
        
    def set_whatsapp_number(self, number: str) -> None:
        """Define o número do WhatsApp para envio."""
        if not number:
            logger.error("Tentativa de configurar número de WhatsApp vazio na ferramenta de reação")
            return
            
        old_number = self._whatsapp_number
        self._whatsapp_number = number
        logger.info(f"Número de WhatsApp configurado na ferramenta de reação: '{number}' (anterior: '{old_number}')")
    
    def set_last_message_id(self, message_id: str) -> None:
        """Define o ID da última mensagem recebida."""
        self._last_message_id = message_id
    
    def _run(self, reaction_type: str = None, emoji: str = None, message_id: str = None) -> str:
        """
        Não usar diretamente, usar o método async.
        
        Args:
            reaction_type: Tipo de reação em linguagem natural
            emoji: Emoji direto para reação
            message_id: ID da mensagem (opcional, usa a última por padrão)
            
        Returns:
            Mensagem de sucesso ou erro
        """
        raise NotImplementedError("Use o método _arun")
    
    def _map_reaction_type(self, reaction_type: str) -> str:
        """
        Mapeia uma descrição para o emoji correspondente.
        
        Args:
            reaction_type: Descrição da reação
            
        Returns:
            Emoji correspondente ou None
        """
        if not reaction_type:
            return None
            
        # Normalizar o texto
        normalized = reaction_type.lower().strip()
        
        # Adicionar mapeamentos para variações da palavra "heart" e "coração"
        if "heart" in normalized or "coração" in normalized or "coracao" in normalized:
            return "❤️"
            
        # Verificar correspondência exata
        if normalized in REACTION_MAP:
            return REACTION_MAP[normalized]
            
        # Verificar correspondência parcial
        for term, emoji in REACTION_MAP.items():
            if term in normalized or normalized in term:
                return emoji
                
        # Casos especiais
        if "gost" in normalized or "curti" in normalized:
            return "👍"
        if "ama" in normalized or "coraç" in normalized:
            return "❤️"
        
        # Padrão
        return "👍"
    
    async def _arun(self, reaction_type: str = None, emoji: str = None, message_id: str = None, follow_up: str = None) -> str:
        """
        Reage a uma mensagem do cliente.
        
        Args:
            reaction_type: Tipo de reação em linguagem natural (ex: "curtir", "amar")
            emoji: Emoji direto para reação (ex: "👍", "❤️")
            message_id: ID da mensagem (opcional, usa a última por padrão)
            follow_up: Mensagem de texto para enviar após a reação (opcional)
            
        Returns:
            String com a mensagem de follow-up se fornecida, ou espaço em branco em caso de sucesso
        """
        try:
            # Verificar número do WhatsApp
            if not self._whatsapp_number:
                logger.error("Número do WhatsApp não configurado na ferramenta de reação")
                return "Erro: Número do WhatsApp não configurado. Por favor, aguarde o cliente enviar uma mensagem primeiro."
                
            # Determinar qual mensagem reagir
            msg_id = message_id or self._last_message_id
            
            # Verificar se temos um ID de mensagem válido
            if not msg_id:
                logger.error("ID da mensagem não fornecido e não há último ID armazenado")
                return "Não posso reagir agora. Aguarde o cliente enviar uma mensagem primeiro."
                
            # Validar formato do ID da mensagem - deve ser um ID válido, não um timestamp
            if ":" in str(msg_id) or "/" in str(msg_id):
                logger.error(f"Formato de ID de mensagem inválido: '{msg_id}'")
                if not self._last_message_id or ":" in str(self._last_message_id) or "/" in str(self._last_message_id):
                    return "Não posso reagir a esta mensagem. Aguarde o cliente enviar uma nova mensagem."
                logger.info(f"Usando último ID válido armazenado: {self._last_message_id}")
                msg_id = self._last_message_id
                
            # Determinar qual emoji usar
            reaction_emoji = None
            
            # Se forneceu emoji diretamente
            if emoji and emoji in SUPPORTED_EMOJIS:
                reaction_emoji = emoji
            
            # Se forneceu tipo de reação para mapear
            elif reaction_type:
                reaction_emoji = self._map_reaction_type(reaction_type)
            
            # Se não encontrou nenhum emoji válido
            if not reaction_emoji:
                logger.warning("Emoji não reconhecido, usando '👍' como padrão")
                reaction_emoji = "👍"
                
            # Logs detalhados antes de enviar
            logger.info(f"Reagindo à mensagem {msg_id} com '{reaction_emoji}'")
            logger.info(f"Usando número de WhatsApp: {self._whatsapp_number}")
            
            # Enviar a reação
            success = await send_reaction_to_message(msg_id, reaction_emoji, self._whatsapp_number)
            
            # Se tiver uma mensagem de follow-up, retorna ela
            if follow_up:
                logger.info(f"Retornando mensagem de follow-up após reação: '{follow_up[:30]}...'")
                return follow_up.strip()
                
            if success:
                # Retornar espaço em branco em vez de string vazia para evitar erro de resposta inválida
                return " "
            else:
                return "Erro ao reagir à mensagem. Tente novamente."
                
        except Exception as e:
            logger.error(f"Erro ao enviar reação: {e}")
            return f"Erro ao reagir à mensagem: {str(e)}"

# Instância da ferramenta para exportação
reaction_tool = ReactionTool() 