    from dotenv import load_dotenv
    import logging
    import json
    import os
    import re
    import asyncio
    import random
    import requests
    import time
    import tempfile
    import whisper
    from typing import List, Optional, Any
    from quart import Quart, request, jsonify
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from langchain.prompts import PromptTemplate
    from langchain.agents import Tool, AgentExecutor, create_openai_functions_agent
    from langchain_community.document_loaders import PlaywrightURLLoader
    from bs4 import BeautifulSoup
    from langchain.text_splitter import RecursiveCharacterTextSplitter
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document

    # Configuração inicial
    load_dotenv()
    app = Quart(__name__)
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    model = whisper.load_model("base")
    os.environ['KMP_DUPLICATE_LIB_OK']='TRUE'


    conversation_history = {}
    message_buffer = {}

    # Verificação de variáveis de ambiente
    REQUIRED_ENV = ["OPENAI_API_KEY", "EVOLUTION_API_KEY", "EVOLUTION_API_URL"]
    if missing := [key for key in REQUIRED_ENV if not os.getenv(key)]:
        raise EnvironmentError(f"Variáveis faltando: {', '.join(missing)}")

    # Configurações da instância
    INSTANCE_NAME = "nerai"
    OPENAI_MODEL = "gpt-4o-mini"
    MAX_RETRIES = 3
    RETRY_DELAY = 1

    class SafeFAISS(FAISS):
        @classmethod
        def load_local(cls, folder_path: str, embeddings: Any, allow_dangerous: bool = True) -> FAISS:
            return super().load_local(folder_path, embeddings, allow_dangerous=True)

    class SiteKnowledge:
        def __init__(self):
            self.vectorstore = None
            self.last_update = None
            self.update_interval = 86400    # 1 dia

        def needs_update(self) -> bool:
            if not self.last_update:
                return True
            return (time.time() - self.last_update) > self.update_interval

        async def initialize(self):
            """Inicializa ou cria a base de conhecimento."""
            try:
                logger.info("Inicializando base de conhecimento...")
                self.vectorstore = await self.load_knowledge_base()
                if not self.vectorstore or self.needs_update():
                    logger.info("Base desatualizada ou inexistente. Criando nova base...")
                    self.vectorstore = await self.create_knowledge_base()
                else:
                    logger.info("Base carregada com sucesso.")
            except Exception as e:
                logger.error(f"Erro na inicialização da base: {str(e)}")
                self.vectorstore = None

        async def create_knowledge_base(self) -> Optional[FAISS]:
            """Cria a base de conhecimento a partir do site."""
            try:
                logger.info("Iniciando criação da base de conhecimento...")
                urls = ["https://nerai.com.br"]
                loader = PlaywrightURLLoader(urls=urls, remove_selectors=["nav", "footer", "header"])
                logger.info("Carregando conteúdo do site...")
                documents = await loader.aload()
                if not documents:
                    logger.error("Nenhum documento foi carregado do site.")
                    return None

                logger.info(f"Processando {len(documents)} documentos...")
                content = []
                for doc in documents:
                    soup = BeautifulSoup(doc.page_content, 'html.parser')
                    for script in soup(["script", "style"]):
                        script.decompose()
                    content.append(Document(page_content=soup.get_text(strip=True)))

                logger.info("Dividindo texto em chunks...")
                text_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=500,
                    chunk_overlap=50,
                    length_function=len,
                    separators=["\n\n", "\n", ". ", " ", ""]
                )
                splits = text_splitter.split_documents(content)
                logger.info(f"{len(splits)} chunks criados.")

                logger.info("Criando embeddings e salvando base...")
                embeddings = OpenAIEmbeddings()
                vectorstore = FAISS.from_documents(splits, embeddings)
                os.makedirs("knowledge_base", exist_ok=True)
                vectorstore.save_local("knowledge_base")
                self.last_update = time.time()
                logger.info("Base de conhecimento criada com sucesso!")
                return vectorstore
            except Exception as e:
                logger.error(f"Erro ao criar base de conhecimento: {str(e)}", exc_info=True)
                return None

        async def load_knowledge_base(self) -> Optional[FAISS]:
            """Carrega a base de conhecimento existente."""
            try:
                if not os.path.exists("knowledge_base"):
                    return None
                embeddings = OpenAIEmbeddings()
                return SafeFAISS.load_local("knowledge_base", embeddings)
            except Exception as e:
                logger.error(f"Erro ao carregar base de conhecimento: {str(e)}")
                return None

        def query(self, question: str, k: int = 3) -> str:
            """Consulta a base de conhecimento."""
            if not self.vectorstore:
                return "Base de conhecimento não disponível."
            try:
                docs = self.vectorstore.similarity_search(question, k=k)
                return "\n\n".join([doc.page_content for doc in docs])
            except Exception as e:
                logger.error(f"Erro na consulta: {str(e)}")
                return "Erro ao consultar a base de conhecimento."

    class WhatsAppClient:
        def __init__(self, api_key: str, api_url: str, instance: str):
            self.api_key = api_key
            self.api_url = api_url.rstrip('/')
            self.instance = instance
            self.headers = {
                "apikey": api_key,
                "Content-Type": "application/json"
            }

        def send_message(self, text: str, number: str, delay: int = 0) -> bool:
            """Envia mensagem via WhatsApp com delay opcional."""
            try:
                url = f"{self.api_url}/message/sendText/{self.instance}"
                payload = {
                    "number": self._format_number(number),
                    "text": text,
                    "delay": delay
                }
                for attempt in range(MAX_RETRIES):
                    try:
                        response = requests.post(url, json=payload, headers=self.headers, timeout=30)
                        response.raise_for_status()
                        return True
                    except requests.exceptions.RequestException as e:
                        if attempt == MAX_RETRIES - 1:
                            logger.error(f"Erro ao enviar mensagem após {MAX_RETRIES} tentativas: {str(e)}")
                            return False
                        time.sleep(RETRY_DELAY * (attempt + 1))
                return False
            except Exception as e:
                logger.error(f"Erro ao enviar mensagem: {str(e)}")
                return False

        @staticmethod
        def _format_number(number: str) -> str:
            """Formata o número para o padrão correto."""
            number = re.sub(r"\D", "", number)
            if not number.startswith("55") and 10 <= len(number) <= 11:
                number = f"55{number}"
            return number

    # Instâncias globais
    whatsapp = WhatsAppClient(
        api_key=os.getenv("EVOLUTION_API_KEY"),
        api_url=os.getenv("EVOLUTION_API_URL"),
        instance=INSTANCE_NAME
    )
    site_knowledge = SiteKnowledge()

    def query_site_knowledge(query: str) -> str:
        """Consulta a base de conhecimento do site."""
        return site_knowledge.query(query)

    def split_message(text: str) -> List[str]:
        """Divide a mensagem de forma natural e contextual."""
        
        # Pontos naturais de quebra
        natural_breaks = [
            "\n\n",          # Quebra de parágrafo
            ". ",            # Final de frase
            "! ",            # Exclamação
            "? ",            # Interrogação
            "\n• ",         # Novo item de lista
            "\n- "          # Novo item com hífen
        ]
        
        # Se for uma mensagem curta, retorna direto
        if len(text) <= 160:
            return [text]
            
        messages = []
        current_text = text.strip()
        
        while current_text:
            break_index = -1
            
            # Procura pelo melhor ponto de quebra entre 100-160 caracteres
            for separator in natural_breaks:
                pos = current_text.find(separator, 100, 160)
                if pos != -1:
                    break_index = pos + len(separator)
                    break
            
            # Se não encontrou quebra ideal, procura em qualquer posição após 100 caracteres
            if break_index == -1:
                for separator in natural_breaks:
                    pos = current_text.find(separator, 100)
                    if pos != -1:
                        break_index = pos + len(separator)
                        break
            
            # Se ainda não encontrou, usa a primeira quebra disponível
            if break_index == -1:
                for separator in natural_breaks:
                    pos = current_text.find(separator)
                    if pos != -1:
                        break_index = pos + len(separator)
                        break
            
            # Se não encontrou nenhuma quebra natural, usa o tamanho máximo
            if break_index == -1:
                break_index = len(current_text)
            
            # Adiciona a parte atual se não estiver vazia
            part = current_text[:break_index].strip()
            if part:
                messages.append(part)
            
            # Atualiza o texto restante
            current_text = current_text[break_index:].strip()
        
        return [m for m in messages if m.strip()]


    def transcribe_audio(audio_file_path: str) -> str:
        """Transcreve áudio usando o Whisper"""
        result = model.transcribe(audio_file_path)
        return result['text']

    async def handle_audio_message(audio_data, number: str):
        try:
            logger.info("Iniciando processamento de áudio...")
            
            # Decodificar o base64
            import base64
            audio_base64 = audio_data.get("base64")
            if not audio_base64:
                logger.error("Base64 do áudio não encontrado")
                await send_message_in_chunks("Desculpe, não consegui processar o áudio. Tente novamente.", number)
                return

            try:
                audio_bytes = base64.b64decode(audio_base64)
            except Exception as e:
                logger.error(f"Erro ao decodificar base64: {e}")
                await send_message_in_chunks("Desculpe, ocorreu um erro ao processar seu áudio. Tente novamente.", number)
                return
            
            # Salvar como arquivo temporário
            with tempfile.NamedTemporaryFile(delete=False, suffix=".opus") as temp_audio_file:
                temp_audio_file.write(audio_bytes)
                temp_audio_file_path = temp_audio_file.name

            try:
                # Converter para wav
                wav_path = temp_audio_file_path + ".wav"
                convert_command = f'ffmpeg -i {temp_audio_file_path} -ar 16000 -ac 1 -hide_banner {wav_path}'
                conversion_result = os.system(convert_command)
                
                if conversion_result != 0:
                    logger.error(f"Erro na conversão do áudio: {conversion_result}")
                    raise Exception("Falha na conversão do áudio")

                # Transcrever o áudio
                logger.info("Transcrevendo áudio...")
                transcribed_text = transcribe_audio(wav_path)
                
                if not transcribed_text:
                    logger.error("Transcrição retornou vazia")
                    await send_message_in_chunks("Desculpe, não consegui entender o áudio. Pode tentar novamente?", number)
                    return

                logger.info(f"Áudio transcrito: {transcribed_text}")
                
                # Processar o texto transcrito
                await handle_message_with_buffer(transcribed_text, number)

            finally:
                # Limpar arquivos temporários
                try:
                    os.remove(temp_audio_file_path)
                    if os.path.exists(wav_path):
                        os.remove(wav_path)
                except Exception as e:
                    logger.error(f"Erro ao remover arquivos temporários: {e}")

        except Exception as e:
            logger.error(f"Erro no processamento de áudio: {str(e)}", exc_info=True)
            await send_message_in_chunks("Desculpe, ocorreu um erro ao processar seu áudio. Tente novamente.", number)


    async def send_message_in_chunks(text: str, number: str) -> bool:
        """Envia mensagem em chunks com simulação de digitação mais natural."""
        try:
            chunks = split_message(text)
            for i, chunk in enumerate(chunks):
                # Calcula delay com base no comprimento e tipo de mensagem
                delay = calculate_typing_delay(len(chunk))
                
                # Envia a mensagem com delay
                if not whatsapp.send_message(chunk, number, delay=delay):
                    logger.error(f"Falha ao enviar chunk para {number}")
                    return False
                
                # Pausa maior entre mensagens se não for a última
                if i < len(chunks) - 1:
                    # Pausa variável baseada no contexto
                    if '?' in chunk:  # Se for uma pergunta, pausa maior
                        await asyncio.sleep(2.5)
                    elif '!' in chunk:  # Se for exclamação, pausa média
                        await asyncio.sleep(2)
                    else:  # Pausa padrão
                        await asyncio.sleep(1.5)
            
            return True
        except Exception as e:
            logger.error(f"Erro ao enviar mensagens em chunks: {e}")
            return False

    def calculate_typing_delay(text_length: int) -> int:
        try:
            base_delay = (text_length / 40) * 1000
            variation = base_delay * 0.2
            delay = base_delay + random.uniform(-variation, variation)
            return int(max(2000, min(delay, 5000)))
        except Exception:
            return 2000  # Valor padrão em caso de erro

    # Configuração do LangChain
    llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0.3)

    # Ferramentas disponíveis
    tools = [
        Tool(
            name="site_knowledge",
            func=query_site_knowledge,
            description="Consulta informações específicas do site nerai.com.br. Use esta ferramenta para responder perguntas sobre a empresa e seus serviços."
        )
    ]

    SYSTEM_PROMPT = """Você é o assistente virtual oficial da Nerai. Sua função primária é fornecer *APENAS* informações verificadas da base de conhecimento.

    *REGRAS FUNDAMENTAIS:*

    1. *CONSULTA OBRIGATÓRIA*
    - SEMPRE consulte site_knowledge antes de qualquer resposta
    - Use *apenas* informações confirmadas pela base
    - NUNCA improvise ou suponha informações
    - Se não encontrar a informação, solicite mais detalhes

    2. *FORMATAÇÃO WHATSAPP*
    - Use *um asterisco* para negrito (ex: *palavra*)
    - Máximo 2 emojis por mensagem


    3. *ESTRUTURA DAS RESPOSTAS*
    Primeira mensagem:
    - Apresente o ponto principal
    - Use um emoji estratégico 🚀

    Mensagens subsequentes (se necessário):
    - Detalhe pontos específicos
    - Use bullets para listar informações
    - Mantenha a coesão entre mensagens

    4. *QUANDO NÃO HOUVER INFORMAÇÃO*
    Responda apenas:
    "Para garantir uma resposta precisa sobre [tema], preciso consultar informações específicas. Pode me detalhar melhor sua dúvida? 🤔"

    5. *TÓPICOS PARA CONSULTA OBRIGATÓRIA*
    - Serviços e soluções
    - Projetos e cases
    - Tecnologias utilizadas
    - Metodologias
    - Equipe e expertise
    - Diferenciais


    6. *PROIBIÇÕES*
    - Não use ** duplo para negrito
    - Não crie exemplos fictícios
    - Não mencione tecnologias não listadas
    - Não faça promessas não documentadas
    - Não sugira prazos ou valores

    7. *HIERARQUIA DE INFORMAÇÕES*
    1º Base de conhecimento (site_knowledge)
    2º Informações verificadas do site
    3º Solicitação de mais detalhes

    8. *TOM DE VOZ*
    - Profissional mas acolhedor
    - Direto mas não ríspido
    - Técnico mas compreensível
    - Confiante mas humilde

    9. *CHECKLIST ANTES DE ENVIAR*
    □ Informação verificada na base?
    □ Formatação correta do negrito?
    □ Emojis usados com moderação?
    □ Mensagem clara e objetiva?
    □ Todas as informações confirmadas?

    Lembre-se: Sua credibilidade depende da precisão das informações fornecidas."""

    # Template do Prompt
    prompt = PromptTemplate.from_template(
        template=(
            "{system_prompt}\n\n"
            "Histórico da Conversa:\n{history}\n\n"
            "Solicitação Atual: {input}\n\n"
            "Histórico de Ações:\n{agent_scratchpad}\n"
        )
    )

    # Cria o agente
    prompt = prompt.partial(system_prompt=SYSTEM_PROMPT)
    agent = create_openai_functions_agent(llm, tools, prompt)
    agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

    async def process_message(message: str, number: str) -> bool:
        """Processa a mensagem e envia a resposta."""
        try:
            logger.info(f"Processando mensagem: {message} de {number}")

            # Inicializa o histórico do usuário, se necessário
            if number not in conversation_history:
                conversation_history[number] = []

            # Adiciona a mensagem do usuário ao histórico
            conversation_history[number].append({"role": "user", "content": message})

            # Invoca o agente com o histórico completo
            result = agent_executor.invoke({
                "input": message,
                "history": conversation_history[number]
            })

            # Obtém a resposta do agente
            response = result.get("output", "Desculpe, não consegui processar sua mensagem.")

            # Adiciona a resposta do assistente ao histórico
            conversation_history[number].append({"role": "assistant", "content": response})

            logger.debug(f"Enviando resposta em chunks: {response}")
            return await send_message_in_chunks(response, number)
        except Exception as e:
            logger.error(f"Erro no processamento: {str(e)}", exc_info=True)
            await send_message_in_chunks("Desculpe, ocorreu um erro. Tente novamente.", number)
            return False

    async def handle_message_with_buffer(message: str, number: str):
        """Processa todas as mensagens enviadas dentro do intervalo de buffer."""
        try:
            buffer_time = 10  # Tempo de buffer em segundos
            
            # Inicializa ou atualiza o buffer do cliente
            if number not in message_buffer:
                message_buffer[number] = {
                    "messages": [],
                    "last_activity": time.time(),
                    "processing": False
                }
            
            # Adiciona a mensagem ao buffer
            message_buffer[number]["messages"].append(message)
            message_buffer[number]["last_activity"] = time.time()
            
            # Se já está processando, apenas retorna
            if message_buffer[number]["processing"]:
                return
            
            # Marca como processando para evitar processamento duplicado
            message_buffer[number]["processing"] = True
            
            try:
                # Aguarda o período de buffer
                while time.time() - message_buffer[number]["last_activity"] < buffer_time:
                    await asyncio.sleep(1)
                
                # Recupera todas as mensagens e limpa o buffer
                messages = message_buffer[number]["messages"]
                
                # Remove o buffer deste cliente
                message_buffer.pop(number)
                
                if not messages:
                    return
                    
                # Concatena todas as mensagens em um único contexto
                full_context = " ".join(messages)
                logger.info(f"Processando contexto completo de {number}: {full_context}")
                
                # Processa o contexto completo
                await process_message(full_context, number)
                
            finally:
                # Garante que o buffer seja limpo mesmo em caso de erro
                if number in message_buffer:
                    message_buffer[number]["processing"] = False
            
        except Exception as e:
            logger.error(f"Erro ao processar mensagens com buffer: {str(e)}")
            await send_message_in_chunks("Desculpe, ocorreu um erro. Tente novamente.", number)

    @app.route('/webhook', methods=['POST'])
    async def webhook():
        try:
            data = await request.get_json() 
            logger.debug(f"Webhook recebido: {data}")  # Log completo para debug
            
            if not data:
                return jsonify({"status": "ignored"}), 200
            
            event_type = data.get("event")
            message_data = data.get("data", {})

            # Processa lista se necessário
            if isinstance(message_data, list):
                message_data = message_data[0] if message_data else None
                if not message_data:
                    return jsonify({"status": "ignored"}), 200

            # Ignora mensagens do próprio bot
            if message_data.get("fromMe"):
                return jsonify({"status": "ignored"}), 200

            # Extrai o número
            remote_jid = (
                message_data.get("key", {}).get("remoteJid", "") or 
                message_data.get("remoteJid", "") or 
                message_data.get("jid", "")
            )
            number = remote_jid.split("@")[0].split(":")[0] if remote_jid else ""
            if not number:
                return jsonify({"status": "ignored"}), 200

            # Processa mensagens
            if event_type == "messages.upsert":
                msg_content = message_data.get("message", {})
                logger.debug(f"Conteúdo da mensagem: {msg_content}")  # Log para debug

                # Verifica áudio
                if "audioMessage" in msg_content:
                    logger.info("Mensagem de áudio detectada")
                    
                    # Obter base64 diretamente da mensagem
                    base64_data = message_data.get("message", {}).get("base64")
                    if base64_data:
                        logger.info("Base64 do áudio encontrado")
                        await handle_audio_message({"base64": base64_data}, number)
                        return jsonify({"status": "processed"}), 200
                    else:
                        logger.error("Base64 do áudio não encontrado")
                        await send_message_in_chunks("Desculpe, não consegui processar o áudio. Tente novamente.", number)
                        return jsonify({"status": "error", "message": "Base64 não encontrado"}), 200

                # Verifica texto
                message = (
                    msg_content.get("conversation") or 
                    msg_content.get("extendedTextMessage", {}).get("text")
                )
                
                if message:
                    logger.info(f"Nova mensagem de texto de {number}: {message}")
                    await handle_message_with_buffer(message, number)
                    return jsonify({"status": "processed"}), 200

            return jsonify({"status": "ignored"}), 200
        
        except Exception as e:
            logger.error(f"Erro no webhook: {str(e)}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500

    async def init_app():
        """Inicializa a aplicação."""
        try:
            await site_knowledge.initialize()
            logger.info("Base de conhecimento inicializada com sucesso!")
        except Exception as e:
            logger.error(f"Erro ao inicializar a aplicação: {str(e)}")

    @app.before_serving
    async def startup():
        """Executa antes do servidor iniciar."""
        await init_app()

    if __name__ == '__main__':
        app.run(host='0.0.0.0', port=5000, debug=True)