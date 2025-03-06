#config.py
import os
import logging
from typing import Dict, List, Any
from dataclasses import dataclass
from enum import Enum
from dotenv import load_dotenv
from supabase import create_client, Client

logger = logging.getLogger(__name__)

class ModelProvider(Enum):
    """Define os provedores de IA disponíveis"""
    OPENAI = "openai"
    GROQ = "groq"
    CLAUDE = "claude"

@dataclass
class ModelConfig:
    """Configuração para modelos de IA"""
    name: str             # Nome do modelo
    provider: ModelProvider  # Provedor
    temperature: float = 0.3  # Temperatura (criatividade)
    max_tokens: int = 400    # Máximo de tokens

@dataclass
class APIConfig:
    """Configurações das APIs"""
    openai_key: str
    evolution_key: str
    evolution_url: str
    groq_key: str
    anthropic_key: str

@dataclass
class WhatsAppConfig:
    """Configurações do WhatsApp"""
    instance_name: str = "nerai"
    max_retries: int = 3     # Máximo de tentativas
    retry_delay: int = 1     # Delay entre tentativas
    timeout: int = 30        # Timeout das requisições

@dataclass
class SupabaseConfig:
    """Configurações do Supabase"""
    url: str
    key: str

@dataclass
class CalendarConfig:
    """Configurações do Cal.com"""
    api_key: str
    base_url: str = "https://api.cal.com/v1"
    default_event_type_id: int = 1  # ID padrão para tipos de eventos
    time_zone: str = "America/Sao_Paulo"
    buffer_time: int = 15  # Buffer em minutos entre reuniões
    default_duration: int = 60  # Duração padrão da reunião em minutos

class ConfigurationManager:
    """Classe principal que gerencia todas as configurações"""

    #Define variáveis de ambiente necessárias
    REQUIRED_ENV = {
        "OPENAI_API_KEY": "Chave da API OpenAI",
        "EVOLUTION_API_KEY": "Chave da API Evolution",
        "EVOLUTION_API_URL": "URL da API Evolution",
        "GROQ_API_KEY": "Chave da API Groq",
        "ANTHROPIC_API_KEY": "Chave da API Claude",
        "SUPABASE_URL": "URL do Supabase",
        "SUPABASE_KEY": "Chave da API do Supabase",
        "CAL_API_KEY": "Chave da API do Cal.com",
        "CAL_EVENT_TYPE_ID": "ID do tipo de evento padrão no Cal.com"
    }

    # Define configurações dos modelos
    MODELS = {
        ModelProvider.OPENAI: ModelConfig(
            name="gpt-4o-mini",
            provider=ModelProvider.OPENAI
        ),
        ModelProvider.GROQ: ModelConfig(
            name="llama-3.3-70b-versatile",
            provider=ModelProvider.GROQ
        ),
        ModelProvider.CLAUDE: ModelConfig(
            name="claude-3-opus-20240229",
            provider=ModelProvider.CLAUDE,
            temperature=0.3,
            max_tokens=4096
        )
    }

    def __init__(self):
        """Inicializa o gerenciador de configurações."""
        self._load_environment()
        self.api_config = self._load_api_config()
        self.whatsapp_config = self._load_whatsapp_config()
        self.supabase_config = self._load_supabase_config()
        self.calendar_config = self._load_calendar_config()

    def _load_environment(self) -> None:
        """
        Carrega e valida variáveis de ambiente.
        
        Raises:
            EnvironmentError: Se variáveis requeridas estiverem faltando
        """
        load_dotenv(override=True)
        
        missing = [
            f"{key} ({desc})"
            for key, desc in self.REQUIRED_ENV.items()
            if not os.getenv(key)
        ]
        
        if missing:
            error_msg = f"Variáveis de ambiente faltando:\n" + "\n".join(missing)
            logger.error(error_msg)
            raise EnvironmentError(error_msg)

    def _load_api_config(self) -> APIConfig:
        """
        Carrega configurações das APIs.
        
        Returns:
            APIConfig: Configurações das APIs
        """
        return APIConfig(
            openai_key=os.getenv("OPENAI_API_KEY", ""),
            evolution_key=os.getenv("EVOLUTION_API_KEY", ""),
            evolution_url=os.getenv("EVOLUTION_API_URL", ""),
            groq_key=os.getenv("GROQ_API_KEY", ""),
            anthropic_key=os.getenv("ANTHROPIC_API_KEY", "")
        )

    def _load_whatsapp_config(self) -> WhatsAppConfig:
        """
        Carrega configurações do WhatsApp.
        
        Returns:
            WhatsAppConfig: Configurações do WhatsApp
        """
        return WhatsAppConfig(
            instance_name=os.getenv("INSTANCE_NAME", "nerai"),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            retry_delay=int(os.getenv("RETRY_DELAY", "1")),
            timeout=int(os.getenv("TIMEOUT", "30"))
        )

    def _load_supabase_config(self) -> SupabaseConfig:
        """
        Carrega configurações do Supabase.
        
        Returns:
            SupabaseConfig: Configurações do Supabase
        """
        return SupabaseConfig(
            url=os.getenv("SUPABASE_URL", ""),
            key=os.getenv("SUPABASE_KEY", "")
        )

    def _load_calendar_config(self) -> CalendarConfig:
        """
        Carrega configurações do Cal.com.
        
        Returns:
            CalendarConfig: Configurações do Cal.com
        """
        return CalendarConfig(
            api_key=os.getenv("CAL_API_KEY", ""),
            base_url=os.getenv("CAL_BASE_URL", "https://api.cal.com/v1"),
            default_event_type_id=int(os.getenv("CAL_EVENT_TYPE_ID")),
            time_zone=os.getenv("CAL_TIME_ZONE", "America/Sao_Paulo"),
            buffer_time=int(os.getenv("CAL_BUFFER_TIME", "15")),
            default_duration=int(os.getenv("CAL_DEFAULT_DURATION", "60"))
        )

    @property
    def environment(self) -> Dict[str, Any]:
        """Retorna todas as variáveis de ambiente carregadas."""
        return {
            key: os.getenv(key)
            for key in self.REQUIRED_ENV
        }

    def get_model_config(self, provider: ModelProvider) -> ModelConfig:
        """
        Retorna a configuração do modelo para um provedor específico.
        
        Args:
            provider (ModelProvider): Provedor do modelo

        Returns:
            ModelConfig: Configuração do modelo
        """
        return self.MODELS.get(provider)

# Cria instância global do gerenciador
config_manager = ConfigurationManager()

# Exporta configurações para uso em outros módulos
INSTANCE_NAME = config_manager.whatsapp_config.instance_name
MAX_RETRIES = config_manager.whatsapp_config.max_retries
RETRY_DELAY = config_manager.whatsapp_config.retry_delay

# Exporta configurações dos modelos
OPENAI_MODEL = config_manager.get_model_config(ModelProvider.OPENAI).name
GROQ_MODEL = config_manager.get_model_config(ModelProvider.GROQ).name
ANTHROPIC_MODEL = config_manager.get_model_config(ModelProvider.CLAUDE).name

# Exporta configurações de API
API_CONFIG = config_manager.api_config

# Exporta configurações do Cal.com
CALENDAR_CONFIG = config_manager.calendar_config

# Exporta configurações do Supabase
SUPABASE_CONFIG = config_manager.supabase_config
SUPABASE_CLIENT = create_client(SUPABASE_CONFIG.url, SUPABASE_CONFIG.key)