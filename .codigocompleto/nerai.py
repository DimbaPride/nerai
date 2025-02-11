# =============================================================================
# Importações
# =============================================================================

# Bibliotecas padrão Python
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

# Framework Web
from quart import Quart, request, jsonify

# Componentes LangChain
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_groq import ChatGroq
from langchain.prompts import PromptTemplate
from langchain.agents import Tool, AgentExecutor, create_openai_functions_agent
from langchain_community.document_loaders import PlaywrightURLLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

# Processamento HTML
from bs4 import BeautifulSoup

# =============================================================================
# Configuração Inicial do Aplicativo
# =============================================================================

# Carrega variáveis de ambiente e configura app
load_dotenv()
app = Quart(__name__)

# Configuração de logs
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Inicialização do modelo Whisper
model = whisper.load_model("base")
os.environ['KMP_DUPLICATE_LIB_OK']='TRUE'

# Estruturas de dados para gerenciamento de conversas
conversation_history = {}  # Armazena histórico de conversas por usuário
message_buffer = {}       # Buffer temporário para mensagens

# =============================================================================
# Verificação de Ambiente e Configurações
# =============================================================================

# Verifica variáveis de ambiente necessárias
REQUIRED_ENV = ["OPENAI_API_KEY", "EVOLUTION_API_KEY", "EVOLUTION_API_URL", "GROQ_API_KEY"]
if missing := [key for key in REQUIRED_ENV if not os.getenv(key)]:
    raise EnvironmentError(f"Variáveis faltando: {', '.join(missing)}")

# Configurações da instância
INSTANCE_NAME = "nerai"        # Nome da instância do WhatsApp
OPENAI_MODEL = "gpt-4o-mini"   # Modelo GPT a ser usado
GROQ_MODEL = "llama-3.3-70b-versatile"     # Modelo GROQ a ser usado
MAX_RETRIES = 3               # Número máximo de tentativas para envio
RETRY_DELAY = 1               # Delay entre tentativas em segundos

# =============================================================================
# Classes de Gerenciamento da Base de Conhecimento
# =============================================================================

class SafeFAISS(FAISS):
    """
    Extensão segura da classe FAISS para carregamento de embeddings.
    Permite carregamento local com configurações de segurança personalizadas.
    """
    @classmethod
    def load_local(cls, folder_path: str, embeddings: Any, allow_dangerous: bool = True) -> FAISS:
        return super().load_local(folder_path, embeddings, allow_dangerous=True)


class SiteKnowledge:
    """
    Gerenciador da base de conhecimento do site.
    Responsável por criar, atualizar e consultar informações do site da Nerai.
    """
    def __init__(self):
        """Inicializa o gerenciador de conhecimento."""
        self.vectorstore = None             # Armazena a base vetorial
        self.last_update = None            # Timestamp da última atualização
        self.update_interval = 86400       # Intervalo de atualização (1 dia)

    def needs_update(self) -> bool:
        """
        Verifica se a base precisa ser atualizada.
        Returns:
            bool: True se precisar atualizar, False caso contrário
        """
        if not self.last_update:
            return True
        return (time.time() - self.last_update) > self.update_interval

    async def initialize(self):
        """
        Inicializa ou cria a base de conhecimento.
        Tenta carregar uma base existente primeiro, se não existir ou estiver
        desatualizada, cria uma nova.
        """
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
        """
        Cria uma nova base de conhecimento extraindo dados do site.
        
        Processo:
        1. Carrega conteúdo das URLs definidas
        2. Processa e limpa o HTML
        3. Divide o texto em chunks
        4. Cria embeddings e salva a base
        
        Returns:
            Optional[FAISS]: Base de conhecimento ou None se houver erro
        """
        try:
            logger.info("Iniciando criação da base de conhecimento...")
            # Configuração do loader
            urls = ["https://nerai.com.br"]
            loader = PlaywrightURLLoader(
                urls=urls, 
                remove_selectors=["nav", "footer", "header"]
            )
            
            # Carregamento de documentos
            logger.info("Carregando conteúdo do site...")
            documents = await loader.aload()
            if not documents:
                logger.error("Nenhum documento foi carregado do site.")
                return None

            # Processamento de documentos
            logger.info(f"Processando {len(documents)} documentos...")
            content = []
            for doc in documents:
                soup = BeautifulSoup(doc.page_content, 'html.parser')
                for script in soup(["script", "style"]):
                    script.decompose()
                content.append(Document(page_content=soup.get_text(strip=True)))

            # Divisão em chunks
            logger.info("Dividindo texto em chunks...")
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=500,
                chunk_overlap=50,
                length_function=len,
                separators=["\n\n", "\n", ". ", " ", ""]
            )
            splits = text_splitter.split_documents(content)
            logger.info(f"{len(splits)} chunks criados.")

            # Criação dos embeddings
            logger.info("Criando embeddings e salvando base...")
            embeddings = OpenAIEmbeddings()
            vectorstore = FAISS.from_documents(splits, embeddings)
            
            # Salvamento da base
            os.makedirs("knowledge_base", exist_ok=True)
            vectorstore.save_local("knowledge_base")
            self.last_update = time.time()
            logger.info("Base de conhecimento criada com sucesso!")
            
            return vectorstore
            
        except Exception as e:
            logger.error(f"Erro ao criar base de conhecimento: {str(e)}", exc_info=True)
            return None

    async def load_knowledge_base(self) -> Optional[FAISS]:
        """
        Carrega uma base de conhecimento existente do disco.
        
        Returns:
            Optional[FAISS]: Base carregada ou None se não existir/erro
        """
        try:
            if not os.path.exists("knowledge_base"):
                return None
            embeddings = OpenAIEmbeddings()
            return SafeFAISS.load_local("knowledge_base", embeddings)
        except Exception as e:
            logger.error(f"Erro ao carregar base de conhecimento: {str(e)}")
            return None

    def query(self, question: str, k: int = 3) -> str:
        """
        Consulta a base de conhecimento.
        
        Args:
            question (str): Pergunta a ser consultada
            k (int): Número de resultados similares a retornar
            
        Returns:
            str: Resposta concatenada dos documentos mais relevantes
        """
        if not self.vectorstore:
            return "Base de conhecimento não disponível."
        try:
            docs = self.vectorstore.similarity_search(question, k=k)
            return "\n\n".join([doc.page_content for doc in docs])
        except Exception as e:
            logger.error(f"Erro na consulta: {str(e)}")
            return "Erro ao consultar a base de conhecimento."

 # Cria instância do SiteKnowledge
site_knowledge = SiteKnowledge()    

# Configuração do modelo de linguagem
llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0.3)
llm2 = ChatGroq(model=GROQ_MODEL, temperature=0.3)

# Função de consulta
def query_site_knowledge(query: str) -> str:
    """Consulta a base de conhecimento do site."""
    return site_knowledge.query(query)

# =============================================================================
# Cliente WhatsApp e Processamento de Mensagens
# =============================================================================

class WhatsAppClient:
    """
    Cliente para comunicação com a API do WhatsApp.
    Gerencia o envio de mensagens e formatação de números.
    """
    def __init__(self, api_key: str, api_url: str, instance: str):
        """
        Inicializa o cliente WhatsApp.
        
        Args:
            api_key: Chave de autenticação da API
            api_url: URL base da API
            instance: Nome da instância do WhatsApp
        """
        self.api_key = api_key
        self.api_url = api_url.rstrip('/')
        self.instance = instance
        self.headers = {
            "apikey": api_key,
            "Content-Type": "application/json"
        }

    def send_message(self, text: str, number: str, delay: int = 0) -> bool:
        """
        Envia mensagem via WhatsApp com delay opcional.
        
        Args:
            text: Texto da mensagem
            number: Número do destinatário
            delay: Atraso antes do envio em milissegundos
            
        Returns:
            bool: True se envio bem sucedido, False caso contrário
        """
        try:
            url = f"{self.api_url}/message/sendText/{self.instance}"
            payload = {
                "number": self._format_number(number),
                "text": text,
                "delay": delay
            }
            
            # Sistema de retry com backoff
            for attempt in range(MAX_RETRIES):
                try:
                    response = requests.post(
                        url, 
                        json=payload, 
                        headers=self.headers, 
                        timeout=30
                    )
                    response.raise_for_status()
                    return True
                except requests.exceptions.RequestException as e:
                    if attempt == MAX_RETRIES - 1:
                        logger.error(f"Erro após {MAX_RETRIES} tentativas: {str(e)}")
                        return False
                    time.sleep(RETRY_DELAY * (attempt + 1))
            return False
            
        except Exception as e:
            logger.error(f"Erro ao enviar mensagem: {str(e)}")
            return False

    @staticmethod
    def _format_number(number: str) -> str:
        """
        Formata o número para o padrão do WhatsApp.
        
        Args:
            number: Número em qualquer formato
            
        Returns:
            str: Número formatado (ex: 5511999999999)
        """
        number = re.sub(r"\D", "", number)
        if not number.startswith("55") and 10 <= len(number) <= 11:
            number = f"55{number}"
        return number

# Cria instância do WhatsApp
whatsapp = WhatsAppClient(
    api_key=os.getenv("EVOLUTION_API_KEY"),
    api_url=os.getenv("EVOLUTION_API_URL"),
    instance=INSTANCE_NAME)        


# =============================================================================
# Funções de Processamento de Mensagens
# =============================================================================
class SmartMessageProcessor:
    """
    Processador que usa IA para formatar mensagens e simula digitação.
    """
    def __init__(self, llm):
        self.llm = llm
        self.format_prompt = """
        Formate o texto a seguir em partes naturais para envio via WhatsApp.
        Regras:
        - Divida em partes que façam sentido semanticamente
        - Mantenha o contexto em cada parte
        - Retorne apenas as partes separadas por |||
        - Não adicione numeração ou marcadores
        
        Texto: {text}
        """

    async def process_and_send(self, text: str, number: str) -> bool:
        """
        Processa e envia mensagem usando IA para formatação e simula digitação de forma natural.
        """
        try:
            # Monta o prompt com o texto a ser formatado
            prompt_to_send = self.format_prompt.format(text=text)
            logger.debug(f"Prompt enviado para a LLM: {prompt_to_send}")
            
            # Chamada usando o método 'ainvoke'
            formatted_response = await self.llm.ainvoke(prompt_to_send)
            
            # Extrai o texto da resposta; ajuste se o atributo for diferente
            if hasattr(formatted_response, "content"):
                formatted_text = formatted_response.content
            else:
                formatted_text = str(formatted_response)
            
            logger.debug(f"Resposta da LLM: {formatted_text}")

            # Divide a resposta nos separadores definidos
            chunks = [chunk.strip() for chunk in formatted_text.split("|||") if chunk.strip()]

            # Envia cada chunk simulando o tempo de digitação
            for i, chunk in enumerate(chunks):
                # Calcula o delay com base no tamanho do chunk
                typing_delay = calculate_typing_delay(len(chunk))
                # Aguarda o tempo simulado de digitação (converte ms para segundos)
                await asyncio.sleep(typing_delay / 1000)
                
                # Envia a mensagem (passando o delay para a API do WhatsApp, se suportado)
                if not whatsapp.send_message(chunk, number, delay=typing_delay):
                    return False

                # Aguarda um pequeno intervalo após o envio para manter o status "composing" visível
                await asyncio.sleep(0.5)

                # Pausa entre mensagens conforme o conteúdo
                if i < len(chunks) - 1:
                    if '?' in chunk:
                        await asyncio.sleep(2.5)
                    elif '!' in chunk:
                        await asyncio.sleep(2)
                    else:
                        await asyncio.sleep(1.5)

            return True

        except Exception as e:
            logger.error(f"Erro no processamento: {e}")
            return False
 

# Função para calcular o delay de digitação
def calculate_typing_delay(text_length: int) -> int:
    try:
        base_delay = (text_length / 40) * 1000
        variation = base_delay * 0.2
        delay = base_delay + random.uniform(-variation, variation)
        return int(max(2000, min(delay, 5000)))  # Limita entre 2s e 5s
    except Exception:
        return 2000  # Valor padrão em caso de erro

# Uso do processador com a instância Groq (llm2)
smart_processor = SmartMessageProcessor(llm2)

async def send_message_in_chunks(text: str, number: str) -> bool:
    """
    Envia mensagem usando o processador com IA.
    """
    return await smart_processor.process_and_send(text, number)
# =============================================================================
# Processamento de Áudio
# =============================================================================

def transcribe_audio(audio_file_path: str) -> str:
    """
    Transcreve áudio usando o modelo Whisper.
    
    Args:
        audio_file_path: Caminho do arquivo de áudio a ser transcrito
        
    Returns:
        str: Texto transcrito do áudio
    """
    result = model.transcribe(audio_file_path)
    return result['text']

async def handle_audio_message(audio_data: dict, number: str):
    """
    Processa mensagens de áudio do WhatsApp.
    
    Fluxo de processamento:
    1. Decodifica o áudio em base64
    2. Salva em arquivo temporário
    3. Converte para formato WAV
    4. Realiza a transcrição
    5. Processa o texto transcrito
    
    Args:
        audio_data: Dicionário contendo o áudio em base64
        number: Número do remetente
    """
    try:
        logger.info("Iniciando processamento de áudio...")
        
        # Decodificação do base64
        import base64
        audio_base64 = audio_data.get("base64")
        if not audio_base64:
            logger.error("Base64 do áudio não encontrado")
            await send_message_in_chunks(
                "Desculpe, não consegui processar o áudio. Tente novamente.", 
                number
            )
            return

        try:
            audio_bytes = base64.b64decode(audio_base64)
        except Exception as e:
            logger.error(f"Erro ao decodificar base64: {e}")
            await send_message_in_chunks(
                "Desculpe, ocorreu um erro ao processar seu áudio. Tente novamente.", 
                number
            )
            return
        
        # Salvamento temporário do áudio
        with tempfile.NamedTemporaryFile(delete=False, suffix=".opus") as temp_audio_file:
            temp_audio_file.write(audio_bytes)
            temp_audio_file_path = temp_audio_file.name

        try:
            # Conversão para WAV
            wav_path = temp_audio_file_path + ".wav"
            convert_command = f'ffmpeg -i {temp_audio_file_path} -ar 16000 -ac 1 -hide_banner {wav_path}'
            conversion_result = os.system(convert_command)
            
            if conversion_result != 0:
                logger.error(f"Erro na conversão do áudio: {conversion_result}")
                raise Exception("Falha na conversão do áudio")

            # Transcrição
            logger.info("Transcrevendo áudio...")
            transcribed_text = transcribe_audio(wav_path)
            
            if not transcribed_text:
                logger.error("Transcrição retornou vazia")
                await send_message_in_chunks(
                    "Desculpe, não consegui entender o áudio. Pode tentar novamente?", 
                    number
                )
                return

            logger.info(f"Áudio transcrito: {transcribed_text}")
            
            # Processamento do texto
            await handle_message_with_buffer(transcribed_text, number)

        finally:
            # Limpeza dos arquivos temporários
            try:
                os.remove(temp_audio_file_path)
                if os.path.exists(wav_path):
                    os.remove(wav_path)
            except Exception as e:
                logger.error(f"Erro ao remover arquivos temporários: {e}")

    except Exception as e:
        logger.error(f"Erro no processamento de áudio: {str(e)}", exc_info=True)
        await send_message_in_chunks(
            "Desculpe, ocorreu um erro ao processar seu áudio. Tente novamente.", 
            number
        )

# =============================================================================
# Processamento de Mensagens
# =============================================================================

async def process_message(message: str, number: str) -> bool:
    """
    Processa uma mensagem e gera resposta usando o agente.
    
    Fluxo:
    1. Registra mensagem no histórico
    2. Invoca o agente com contexto completo
    3. Envia resposta em chunks
    
    Args:
        message: Texto da mensagem
        number: Número do remetente
        
    Returns:
        bool: True se processamento bem sucedido
    """
    try:
        logger.info(f"Processando mensagem: {message} de {number}")

        # Inicializa histórico se necessário
        if number not in conversation_history:
            conversation_history[number] = []

        # Adiciona mensagem ao histórico
        conversation_history[number].append({
            "role": "user", 
            "content": message
        })

        # Invoca o agente
        result = agent_executor.invoke({
            "input": message,
            "history": conversation_history[number]
        })

        # Processa resposta
        response = result.get(
            "output", 
            "Desculpe, não consegui processar sua mensagem."
        )

        # Atualiza histórico
        conversation_history[number].append({
            "role": "assistant", 
            "content": response
        })

        logger.debug(f"Enviando resposta em chunks: {response}")
        return await send_message_in_chunks(response, number)
        
    except Exception as e:
        logger.error(f"Erro no processamento: {str(e)}", exc_info=True)
        await send_message_in_chunks(
            "Desculpe, ocorreu um erro. Tente novamente.", 
            number
        )
        return False

async def handle_message_with_buffer(message: str, number: str):
    """
    Gerencia mensagens usando sistema de buffer.
    
    Características:
    - Agrupa mensagens próximas para processamento conjunto
    - Evita processamento duplicado
    - Mantém contexto da conversa
    
    Args:
        message: Texto da mensagem
        number: Número do remetente
    """
    try:
        buffer_time = 10  # Tempo de buffer em segundos
        
        # Inicializa/atualiza buffer
        if number not in message_buffer:
            message_buffer[number] = {
                "messages": [],
                "last_activity": time.time(),
                "processing": False
            }
        
        # Adiciona mensagem
        message_buffer[number]["messages"].append(message)
        message_buffer[number]["last_activity"] = time.time()
        
        # Verifica se já está processando
        if message_buffer[number]["processing"]:
            return
        
        # Marca como em processamento
        message_buffer[number]["processing"] = True
        
        try:
            # Aguarda próximas mensagens
            while time.time() - message_buffer[number]["last_activity"] < buffer_time:
                await asyncio.sleep(1)
            
            # Recupera mensagens e limpa buffer
            messages = message_buffer[number]["messages"]
            message_buffer.pop(number)
            
            if not messages:
                return
                
            # Processa contexto completo
            full_context = " ".join(messages)
            logger.info(f"Processando contexto de {number}: {full_context}")
            await process_message(full_context, number)
            
        finally:
            # Garante limpeza do buffer
            if number in message_buffer:
                message_buffer[number]["processing"] = False
        
    except Exception as e:
        logger.error(f"Erro ao processar mensagens com buffer: {str(e)}")
        await send_message_in_chunks(
            "Desculpe, ocorreu um erro. Tente novamente.", 
            number
        )

# Configuração das ferramentas disponíveis
tools = [
    Tool(
        name="site_knowledge",
        func=query_site_knowledge,
        description="Consulta informações específicas do site nerai.com.br. Use esta ferramenta para responder perguntas sobre a empresa e seus serviços."
    )
]

# Prompt do sistema com instruções detalhadas
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

# Template do prompt com histórico
prompt = PromptTemplate.from_template(
    template=(
        "{system_prompt}\n\n"
        "Histórico da Conversa:\n{history}\n\n"
        "Solicitação Atual: {input}\n\n"
        "Histórico de Ações:\n{agent_scratchpad}\n"
    )
)

# Inicialização do agente
prompt = prompt.partial(system_prompt=SYSTEM_PROMPT)
agent = create_openai_functions_agent(llm, tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

# =============================================================================
# Rotas e Webhook
# =============================================================================

@app.route('/webhook', methods=['POST'])
async def webhook():
    """
    Endpoint principal para receber webhooks do WhatsApp.
    
    Fluxo de processamento:
    1. Recebe e valida dados do webhook
    2. Identifica tipo de mensagem
    3. Processa áudio ou texto conforme necessário
    4. Retorna status apropriado
    
    Returns:
        tuple: (resposta_json, código_http)
    """
    try:
        # Recebe e loga dados
        data = await request.get_json() 
        logger.debug(f"Webhook recebido: {data}")
        
        if not data:
            return jsonify({"status": "ignored"}), 200
        
        # Extrai informações principais
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

        # Extrai número do remetente
        remote_jid = (
            message_data.get("key", {}).get("remoteJid", "") or 
            message_data.get("remoteJid", "") or 
            message_data.get("jid", "")
        )
        number = remote_jid.split("@")[0].split(":")[0] if remote_jid else ""
        
        if not number:
            return jsonify({"status": "ignored"}), 200

        # Processa mensagens por tipo
        if event_type == "messages.upsert":
            msg_content = message_data.get("message", {})
            logger.debug(f"Conteúdo da mensagem: {msg_content}")

            # Processa áudio
            if "audioMessage" in msg_content:
                logger.info("Mensagem de áudio detectada")
                base64_data = message_data.get("message", {}).get("base64")
                
                if base64_data:
                    logger.info("Base64 do áudio encontrado")
                    await handle_audio_message({"base64": base64_data}, number)
                    return jsonify({"status": "processed"}), 200
                else:
                    logger.error("Base64 do áudio não encontrado")
                    await send_message_in_chunks(
                        "Desculpe, não consegui processar o áudio. Tente novamente.", 
                        number
                    )
                    return jsonify({
                        "status": "error", 
                        "message": "Base64 não encontrado"
                    }), 200

            # Processa texto
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
        return jsonify({
            "status": "error", 
            "message": str(e)
        }), 500

# =============================================================================
# Inicialização da Aplicação
# =============================================================================

async def init_app():
    """
    Inicializa todos os componentes da aplicação.
    """
    try:
        await site_knowledge.initialize()
        logger.info("Base de conhecimento inicializada com sucesso!")
    except Exception as e:
        logger.error(f"Erro ao inicializar a aplicação: {str(e)}")

@app.before_serving
async def startup():
    """Hook executado antes do servidor iniciar."""
    await init_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)                            