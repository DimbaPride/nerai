#audio_processing.py
import os
import tempfile
import logging
import base64
import asyncio
from typing import Optional, Tuple
from dataclasses import dataclass

import whisper
from utils.smart_message_processor import send_message_in_chunks
from utils.message_buffer import handle_message_with_buffer


logger = logging.getLogger(__name__)
os.environ['KMP_DUPLICATE_LIB_OK']='TRUE'


@dataclass
class AudioConfig:
    """Configuração para processamento de áudio."""
    model_type: str = "base"
    sample_rate: int = 16000
    channels: int = 1
    temp_suffix: str = ".opus"
    error_message: str = "Desculpe, não consegui processar o áudio. Tente novamente."

class AudioProcessor:
    """Gerenciador de processamento de áudio e transcrição."""
    
    def __init__(self, config: Optional[AudioConfig] = None):
        """
        Inicializa o processador de áudio.
        
        Args:
            config: Configurações opcionais para o processamento
        """
        self.config = config or AudioConfig()
        self.model = self._load_model()
        
    def _load_model(self) -> whisper.Whisper:
        """Carrega o modelo Whisper."""
        try:
            return whisper.load_model(self.config.model_type)
        except Exception as e:
            logger.error(f"Erro ao carregar modelo Whisper: {e}")
            raise RuntimeError("Falha ao inicializar modelo de transcrição")

    def transcribe_audio(self, audio_path: str) -> str:
        """
        Transcreve um arquivo de áudio para texto.
        
        Args:
            audio_path: Caminho do arquivo de áudio
            
        Returns:
            str: Texto transcrito
        """
        try:
            result = self.model.transcribe(audio_path)
            return result['text']
        except Exception as e:
            logger.error(f"Erro na transcrição: {e}")
            raise RuntimeError("Falha na transcrição do áudio")

    async def _create_temp_files(self, audio_bytes: bytes) -> Tuple[str, str]:
        """
        Cria arquivos temporários para processamento.
        
        Returns:
            Tuple[str, str]: Caminhos dos arquivos temporários (opus, wav)
        """
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=self.config.temp_suffix) as temp_file:
                temp_file.write(audio_bytes)
                temp_path = temp_file.name
                wav_path = f"{temp_path}.wav"
                return temp_path, wav_path
        except Exception as e:
            logger.error(f"Erro ao criar arquivos temporários: {e}")
            raise RuntimeError("Falha ao criar arquivos temporários")

    def _convert_to_wav(self, input_path: str, output_path: str) -> None:
        """
        Converte áudio para formato WAV usando ffmpeg.
        
        Args:
            input_path: Caminho do arquivo de entrada
            output_path: Caminho do arquivo de saída
        """
        command = (
            f'ffmpeg -i {input_path} '
            f'-ar {self.config.sample_rate} '
            f'-ac {self.config.channels} '
            f'-hide_banner {output_path}'
        )
        
        if os.system(command) != 0:
            raise RuntimeError("Falha na conversão do áudio")

    def _cleanup_files(self, *file_paths: str) -> None:
        """Remove arquivos temporários."""
        for path in file_paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                logger.error(f"Erro ao remover arquivo {path}: {e}")

    async def process_audio(self, audio_data: dict, number: str) -> None:
        """
        Processa uma mensagem de áudio do WhatsApp.
        
        Args:
            audio_data: Dicionário contendo o áudio em base64
            number: Número do remetente
        """
        temp_path = wav_path = None
        
        try:
            # Validação do audio_data
            audio_base64 = audio_data.get("base64")
            if not audio_base64:
                logger.error("Base64 do áudio não encontrado")
                await send_message_in_chunks(self.config.error_message, number)
                return

            # Decodificação do base64
            try:
                audio_bytes = base64.b64decode(audio_base64)
            except Exception as e:
                logger.error(f"Erro ao decodificar base64: {e}")
                await send_message_in_chunks(self.config.error_message, number)
                return

            # Criação dos arquivos temporários
            temp_path, wav_path = await self._create_temp_files(audio_bytes)

            # Conversão para WAV
            self._convert_to_wav(temp_path, wav_path)

            # Transcrição
            transcribed_text = self.transcribe_audio(wav_path)
            if not transcribed_text:
                raise ValueError("Transcrição vazia")

            logger.info(f"Áudio transcrito: {transcribed_text}")
            
            # Processamento do texto transcrito
            await handle_message_with_buffer(transcribed_text, number)

        except Exception as e:
            logger.error(f"Erro no processamento de áudio: {e}", exc_info=True)
            await send_message_in_chunks(self.config.error_message, number)
            
        finally:
            # Limpeza dos arquivos temporários
            if temp_path or wav_path:
                self._cleanup_files(temp_path, wav_path)

# Instância global do processador
audio_processor = AudioProcessor()

# Função de interface para manter compatibilidade
async def handle_audio_message(audio_data: dict, number: str) -> None:
    """Função de interface para processar mensagens de áudio."""
    await audio_processor.process_audio(audio_data, number)