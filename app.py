# app.py
import logging  # Para registro de logs
import re       # Para expressões regulares
import json     # Para manipulação de JSON
import os       # Para operações do sistema
import time     # Para operações com tempo
import ssl      # Para configurações SSL
import asyncio  # Para programação assíncrona
from quart import Quart, request, jsonify  # Framework web assíncrono

from agents.agent_setup import agent_manager  # Gerenciador do agente de IA
from services.audio_processing import handle_audio_message  # Processamento de áudio
from utils.message_buffer import handle_message_with_buffer  # Buffer de mensagens
from utils.smart_message_processor import send_message_in_chunks  # Envio de mensagens

logging.basicConfig(level=logging.DEBUG)  # Configura logging em modo DEBUG
logger = logging.getLogger(__name__)      # Cria logger para este arquivo

app = Quart(__name__)  # Cria aplicação Quart

@app.before_serving
async def startup():
    """Executa antes do servidor começar"""
    # Inicializa o agente e base de conhecimento
    try:
        await agent_manager.initialize()
        logger.info("Agente e base de conhecimento inicializados com sucesso!")
    except Exception as e:
        logger.error(f"Erro na inicialização: {str(e)}", exc_info=True)
        raise

@app.route('/webhook', methods=['POST'])
async def webhook():
    """
    Processa webhooks do WhatsApp:
    - Verifica se é mensagem do próprio agente
    - Evita processamento duplicado
    - Extrai número do remetente
    - Processa mensagens de áudio e texto
    """
    try:
        data = await request.get_json()
        logger.debug(f"Webhook recebido: {data}")

        if not data:
            return jsonify({"status": "ignored"}), 200

        event_type = data.get("event")
        message_data = data.get("data", {})

        if isinstance(message_data, list) and message_data:
            message_data = message_data[0]

        # Verifica se a mensagem foi enviada pelo próprio agente
        agent_number = '5511911043825'
        if message_data.get('sender') == f"{agent_number}@s.whatsapp.net":
            logger.info("Mensagem enviada pelo agente, ignorando...")
            return jsonify({"status": "success", "message": "Mensagem do agente ignorada"}), 200

        # Verifica processamento duplicado
        message_id = message_data.get("key", {}).get("id")
        if message_id and message_id in processed_message_ids:
            logger.info(f"Mensagem {message_id} já processada, ignorando.")
            return jsonify({"status": "ignored"}), 200

        # Extrai o número do remetente
        remote_jid = (
            message_data.get("key", {}).get("remoteJid", "")
            or message_data.get("remoteJid", "")
            or message_data.get("jid", "")
        )
        number = remote_jid.split("@")[0].split(":")[0] if remote_jid else ""
        if not number:
            return jsonify({"status": "ignored"}), 200

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

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)