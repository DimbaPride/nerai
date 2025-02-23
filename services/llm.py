import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass

from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from langchain_anthropic import ChatAnthropic
from config import OPENAI_MODEL, GROQ_MODEL, ANTHROPIC_MODEL

logger = logging.getLogger(__name__)

@dataclass
class LLMConfig:
    """Configuração base para modelos de linguagem."""
    temperature: float = 0.3
    request_timeout: int = 30
    max_retries: int = 3
    
@dataclass
class OpenAIConfig(LLMConfig):
    """Configuração específica para OpenAI."""
    model: str = OPENAI_MODEL
    
@dataclass
class GroqConfig(LLMConfig):
    """Configuração específica para Groq."""
    model: str = GROQ_MODEL

@dataclass
class ClaudeConfig(LLMConfig):
    """Configuração específica para Claude."""
    model: str = ANTHROPIC_MODEL

class LLMManager:
    """Gerenciador de modelos de linguagem."""
    
    def __init__(
        self,
        openai_config: Optional[OpenAIConfig] = None,
        groq_config: Optional[GroqConfig] = None,
        claude_config: Optional[ClaudeConfig] = None
    ):
        """
        Inicializa o gerenciador de LLMs.
        
        Args:
            openai_config: Configuração opcional para OpenAI
            groq_config: Configuração opcional para Groq
            claude_config: Configuração opcional para Claude
        """
        self.openai_config = openai_config or OpenAIConfig()
        self.groq_config = groq_config or GroqConfig()
        self.claude_config = claude_config or ClaudeConfig()
        
        self._llm_openai: Optional[ChatOpenAI] = None
        self._llm_groq: Optional[ChatGroq] = None
        self._llm_claude: Optional[ChatAnthropic] = None
        
    @property
    def llm_openai(self) -> ChatOpenAI:
        """Instância singleton do modelo OpenAI."""
        if self._llm_openai is None:
            try:
                # Passando request_timeout diretamente como argumento, não em model_kwargs
                self._llm_openai = ChatOpenAI(
                    model=self.openai_config.model,
                    temperature=self.openai_config.temperature,
                    max_retries=self.openai_config.max_retries,
                    request_timeout=self.openai_config.request_timeout
                )
            except Exception as e:
                logger.error(f"Erro ao inicializar OpenAI: {e}")
                raise RuntimeError("Falha ao inicializar modelo OpenAI")
        return self._llm_openai
    
    @property
    def llm_groq(self) -> ChatGroq:
        """Instância singleton do modelo Groq."""
        if self._llm_groq is None:
            try:
                self._llm_groq = ChatGroq(
                    model=self.groq_config.model,
                    temperature=self.groq_config.temperature,
                    max_retries=self.groq_config.max_retries,
                    request_timeout=self.groq_config.request_timeout
                )
            except Exception as e:
                logger.error(f"Erro ao inicializar Groq: {e}")
                raise RuntimeError("Falha ao inicializar modelo Groq")
        return self._llm_groq

    @property
    def llm_claude(self) -> ChatAnthropic:
        """Instância singleton do modelo Claude."""
        if self._llm_claude is None:
            try:
                self._llm_claude = ChatAnthropic(
                    model=self.claude_config.model,
                    temperature=self.claude_config.temperature,
                    max_retries=self.claude_config.max_retries,
                    # O request_timeout não é suportado pelo Claude
                )
            except Exception as e:
                logger.error(f"Erro ao inicializar Claude: {e}")
                raise RuntimeError("Falha ao inicializar modelo Claude")
        return self._llm_claude
    
    def get_llm(self, provider: str = "openai") -> Any:
        """
        Retorna a instância do LLM especificado.
        
        Args:
            provider: Nome do provedor ("openai", "groq" ou "claude")
            
        Returns:
            ChatOpenAI, ChatGroq ou ChatAnthropic: Instância do modelo
        """
        providers = {
            "openai": self.llm_openai,
            "groq": self.llm_groq,
            "claude": self.llm_claude
        }
        
        if provider.lower() not in providers:
            raise ValueError(f"Provedor desconhecido: {provider}")
            
        return providers[provider.lower()]

# Cria instância global do gerenciador
llm_manager = LLMManager()

# Exporta instâncias dos modelos para compatibilidade
llm_openai = llm_manager.llm_openai
llm_groq = llm_manager.llm_groq
llm_claude = llm_manager.llm_claude

# Função helper para obter LLM por nome
def get_llm(provider: str = "openai") -> Any:
    """Função helper para obter instância de LLM por nome."""
    return llm_manager.get_llm(provider)