from typing import Dict, List, Optional, Any
from datetime import datetime
import logging
import re
import asyncio
from zoneinfo import ZoneInfo

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from services.calendar_service import calendar_service, CalendarServiceError
from config import CALENDAR_CONFIG

logger = logging.getLogger(__name__)


class AsyncTool(BaseTool):
    """Ferramenta que suporta apenas execução assíncrona."""
    
    def _run(self, *args, **kwargs):
        """
        Implementação síncrona que lança exceção.
        Esta ferramenta só suporta execução assíncrona.
        """
        raise NotImplementedError(
            f"A ferramenta {self.name} não suporta execução síncrona. Use a versão assíncrona."
        )
    
    async def _arun(self, *args, **kwargs):
        """Método assíncrono a ser implementado pelas subclasses."""
        raise NotImplementedError("Subclasses de AsyncTool devem implementar _arun")


# Modelos Pydantic para os argumentos das ferramentas
class CalendarScheduleArgs(BaseModel):
    start_time: str = Field(..., description="Data e hora no formato YYYY-MM-DDTHH:MM:SS")
    name: str = Field(..., description="Nome completo do cliente")
    email: str = Field(..., description="Email do cliente")
    phone: Optional[str] = Field(None, description="Número de telefone (opcional)")
    notes: Optional[str] = Field(None, description="Observações adicionais (opcional)")

class CalendarRescheduleArgs(BaseModel):
    booking_id: str = Field(..., description="ID da reserva a ser reagendada")
    new_start_time: str = Field(..., description="Nova data e hora no formato YYYY-MM-DDTHH:MM:SS")

class CalendarCancelArgs(BaseModel):
    booking_id: str = Field(..., description="ID da reserva a ser cancelada")


# Classes base para ferramentas de calendário
class BaseCalendarTool(AsyncTool):
    """Classe base para todas as ferramentas de calendário"""
    name: str = ""
    description: str = ""
    
    def __init__(self, whatsapp_context: Dict):
        """Inicializa a ferramenta com contexto do WhatsApp."""
        super().__init__()  # Importante chamar o construtor da classe pai
        self._whatsapp_context = whatsapp_context
        self._tz = ZoneInfo(CALENDAR_CONFIG.time_zone)
    
    @property
    def whatsapp_context(self) -> Dict:
        """Getter para o contexto do WhatsApp"""
        return self._whatsapp_context
    
    @property
    def tz(self) -> ZoneInfo:
        """Getter para o timezone"""
        return self._tz


class AsyncCalendarCheckTool(BaseCalendarTool):
    name: str = "calendar_check"
    description: str = "Verificar horários disponíveis no calendário"
    
    async def _arun(self, *args, **kwargs) -> str:
        """Verifica disponibilidade de horários no calendário."""
        # Extrair days_ahead dos argumentos
        days_ahead = 7  # Valor padrão
        
        # Processar kwargs primeiro (tem prioridade)
        if 'days_ahead' in kwargs:
            days_ahead = kwargs['days_ahead']
        # Depois processar args se kwargs não tiver o parâmetro necessário
        elif len(args) > 0:
            days_ahead = args[0]
        # Por último, processar um possível 'args' passado erroneamente como kwargs
        elif 'args' in kwargs and isinstance(kwargs['args'], (list, tuple)) and len(kwargs['args']) > 0:
            days_ahead = kwargs['args'][0]
        
        logger.debug(f"Verificando disponibilidade para {days_ahead} dias")
        try:
            # Garantir que days_ahead seja um inteiro
            if isinstance(days_ahead, str):
                try:
                    days_ahead = int(days_ahead)
                except ValueError:
                    return ("Por favor, forneça um número válido de dias. "
                            "Por exemplo: para ver os próximos 7 dias, use 'calendar_check(7)'")
            
            # Validar o range de dias
            if days_ahead < 1:
                days_ahead = 7
            elif days_ahead > 60:  # Limite máximo de 60 dias
                days_ahead = 60
                
            logger.debug(f"Buscando slots disponíveis para os próximos {days_ahead} dias")
            
            # Usar método do serviço de calendário diretamente
            slots = await calendar_service.get_availability(days_ahead=days_ahead)
            
            if not slots.get("slots"):
                return ("Não encontrei horários disponíveis para os próximos dias. "
                    "Gostaria de verificar um período diferente?")
            
            
            # Construir a resposta usando os slots organizados por data
            response_parts = ["Encontrei os seguintes horários disponíveis:\n"]
            
            for date, day_slots in sorted(slots["slots"].items())[:7]:  # Limita a 7 dias
                # Converter a data para datetime para formatação
                date_obj = datetime.strptime(date, "%Y-%m-%d")
                date_str = date_obj.strftime("%d/%m/%Y (%A)").replace("Monday", "Segunda-feira")\
                                                            .replace("Tuesday", "Terça-feira")\
                                                            .replace("Wednesday", "Quarta-feira")\
                                                            .replace("Thursday", "Quinta-feira")\
                                                            .replace("Friday", "Sexta-feira")\
                                                            .replace("Saturday", "Sábado")\
                                                            .replace("Sunday", "Domingo")
                
                response_parts.append(f"\n*{date_str}*")
                
                # Limitar a 5 slots por dia para não sobrecarregar
                for slot in day_slots:
                    slot_time = datetime.fromisoformat(slot["time"].replace('Z', '+00:00'))
                    local_time = slot_time.astimezone(self._tz)
                    # Usar duração padrão já que não vem da API
                    response_parts.append(f"- {local_time.strftime('%H:%M')}")
            
            response_parts.append("\nVocê gostaria de agendar em algum desses horários?")
            return "\n".join(response_parts)
                
        except CalendarServiceError as e:
            logger.error(f"Erro ao verificar disponibilidade: {e}")
            return ("Desculpe, estou com dificuldades para verificar os horários disponíveis no momento. "
                "Pode tentar novamente em alguns instantes?")
                
        except Exception as e:
            logger.error(f"Erro inesperado ao verificar disponibilidade: {e}")
            return "Desculpe, ocorreu um erro inesperado. Por favor, tente novamente."


class AsyncCalendarScheduleTool(BaseCalendarTool):
    name: str = "calendar_schedule"
    description: str = "Agendar uma reunião no calendário"
    
    async def _arun(self, *args, **kwargs) -> str:
        """Agenda uma nova reunião."""
        # Log para depuração da estrutura completa de argumentos
        logger.debug(f"Argumentos recebidos: args={args}, kwargs={kwargs}")
        
        # Extrair parâmetros dos argumentos (prioridade para kwargs)
        start_time = kwargs.get('start_time')
        name = kwargs.get('name')
        email = kwargs.get('email')
        phone = kwargs.get('phone')
        notes = kwargs.get('notes')
        
        # Processar estrutura aninhada em 'args'
        if 'args' in kwargs and isinstance(kwargs['args'], list) and len(kwargs['args']) > 0:
            # Verificar se temos um dicionário dentro da lista args
            if isinstance(kwargs['args'][0], dict):
                args_dict = kwargs['args'][0]  # Extrair o dicionário da lista
                start_time = start_time or args_dict.get('start_time')
                name = name or args_dict.get('name')
                email = email or args_dict.get('email')
                phone = phone or args_dict.get('phone')
                notes = notes or args_dict.get('notes')
        
        # Processar args posicionais
        if not all([start_time, name, email]) and len(args) >= 3:
            start_time = start_time or args[0]
            name = name or args[1]
            email = email or args[2]
            if len(args) >= 4:
                phone = phone or args[3]
            if len(args) >= 5:
                notes = notes or args[4]
        
        logger.debug(f"Parâmetros extraídos: start_time={start_time}, name={name}, email={email}, phone={phone}")
        
        try:
            # Verificar se os dados parecem ser valores padrão/genéricos
            if name.lower() in ['cliente', 'customer', 'user', 'usuário', 'lead'] or \
               email.lower() in ['cliente@dominio.com', 'email@dominio.com', 'user@email.com']:
                return ("Para agendar a reunião, preciso de dados específicos do cliente. "
                        "Por favor, primeiro pergunte o nome completo e o email.")

            # Validar parâmetros obrigatórios
            if not all([start_time, name, email]):
                missing = []
                if not start_time: missing.append("data e hora")
                if not name: missing.append("nome")
                if not email: missing.append("email")
                return f"Para agendar, preciso dos seguintes dados: {', '.join(missing)}"
    
            # Validar formato do email
            if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
                return "Por favor, forneça um endereço de email válido."
    
            # Validar formato da data
            try:
                start_datetime = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                
                # Verificar ano e corrigir se necessário
                current_year = datetime.now().year
                if start_datetime.year < current_year:
                    start_time = start_time.replace(str(start_datetime.year), str(current_year))
                    start_datetime = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                    logger.info(f"Corrigindo ano para atual: {start_time}")
            except ValueError:
                return "Por favor, forneça uma data e hora válidas no formato YYYY-MM-DDTHH:MM:SS"
    
            # Agendar usando o serviço de calendário
            booking = await calendar_service.schedule_event(
                event_type_id=calendar_service.default_event_type_id,
                start_time=start_datetime,
                name=name,
                email=email,
                phone=phone,
                notes=notes
            )
            
            if not booking:
                return "Desculpe, não foi possível realizar o agendamento. Por favor, tente outro horário."
            
            # Extrair o ID do agendamento
            booking_id = booking.get("id")
            if not booking_id:
                return "O agendamento foi criado, mas não foi possível obter o ID. Por favor, verifique seu email para os detalhes."
            
            # Criar participante associado ao agendamento
            current_number = self._whatsapp_context.get("current")
            phone_to_use = phone or current_number
            
            try:
                attendee = await calendar_service.create_attendee(
                    booking_id=booking_id,
                    email=email,
                    name=name,
                    phone=phone_to_use
                )
                
                attendee_id = attendee.get("id")
                
                # Armazenar IDs no contexto do WhatsApp
                if current_number:
                    if current_number not in self._whatsapp_context:
                        self._whatsapp_context[current_number] = {}
                    
                    self._whatsapp_context[current_number]["booking_id"] = booking_id
                    self._whatsapp_context[current_number]["attendee_id"] = attendee_id
                    self._whatsapp_context[current_number]["email"] = email
                    self._whatsapp_context[current_number]["name"] = name
                    
                logger.info(f"Attendee criado com sucesso: {attendee_id}")
                
            except Exception as e:
                logger.error(f"Erro ao criar attendee: {e}")
            
            local_time = start_datetime.astimezone(self._tz)
            return (
                f"Ótimo! Sua reunião foi agendada com sucesso para "
                f"{local_time.strftime('%d/%m/%Y às %H:%M')}.\n\n"
                f"Você receberá um e-mail de confirmação em {email} "
                f"com os detalhes da reunião e o link de acesso."
            )
                
        except CalendarServiceError as e:
            logger.error(f"Erro ao agendar reunião: {e}")
            return f"Desculpe, ocorreu um erro ao agendar a reunião: {str(e)}"
        except Exception as e:
            logger.error(f"Erro inesperado ao agendar reunião: {e}")
            return "Desculpe, ocorreu um erro ao agendar a reunião. Por favor, tente novamente."


class AsyncCalendarCancelTool(BaseCalendarTool):
    name: str = "calendar_cancel"
    description: str = "Listar e cancelar reservas do cliente atual"
    
    async def _arun(self, *args, **kwargs) -> str:
        """
        Lista agendamentos do cliente ou cancela um específico.
        Se o cliente tiver apenas uma reserva, cancela automaticamente.
        
        Args:
            booking_id: ID do agendamento a ser cancelado (opcional)
            list_only: Se True, apenas lista as reservas sem cancelar
                        
        Returns:
            Mensagem com lista de reservas ou confirmação de cancelamento
        """
        booking_id = None
        list_only = True  # Por padrão, apenas listamos as reservas
        
        # Processar argumentos
        if 'booking_id' in kwargs:
            booking_id = kwargs['booking_id']
            list_only = False
        elif 'list_only' in kwargs:
            list_only = kwargs['list_only']
            
        # Processar estrutura de args aninhada
        if 'args' in kwargs and isinstance(kwargs['args'], list) and len(kwargs['args']) > 0:
            if isinstance(kwargs['args'][0], dict):
                args_dict = kwargs['args'][0]
                booking_id = booking_id or args_dict.get('booking_id')
                if 'list_only' in args_dict:
                    list_only = args_dict.get('list_only')
            elif kwargs['args'] and isinstance(kwargs['args'][0], (int, str)):
                booking_id = kwargs['args'][0]
                list_only = False
        
        # Obter contexto do cliente atual
        current_number = self._whatsapp_context.get("current")
        if not current_number or current_number not in self._whatsapp_context:
            return "Não foi possível identificar suas informações de contato. Por favor, forneça seu email para verificarmos seus agendamentos."
        
        client_context = self._whatsapp_context[current_number]
        attendee_id = client_context.get("attendee_id")
        email = client_context.get("email")
        
        if not (attendee_id or email):
            return "Não encontramos seu registro em nosso sistema. Você já realizou um agendamento anteriormente?"
        
        try:
            # Buscar todas as reservas do cliente
            bookings = await calendar_service.get_attendee_bookings(attendee_id, email)
            
            if not bookings:
                return "Não encontrei nenhum agendamento ativo em seu nome. Se acredita que isto é um erro, por favor entre em contato conosco."
            
            # CASO ESPECIAL: Se houver apenas uma reserva e o cliente pediu para cancelar (list_only=True),
            # cancelar diretamente sem pedir confirmação
            if len(bookings) == 1 and list_only:
                booking_to_cancel = bookings[0]
                booking_id = booking_to_cancel.get("id")
                
                # Extrair e formatar a data/hora para informação
                start_time = datetime.fromisoformat(booking_to_cancel.get("startTime").replace('Z', '+00:00'))
                local_time = start_time.astimezone(self._tz)
                formatted_date = local_time.strftime("%d/%m/%Y às %H:%M")
                
                # Cancelar o agendamento
                success = await calendar_service.cancel_booking(booking_id)
                
                if success:
                    return f"Sua reunião agendada para {formatted_date} foi cancelada com sucesso."
                else:
                    return f"Não foi possível cancelar sua reunião de {formatted_date}. Por favor, tente novamente mais tarde."
            
            # Se tiver múltiplas reservas ou o booking_id já foi especificado, continua com o fluxo normal
            if list_only and len(bookings) > 1:
                # Formatar a lista de agendamentos
                response = "Encontrei os seguintes agendamentos em seu nome:\n\n"
                
                for idx, booking in enumerate(bookings, 1):
                    # Extrair e formatar a data/hora
                    start_time = datetime.fromisoformat(booking.get("startTime").replace('Z', '+00:00'))
                    local_time = start_time.astimezone(self._tz)
                    formatted_date = local_time.strftime("%d/%m/%Y às %H:%M")
                    
                    # Adicionar detalhes da reunião
                    response += f"{idx}. Reunião em {formatted_date}\n"
                    
                    # Guardar ID no contexto para facilitar o cancelamento
                    client_context[f"booking_id_{idx}"] = booking.get("id")
                
                response += "\nQual dessas reuniões você gostaria de cancelar? Responda com o número correspondente."
                return response
                
            # Caso específico para cancelamento quando o ID já foi especificado
            else:
                # Se booking_id for um índice ou palavra-chave
                if isinstance(booking_id, str):
                    if booking_id.lower() in ["1", "primeiro", "first"]:
                        booking_id = client_context.get("booking_id_1")
                    elif booking_id.lower() in ["2", "segundo", "second"]:
                        booking_id = client_context.get("booking_id_2")
                    elif booking_id.lower() in ["3", "terceiro", "third"]:
                        booking_id = client_context.get("booking_id_3")
                    elif booking_id.lower() in ["atual", "current", "last"]:
                        booking_id = client_context.get("booking_id")
                
                # Validar ID e converter para inteiro
                if isinstance(booking_id, str) and booking_id.isdigit():
                    booking_id = int(booking_id)
                
                if not isinstance(booking_id, (int, float)):
                    return "Por favor, informe um número válido correspondente ao agendamento que deseja cancelar."
                
                # Cancelar o agendamento
                success = await calendar_service.cancel_booking(booking_id)
                
                if success:
                    return "Seu agendamento foi cancelado com sucesso."
                else:
                    return "Não foi possível cancelar o agendamento. Verifique se o número informado está correto ou tente novamente mais tarde."
                    
        except Exception as e:
            logger.error(f"Erro durante operação de cancelamento: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return "Ocorreu um erro ao processar sua solicitação. Por favor, tente novamente mais tarde."


class AsyncCalendarRescheduleTool(BaseCalendarTool):
    name: str = "calendar_reschedule"
    description: str = "Reagendar um compromisso existente para um novo horário"
    
    async def _arun(self, *args, **kwargs) -> str:
        """
        Reagenda um agendamento para um novo horário.
        
        Args:
            booking_id: ID do agendamento (opcional se armazenado no contexto)
            new_start_time: Novo horário no formato "YYYY-MM-DD HH:MM"
            
        Returns:
            Mensagem de confirmação ou erro
        """
        # Extrair parâmetros dos argumentos
        booking_id = None
        new_start_time = None
        
        # Prioridade para kwargs
        if 'booking_id' in kwargs:
            booking_id = kwargs['booking_id']
        if 'new_start_time' in kwargs:
            new_start_time = kwargs['new_start_time']
            
        # Depois args posicionais
        if booking_id is None and len(args) > 0:
            booking_id = args[0]
        if new_start_time is None and len(args) > 1:
            new_start_time = args[1]
            
        # Por último, processar um possível 'args' passado como kwargs
        if (booking_id is None or new_start_time is None) and 'args' in kwargs:
            if isinstance(kwargs['args'], dict):
                if booking_id is None and 'booking_id' in kwargs['args']:
                    booking_id = kwargs['args']['booking_id']
                if new_start_time is None and 'new_start_time' in kwargs['args']:
                    new_start_time = kwargs['args']['new_start_time']
            elif isinstance(kwargs['args'], (list, tuple)):
                if booking_id is None and len(kwargs['args']) > 0:
                    booking_id = kwargs['args'][0]
                if new_start_time is None and len(kwargs['args']) > 1:
                    new_start_time = kwargs['args'][1]
        
        try:
            if not new_start_time:
                return "Por favor, forneça um novo horário para reagendamento."
            
            # Obter número atual do contexto
            current_number = self._whatsapp_context.get("current")
            
            # Se booking_id não foi fornecido, tentar pegar do contexto
            if not booking_id and current_number and current_number in self._whatsapp_context:
                booking_id = self._whatsapp_context[current_number].get("booking_id")
            
            if not booking_id:
                return "Não foi possível identificar qual agendamento reagendar. Por favor, tente novamente com o ID específico."
            
            # Converter string de data/hora para objeto datetime
            try:
                # Tentar formato YYYY-MM-DD HH:MM
                new_datetime = datetime.strptime(new_start_time, "%Y-%m-%d %H:%M")
            except ValueError:
                try:
                    # Tentar formato ISO
                    new_datetime = datetime.fromisoformat(new_start_time.replace('Z', '+00:00'))
                except ValueError:
                    return "Formato de data/hora inválido. Use o formato 'YYYY-MM-DD HH:MM'."
            
            # Definir timezone
            if new_datetime.tzinfo is None:
                new_datetime = new_datetime.replace(tzinfo=self._tz)
                
            # Reagendar
            result = await calendar_service.reschedule_booking(booking_id, new_datetime)
            
            # Verificar resposta
            if result:
                # Formatar hora local para exibição
                local_time = new_datetime.astimezone(self._tz)
                return f"Seu agendamento foi remarcado com sucesso para {local_time.strftime('%d/%m/%Y às %H:%M')}."
            else:
                return "Não foi possível reagendar o compromisso. Verifique se o horário está disponível e tente novamente."
                
        except CalendarServiceError as e:
            logger.error(f"Erro ao reagendar: {e}")
            return f"Não foi possível reagendar: {str(e)}"
        except Exception as e:
            logger.error(f"Erro não esperado ao reagendar: {e}")
            return "Ocorreu um erro ao tentar reagendar. Por favor, tente novamente mais tarde."

# Exportar todas as classes que serão usadas em outros módulos
__all__ = [
    'AsyncTool',
    'CalendarScheduleArgs',
    'CalendarRescheduleArgs',
    'CalendarCancelArgs',
    'BaseCalendarTool',
    'AsyncCalendarCheckTool',
    'AsyncCalendarScheduleTool',
    'AsyncCalendarCancelTool',
    'AsyncCalendarRescheduleTool',
]