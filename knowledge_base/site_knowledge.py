import os
import time
import logging
from typing import Optional, List, Any, Dict
from dataclasses import dataclass, field
from enum import Enum

from bs4 import BeautifulSoup
from langchain_community.document_loaders import PlaywrightURLLoader, TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings

logger = logging.getLogger(__name__)

class KnowledgeSource(Enum):
    WEBSITE = "website"
    STAGES = "stages"

# Constantes de configuração
KNOWLEDGE_BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge_base")
UPDATE_INTERVAL = 86400  # 1 dia em segundos
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 250
DEFAULT_RESULTS = 3
SITE_URL = "https://nerai.com.br"

@dataclass
class KnowledgeBaseConfig:
    """Configuração para a base de conhecimento."""
    base_dir: str = KNOWLEDGE_BASE_DIR
    update_interval: int = UPDATE_INTERVAL
    chunk_size: int = CHUNK_SIZE
    chunk_overlap: int = CHUNK_OVERLAP
    urls: List[str] = field(default_factory=lambda: [SITE_URL])

    def __post_init__(self):
        """Inicializa diretórios após a criação da instância."""
        self.stages_dir = os.path.join(self.base_dir, "stages")
        self.website_dir = os.path.join(self.base_dir, "website")
        logger.debug(f"Base dir: {self.base_dir}")
        logger.debug(f"Stages dir: {self.stages_dir}")
        logger.debug(f"Website dir: {self.website_dir}")
        
        os.makedirs(self.stages_dir, exist_ok=True)
        os.makedirs(self.website_dir, exist_ok=True)
        
    def get_source_dir(self, source: KnowledgeSource) -> str:
        """Retorna o diretório específico para cada fonte de conhecimento."""
        if source == KnowledgeSource.WEBSITE:
            return self.website_dir
        return self.stages_dir

class SafeFAISS(FAISS):
    """Extensão segura da classe FAISS para carregamento de embeddings."""
    def __init__(self, embedding_function, index, docstore, index_to_docstore_id):
        """Inicializa o SafeFAISS com todos os parâmetros necessários."""
        super().__init__(embedding_function, index, docstore, index_to_docstore_id)

    @classmethod
    def load_local(cls, folder_path: str, embeddings: Any, allow_dangerous_deserialization: bool = True) -> FAISS:
        """Carrega uma base FAISS local de forma segura."""
        return super().load_local(folder_path, embeddings, allow_dangerous_deserialization=allow_dangerous_deserialization)

    def save_local(self, folder_path: str, allow_dangerous_deserialization: bool = True):
        """Salva o índice FAISS localmente de forma segura."""
        super().save_local(folder_path)

class SiteKnowledge:
    """Gerenciador da base de conhecimento."""
    def __init__(self, config: Optional[KnowledgeBaseConfig] = None):
        self.config = config or KnowledgeBaseConfig()
        self.vectorstores: Dict[KnowledgeSource, Optional[SafeFAISS]] = {
            KnowledgeSource.WEBSITE: None,
            KnowledgeSource.STAGES: None
        }
        self.last_updates: Dict[KnowledgeSource, Optional[float]] = {
            KnowledgeSource.WEBSITE: None,
            KnowledgeSource.STAGES: None
        }

    def needs_update(self, source: KnowledgeSource) -> bool:
        """Verifica se uma fonte específica precisa ser atualizada."""
        source_dir = self.config.get_source_dir(source)
        logger.debug(f"Verificando necessidade de atualização para {source.value} em {source_dir}")
        
        index_path = os.path.join(source_dir, "index.faiss")
        if not os.path.exists(index_path):
            logger.debug(f"Arquivo index.faiss não encontrado em: {index_path}")
            return True
        if not self.last_updates[source]:
            logger.debug(f"Não há registro de última atualização para {source.value}")
            return True
        needs_update = (time.time() - self.last_updates[source]) > self.config.update_interval
        logger.debug(f"Base {source.value} precisa atualizar: {needs_update}")
        return needs_update

    async def initialize(self):
        """Inicializa todas as bases de conhecimento."""
        try:
            logger.info("Inicializando bases de conhecimento...")
            for source in KnowledgeSource:
                self.vectorstores[source] = await self.load_knowledge_base(source)
                if not self.vectorstores[source] or self.needs_update(source):
                    logger.info(f"Criando nova base para {source.value}...")
                    self.vectorstores[source] = await self.create_knowledge_base(source)
                else:
                    logger.info(f"Base {source.value} carregada com sucesso.")
        except Exception as e:
            logger.error(f"Erro na inicialização das bases: {str(e)}")

    async def create_knowledge_base(self, source: KnowledgeSource) -> Optional[SafeFAISS]:
        """Cria uma nova base de conhecimento para uma fonte específica."""
        try:
            logger.info(f"Iniciando criação da base de conhecimento para {source.value}...")
            
            documents = await self._load_documents(source)
            if not documents:
                logger.error(f"Nenhum documento carregado para {source.value}")
                return None

            content = self._process_documents(documents, source)
            splits = self._split_content(content)
            
            vectorstore = self._create_vectorstore(splits)
            self._save_vectorstore(vectorstore, source)
            
            return vectorstore
            
        except Exception as e:
            logger.error(f"Erro ao criar base de conhecimento {source.value}: {str(e)}", exc_info=True)
            return None

    async def _load_documents(self, source: KnowledgeSource) -> List[Document]:
        """Carrega documentos baseado na fonte."""
        if source == KnowledgeSource.WEBSITE:
            loader = PlaywrightURLLoader(
                urls=self.config.urls,
                remove_selectors=["nav", "footer", "header"]
            )
            return await loader.aload()
        elif source == KnowledgeSource.STAGES:
            documents = []
            stages_dir = self.config.stages_dir
            logger.debug(f"Tentando carregar documentos do diretório: {stages_dir}")
            
            if not os.path.exists(stages_dir):
                logger.error(f"Diretório de estágios não encontrado: {stages_dir}")
                return []
                
            for i in range(1, 7):
                file_path = os.path.join(stages_dir, f"estagio_{i}.txt")
                logger.debug(f"Verificando arquivo: {file_path}")
                
                if os.path.exists(file_path):
                    try:
                        loader = TextLoader(file_path, encoding='utf-8')
                        stage_docs = loader.load()
                        logger.debug(f"Carregado estágio {i} com sucesso")
                        
                        for doc in stage_docs:
                            doc.metadata['stage'] = i
                            doc.metadata['source'] = 'stages'
                        documents.extend(stage_docs)
                    except Exception as e:
                        logger.error(f"Erro ao carregar estágio {i}: {str(e)}")
                else:
                    logger.debug(f"Arquivo não encontrado: {file_path}")
                    
            return documents

    def _process_documents(self, documents: List[Document], source: KnowledgeSource) -> List[Document]:
        """Processa documentos baseado na fonte."""
        processed_docs = []
        for doc in documents:
            if source == KnowledgeSource.WEBSITE:
                soup = BeautifulSoup(doc.page_content, 'html.parser')
                for script in soup(["script", "style"]):
                    script.decompose()
                text = soup.get_text(strip=True)
                processed_docs.append(Document(
                    page_content=text,
                    metadata={'source': 'website', **doc.metadata}
                ))
            else:
                processed_docs.append(doc)
        return processed_docs

    def _split_content(self, content: List[Document]) -> List[Document]:
        """Divide o conteúdo em chunks."""
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""]
        )
        splits = splitter.split_documents(content)
        logger.info(f"{len(splits)} chunks criados.")
        return splits

    def _create_vectorstore(self, splits: List[Document]) -> SafeFAISS:
        """Cria a base vetorial usando SafeFAISS."""
        logger.info("Criando embeddings...")
        embeddings = OpenAIEmbeddings()
        base_vectorstore = FAISS.from_documents(splits, embeddings)
        
        # Criação do SafeFAISS com todos os parâmetros necessários
        return SafeFAISS(
            embedding_function=base_vectorstore.embedding_function,
            index=base_vectorstore.index,
            docstore=base_vectorstore.docstore,
            index_to_docstore_id=base_vectorstore.index_to_docstore_id
        )

    def _save_vectorstore(self, vectorstore: FAISS, source: KnowledgeSource):
        """Salva a base vetorial específica no disco."""
        source_dir = self.config.get_source_dir(source)
        os.makedirs(source_dir, exist_ok=True)
        if isinstance(vectorstore, SafeFAISS):
            vectorstore.save_local(source_dir, allow_dangerous_deserialization=True)
        else:
            # Se não for SafeFAISS, converte para SafeFAISS
            safe_vectorstore = SafeFAISS(
                embedding_function=vectorstore.embedding_function,
                index=vectorstore.index,
                docstore=vectorstore.docstore,
                index_to_docstore_id=vectorstore.index_to_docstore_id
            )
            safe_vectorstore.save_local(source_dir, allow_dangerous_deserialization=True)
        self.last_updates[source] = time.time()
        logger.info(f"Base de conhecimento {source.value} salva com sucesso!")

    async def load_knowledge_base(self, source: KnowledgeSource) -> Optional[SafeFAISS]:
        """Carrega uma base de conhecimento específica do disco."""
        try:
            source_dir = self.config.get_source_dir(source)
            logger.debug(f"Tentando carregar base de {source_dir}")
            
            if not os.path.exists(source_dir):
                logger.debug(f"Diretório {source_dir} não existe")
                return None
                
            embeddings = OpenAIEmbeddings()
            return SafeFAISS.load_local(source_dir, embeddings, allow_dangerous_deserialization=True)
        except Exception as e:
            logger.error(f"Erro ao carregar base {source.value}: {str(e)}")
            return None

    def query(self, question: str, source: Optional[KnowledgeSource] = None, k: int = DEFAULT_RESULTS) -> str:
        """Consulta uma ou todas as bases de conhecimento."""
        try:
            if source:
                if not self.vectorstores[source]:
                    return f"Base de conhecimento {source.value} não disponível."
                docs = self.vectorstores[source].similarity_search(question, k=k)
                return self._format_response(docs)
            
            all_docs = []
            for src, vectorstore in self.vectorstores.items():
                if vectorstore:
                    docs = vectorstore.similarity_search(question, k=k)
                    all_docs.extend(docs)
            
            return self._format_response(all_docs[:k])
            
        except Exception as e:
            logger.error(f"Erro na consulta: {str(e)}")
            return "Erro ao consultar a base de conhecimento."

    def _format_response(self, docs: List[Document]) -> str:
        """Formata a resposta com informações sobre a fonte."""
        formatted_responses = []
        for doc in docs:
            source = doc.metadata.get('source', 'desconhecida')
            stage = doc.metadata.get('stage', '')
            
            if source == 'stages' and stage:
                header = f"[Estágio {stage}]"
            elif source == 'website':
                header = "[Website]"
            else:
                header = "[Fonte desconhecida]"
                
            formatted_responses.append(f"{header}\n{doc.page_content}")
            
        return "\n\n".join(formatted_responses)