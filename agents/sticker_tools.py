"""
Ferramentas para envio de figurinhas pelo WhatsApp.
"""

import logging
from typing import Dict, List, Any, Optional
import asyncio
from langchain.tools import BaseTool

from utils.smart_message_processor import send_sticker_to_user

logger = logging.getLogger(__name__)

# URLs de figurinhas pré-definidas para facilitar o uso
STICKER_COLLECTION = {
    # Figurinhas de café "Cuppy" - https://github.com/WhatsApp/stickers/tree/main/Android/app/src/main/assets/1
    "smile": "https://raw.githubusercontent.com/WhatsApp/stickers/main/Android/app/src/main/assets/1/01_Cuppy_smile.webp",
    "lol": "https://raw.githubusercontent.com/WhatsApp/stickers/main/Android/app/src/main/assets/1/02_Cuppy_lol.webp",
    "rofl": "https://raw.githubusercontent.com/WhatsApp/stickers/main/Android/app/src/main/assets/1/03_Cuppy_rofl.webp",
    "sad": "https://raw.githubusercontent.com/WhatsApp/stickers/main/Android/app/src/main/assets/1/04_Cuppy_sad.webp",
    "cry": "https://raw.githubusercontent.com/WhatsApp/stickers/main/Android/app/src/main/assets/1/05_Cuppy_cry.webp",
    "love": "https://raw.githubusercontent.com/WhatsApp/stickers/main/Android/app/src/main/assets/1/06_Cuppy_love.webp",
    "angry": "https://raw.githubusercontent.com/WhatsApp/stickers/main/Android/app/src/main/assets/1/07_Cuppy_angry.webp",
    "party": "https://raw.githubusercontent.com/WhatsApp/stickers/main/Android/app/src/main/assets/1/10_Cuppy_party.webp",
    "hot": "https://raw.githubusercontent.com/WhatsApp/stickers/main/Android/app/src/main/assets/1/11_Cuppy_hot.webp",
    "cool": "https://raw.githubusercontent.com/WhatsApp/stickers/main/Android/app/src/main/assets/1/14_Cuppy_cool.webp",
    "curious": "https://raw.githubusercontent.com/WhatsApp/stickers/main/Android/app/src/main/assets/1/15_Cuppy_curious.webp",
    "hug": "https://raw.githubusercontent.com/WhatsApp/stickers/main/Android/app/src/main/assets/1/16_Cuppy_hug.webp",
    "think": "https://raw.githubusercontent.com/WhatsApp/stickers/main/Android/app/src/main/assets/1/17_Cuppy_think.webp",
    "sleep": "https://raw.githubusercontent.com/WhatsApp/stickers/main/Android/app/src/main/assets/1/18_Cuppy_sleep.webp",
    "excited": "https://raw.githubusercontent.com/WhatsApp/stickers/main/Android/app/src/main/assets/1/24_Cuppy_excited.webp"
}

class StickerTool(BaseTool):
    """Ferramenta para envio de figurinhas via WhatsApp."""
    
    name: str = "send_sticker"
    description: str = "Envia uma figurinha ao cliente via WhatsApp"
    return_direct: bool = True
    
    def __init__(self):
        """Inicializa a ferramenta de sticker."""
        super().__init__()
        self._whatsapp_number = None
        
    def set_whatsapp_number(self, number: str) -> None:
        """Define o número do WhatsApp para envio."""
        self._whatsapp_number = number
    
    def _run(self, sticker_name: str = None, sticker_url: str = None) -> str:
        """
        Não usar diretamente, usar o método async.
        
        Args:
            sticker_name: Nome da figurinha pré-definida
            sticker_url: URL personalizada de uma figurinha
            
        Returns:
            Mensagem de sucesso ou erro
        """
        raise NotImplementedError("Use o método _arun")
    
    async def _arun(self, sticker_name: str = None, sticker_url: str = None, follow_up: str = None) -> str:
        """
        Envia uma figurinha via WhatsApp.
        
        Args:
            sticker_name: Nome ou descrição da figurinha (ex: "feliz", "triste")
            sticker_url: URL direta para a figurinha (opcional)
            follow_up: Mensagem de texto para enviar após a figurinha (opcional)
            
        Returns:
            String com a mensagem de follow-up se fornecida, ou espaço em branco em caso de sucesso
        """
        try:
            if not self._whatsapp_number:
                logger.error("Número do WhatsApp não configurado")
                return "Erro: Número do WhatsApp não configurado"
            
            # Escolher a URL da figurinha
            url = None
            
            # Se fornecido URL diretamente, usar ela
            if sticker_url:
                url = sticker_url
            
            # Se fornecido nome/descrição, tentar encontrar no banco de figurinhas
            elif sticker_name:
                url = self._find_sticker_url(sticker_name)
            
            # Se não conseguiu determinar a URL, retornar erro
            if not url:
                logger.warning(f"Figurinha não encontrada: {sticker_name}")
                return f"Não encontrei uma figurinha para '{sticker_name}'. Tente outra descrição."
            
            logger.info(f"Enviando figurinha: {url}")
            
            # Enviar a figurinha
            success = await send_sticker_to_user(url, self._whatsapp_number)
            
            # Se tiver uma mensagem de follow-up, retorna ela
            if follow_up:
                logger.info(f"Retornando mensagem de follow-up após figurinha: '{follow_up[:30]}...'")
                return follow_up.strip()
            
            if success:
                # Retornar espaço em branco em vez de string vazia para evitar erro de resposta inválida
                return " "
            else:
                return "Erro ao enviar figurinha. Tente novamente."
                
        except Exception as e:
            logger.error(f"Erro ao enviar figurinha: {e}")
            return f"Erro ao enviar figurinha: {str(e)}"

    def _find_sticker_url(self, sticker_name: str) -> str:
        """
        Encontra a URL da figurinha com base no nome ou descrição fornecida.
        
        Args:
            sticker_name: Nome ou descrição da figurinha
            
        Returns:
            URL da figurinha ou None se não encontrada
        """
        if not sticker_name:
            return None
            
        sticker_name = sticker_name.lower()
        
        # 1. Verificar correspondência exata
        if sticker_name in STICKER_COLLECTION:
            logger.info(f"Correspondência exata encontrada para: {sticker_name}")
            return STICKER_COLLECTION[sticker_name]
        
        # 2. Interpretação inteligente - termos em português e contexto
        if sticker_name in ['feliz', 'sorriso', 'alegre', 'contente', 'sorridente']:
            return STICKER_COLLECTION['smile']
        elif sticker_name in ['triste', 'chateado', 'tristeza']:
            return STICKER_COLLECTION['sad']  
        elif sticker_name in ['rindo', 'risada', 'haha', 'engraçado']:
            return STICKER_COLLECTION['lol']
        elif sticker_name in ['chorando', 'choro', 'lágrimas']:
            return STICKER_COLLECTION['cry']
        elif sticker_name in ['amor', 'coração', 'apaixonado', 'love']:
            return STICKER_COLLECTION['love']
        elif sticker_name in ['bravo', 'raiva', 'irritado', 'zangado']:
            return STICKER_COLLECTION['angry']
        elif sticker_name in ['festa', 'celebração', 'comemorando']:
            return STICKER_COLLECTION['party']
        elif sticker_name in ['legal', 'tranquilo', 'descolado']:
            return STICKER_COLLECTION['cool']
            
        # 3. Correspondência parcial
        for term, emoji_name in [
            (['feliz', 'sorriso', 'alegre'], 'smile'),
            (['triste', 'chateado'], 'sad'),
            (['rindo', 'risada', 'haha'], 'lol'),
            (['amor', 'coração', 'amo'], 'love'),
            (['obrigado', 'gratidão', 'agradecer'], 'thanks'),
            (['legal', 'top', 'massa'], 'cool')
        ]:
            if any(word in sticker_name for word in term):
                logger.info(f"Correspondência parcial encontrada: {sticker_name} -> {emoji_name}")
                return STICKER_COLLECTION[emoji_name]
        
        # 4. Fallback para smile como padrão
        logger.info(f"Nenhuma correspondência encontrada para '{sticker_name}', usando smile como padrão")
        return STICKER_COLLECTION["smile"]

# Instância da ferramenta para exportação
sticker_tool = StickerTool() 