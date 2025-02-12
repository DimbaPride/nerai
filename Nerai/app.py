# app.py
import logging
import re
import json
import os
import time
import ssl
import asyncio
from typing import Dict, List, Optional, Any
from quart import Quart, request, jsonify

from agents.agent_setup import agent_manager  # Modificado para usar agent_manager
from services.audio_processing import handle_audio_message
from utils.message_buffer import handle_message_with_buffer, update_presence  # Adicionada importação
from utils.smart_message_processor import send_message_in_chunks
from utils.conversation_manager import conversation_manager


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Quart(__name__)

# Conjuntos globais para armazenar IDs de mensagens e leads processados
processed_message_ids = set()
PROCESSED_LEADS = set()

@app.before_serving
async def startup():
    """Inicializa o agente e a base de conhecimento antes de servir requisições."""
    try:
        await agent_manager.initialize()
        logger.info("Agente e base de conhecimento inicializados com sucesso!")
    except Exception as e:
        logger.error(f"Erro na inicialização: {str(e)}", exc_info=True)
        raise

@app.route('/webhook', methods=['POST'])
async def webhook():
    try:
        data = await request.get_json()
        logger.debug(f"Webhook recebido: {data}")

        if not data:
            return jsonify({"status": "ignored"}), 200

        event_type = data.get("event")
        message_data = data.get("data", {})

        # Processa eventos de presença primeiro
        if event_type == "presence.update":
            try:
                presence_data = message_data.get("presences", {})
                for number, status in presence_data.items():
                    number = number.split("@")[0]
                    logger.debug(f"Atualizando presença para {number}: {status}")
                    update_presence(number, status)
                return jsonify({"status": "success"}), 200
            except Exception as e:
                logger.error(f"Erro ao processar presence.update: {e}")
                return jsonify({"status": "error", "message": str(e)}), 500

        if isinstance(message_data, list) and message_data:
            message_data = message_data[0]

        # Verifica se a mensagem foi enviada pelo próprio agente
        agent_number = conversation_manager.normalize_phone('5511911043825')
        if message_data.get('sender') == f"{agent_number}@s.whatsapp.net":
            logger.info("Mensagem enviada pelo agente, ignorando...")
            return jsonify({"status": "success", "message": "Mensagem do agente ignorada"}), 200

        # Verifica processamento duplicado
        message_id = message_data.get("key", {}).get("id")
        if message_id and message_id in processed_message_ids:
            logger.info(f"Mensagem {message_id} já processada, ignorando.")
            return jsonify({"status": "ignored"}), 200

        # Extrai e normaliza o número do remetente
        remote_jid = (
            message_data.get("key", {}).get("remoteJid", "")
            or message_data.get("remoteJid", "")
            or message_data.get("jid", "")
        )
        raw_number = remote_jid.split("@")[0].split(":")[0] if remote_jid else ""
        if not raw_number:
            return jsonify({"status": "ignored"}), 200
            
        # Normaliza o número usando o conversation_manager
        number = conversation_manager.normalize_phone(raw_number)

        if event_type == "messages.upsert":
            msg_content = message_data.get("message", {})

            # Processa mensagem de áudio
            if "audioMessage" in msg_content:
                base64_data = msg_content.get("base64") or message_data.get("base64")
                if base64_data:
                    await handle_audio_message({"base64": base64_data}, number)
                    processed_message_ids.add(message_id)
                    return jsonify({"status": "processed"}), 200
                return jsonify({"status": "error", "message": "Base64 não encontrado"}), 200

            # Processa mensagem de texto
            message_text = (
                msg_content.get("conversation")
                or msg_content.get("extendedTextMessage", {}).get("text")
            )
            if message_text:
                await handle_message_with_buffer(message_text, number)
                processed_message_ids.add(message_id)
                return jsonify({"status": "processed"}), 200

        return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logger.error(f"Erro no webhook: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

def get_first_name(full_name: str) -> str:
    """Extrai o primeiro nome de um nome completo."""
    return full_name.split()[0] if full_name else ''

async def send_delayed_message(message: str, phone: str, delay: int) -> bool:
    """
    Envia uma mensagem com delay.
    
    Args:
        message: Mensagem a ser enviada
        phone: Número do telefone
        delay: Delay em segundos
        
    Returns:
        bool: True se a mensagem foi enviada com sucesso
    """
    try:
        await asyncio.sleep(delay)
        success = await send_message_with_retry(message, phone, retries=3, delay=1)
        if not success:
            logger.error(f"Falha ao enviar mensagem após delay de {delay}s: {message}")
        return success
    except Exception as e:
        logger.error(f"Erro ao enviar mensagem com delay: {e}")
        return False

async def send_welcome_messages(formatted_data: dict, phone: str) -> bool:
    """
    Envia mensagens de boas-vindas de forma robusta.
    
    Args:
        formatted_data: Dados do formulário
        phone: Número do telefone
        
    Returns:
        bool: True se todas as mensagens foram enviadas com sucesso
    """
    try:
        first_name = formatted_data['nome'].split()[0]
        
        # Create message parts
        messages = [
            f"Olá *{first_name}*!",
            "Que ótimo ter você aqui!",
            f"Vi que você tem interesse em nossas soluções de IA para a *{formatted_data['empresa']}*.",
            f"Somos especialistas em transformação digital e estou aqui para entender como podemos ajudar sua empresa no ramo de *{formatted_data['ramo']}*.",
            "Me conte um pouco sobre os desafios que você gostaria de resolver com IA?"
        ]

        # Adiciona todas as mensagens ao histórico primeiro
        full_message = "\n".join(messages)
        conversation_manager.add_message(phone, full_message, role='assistant')

        tasks = []
        for index, message in enumerate(messages):
            # Usar asyncio.sleep para criar um delay crescente entre mensagens
            delay = index * 2  # 0s, 2s, 4s, 6s, 8s entre mensagens
            tasks.append(
                asyncio.create_task(
                    send_delayed_message(message, phone, delay)
                )
            )

        # Espera todas as mensagens serem enviadas
        results = await asyncio.gather(*tasks)
        
        # Verifica se todas as mensagens foram enviadas com sucesso
        success = all(results)
        
        if not success:
            logger.error("Falha ao enviar uma ou mais mensagens de boas-vindas")
            
        return success

    except Exception as e:
        logger.error(f"Erro ao enviar mensagens de boas-vindas: {e}")
        return False

async def send_message_with_retry(message: str, phone: str, metadata: Optional[Dict] = None, retries: int = 3, delay: float = 1.0) -> bool:
    """
    Tenta enviar uma mensagem várias vezes em caso de falha.
    
    Args:
        message: Mensagem a ser enviada
        phone: Número do telefone
        metadata: Metadados adicionais (opcional)
        retries: Número de tentativas
        delay: Delay entre tentativas em segundos
        
    Returns:
        bool: True se a mensagem foi enviada com sucesso
    """
    for attempt in range(retries):
        try:
            success = await send_message_in_chunks(message, phone)
            if success:
                return True
                
            if attempt < retries - 1:
                await asyncio.sleep(delay)
                continue
                
            logger.error(f"Todas as {retries} tentativas de envio falharam")
            return False
            
        except Exception as e:
            logger.error(f"Erro na tentativa {attempt + 1} de envio: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(delay)
            else:
                return False
    
    return False

@app.route('/form', methods=['POST'])
async def form_webhook():
    try:
        data = await request.get_json()
        logger.debug(f"Dados do formulário recebidos: {data}")

        # Normaliza o número do telefone usando a função específica
        raw_phone = data.get('Telefone', '')
        phone = conversation_manager.normalize_phone(raw_phone)  # Usa o mesmo normalize_phone

        # Verifica se já existe uma conversa ativa para este número
        history = conversation_manager.get_history(phone)
        if history:
            logger.info(f"Conversa já existe para {phone}, ignorando formulário")
            return jsonify({
                "status": "success",
                "message": "Conversa já existente"
            }), 200

        # Verifica origem do webhook
        if data.get("webhook_source") == "whatsapp":
            logger.info("Webhook originado do WhatsApp, ignorando para evitar loop")
            return jsonify({"status": "ignored", "message": "WhatsApp webhook ignorado"}), 200

        # Gera ID único para o formulário usando o número normalizado
        form_id = f"{data.get('Email')}:{phone}"
        logger.debug(f"Form ID gerado: {form_id}")

        if form_id in PROCESSED_LEADS:
            logger.info(f"Formulário já processado: {form_id}")
            return jsonify({
                "status": "success",
                "message": "Formulário já processado"
            }), 200

        # Formata os dados do formulário com o número normalizado
        formatted_data = {
            'nome': data.get('Name', ''),
            'email': data.get('Email', ''),
            'telefone': phone,  # Usa o número já normalizado
            'empresa': data.get('empresa', ''),
            'ramo': data.get('ramo', '')
        }

        # Validação de campos obrigatórios
        if not all(formatted_data.values()):
            missing_fields = [k for k, v in formatted_data.items() if not v]
            logger.error(f"Campos obrigatórios faltando: {missing_fields}")
            return jsonify({
                "status": "error",
                "message": f"Campos obrigatórios faltando: {', '.join(missing_fields)}"
            }), 400

        # Marca o lead como processado e adiciona contexto
        PROCESSED_LEADS.add(form_id)
        conversation_manager.add_lead_context(phone, formatted_data)

        # Inicia o envio das mensagens em background
        asyncio.create_task(send_welcome_messages(formatted_data, phone))
        
        # Retorna sucesso imediatamente
        logger.info(f"Iniciado envio de mensagens para {phone}")
        return jsonify({
            "status": "success",
            "message": "Iniciado envio de mensagens"
        }), 200

    except Exception as e:
        logger.error(f"Erro no webhook do formulário: {str(e)}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)