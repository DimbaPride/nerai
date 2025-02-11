# app.py
import logging
import re
import json
import os
import time
import ssl
import asyncio
from quart import Quart, request, jsonify

from agents.agent_setup import agent_manager  # Modificado para usar agent_manager
from services.audio_processing import handle_audio_message
from utils.message_buffer import handle_message_with_buffer
from utils.smart_message_processor import send_message_in_chunks

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

@app.route('/form', methods=['POST'])
async def form_webhook():
    try:
        data = await request.get_json()
        logger.debug(f"Dados do formulário recebidos: {data}")

        # Gera ID único para o formulário
        form_id = f"{data.get('Email')}:{data.get('Telefone')}"
        logger.debug(f"Form ID gerado: {form_id}")

        if form_id in PROCESSED_LEADS:
            logger.info(f"Formulário já processado: {form_id}")
            return jsonify({
                "status": "success",
                "message": "Formulário já processado"
            }), 200

        # Formata os dados do formulário
        formatted_data = {
            'nome': data.get('Name', ''),
            'email': data.get('Email', ''),
            'telefone': data.get('Telefone', ''),
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

        # Formatação do número de telefone
        phone = re.sub(r'\D', '', formatted_data['telefone'])
        if not phone.startswith('55'):
            phone = f"55{phone}"

        if len(phone) < 12 or len(phone) > 13:
            logger.error(f"Número de telefone inválido: {phone}")
            return jsonify({
                "status": "error",
                "message": "Número de telefone inválido. Use o formato: DDD + Número"
            }), 400

        if phone in PROCESSED_LEADS:
            logger.info(f"Lead {phone} já processado. Ignorando envio.")
            return jsonify({
                "status": "success",
                "message": "Mensagem já enviada."
            }), 200

        # Mensagem de boas-vindas personalizada
        welcome_message = f"""Olá *{formatted_data['nome']}*! 🚀

Que ótimo ter você aqui! Vi que você tem interesse em nossas soluções de IA para a *{formatted_data['empresa']}*.

Somos especialistas em transformação digital e estou aqui para entender como podemos ajudar sua empresa no ramo de *{formatted_data['ramo']}*.

Me conte um pouco sobre os desafios que você gostaria de resolver com IA? 🤔"""

        # Envio da mensagem com retry
        success = await send_message_with_retry(welcome_message, phone)

        if success:
            PROCESSED_LEADS.add(phone)
            logger.info(f"Lead {phone} registrado com sucesso!")
            return jsonify({
                "status": "success",
                "message": "Mensagem enviada com sucesso"
            }), 200
        
        logger.error(f"Falha ao enviar mensagem para {phone}")
        return jsonify({
            "status": "error",
            "message": "Falha ao enviar mensagem"
        }), 500

    except Exception as e:
        logger.error(f"Erro no webhook do formulário: {str(e)}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

async def send_message_with_retry(message, phone, retries=3, delay=2):
    """Tenta enviar uma mensagem várias vezes em caso de falha."""
    for attempt in range(retries):
        try:
            success = await send_message_in_chunks(message, phone)
            if success:
                return True
            raise Exception("Falha ao enviar mensagem")
        except Exception as e:
            logger.error(f"Tentativa {attempt + 1} de envio falhou: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(delay)
            else:
                logger.error("Máximo de tentativas atingido")
                return False

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)