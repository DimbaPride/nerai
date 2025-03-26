from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import logging
import re
import asyncio
from zoneinfo import ZoneInfo
import json

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from services.calendar_service import calendar_service, CalendarServiceError
from config import CALENDAR_CONFIG
from services.context_manager import context_manager

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
    """Classe base para ferramentas de calendário."""
    
    def __init__(self):
        """Inicializa a ferramenta de calendário."""
        super().__init__()
    
    def _get_context_for_number(self, number: str) -> Dict[str, Any]:
        """Recupera contexto do Supabase para um número."""
        if not number:
            return {}
        return context_manager.get_context(number) or {}
    
    @property
    def _current_number(self) -> Optional[str]:
        """Retorna o número atual."""
        return context_manager.get_current_number()


class AsyncCalendarCheckTool(BaseCalendarTool):
    name: str = "calendar_check"
    description: str = "Verificar horários disponíveis no calendário para próximos dias ou data específica"
    
    async def _arun(self, *args, **kwargs) -> str:
        """
        Verifica a disponibilidade de horários.
        
        Args:
            *args: Pode ser:
                - Um número (days_ahead)
                - Uma string de data específica (e.g., "20/04")
            **kwargs: Pode conter:
                - date: String de data específica
                - days_ahead: Número de dias a verificar
        """
        try:
            # 1. Obter data atual e inicializar variáveis
            current_date = datetime.now().date()
            logger.debug(f"Data atual: {current_date}")
            days_ahead = 7  # Valor padrão
            specific_date = None  # Nova variável para data específica
            
            # 2. Extrair parâmetros dos argumentos
            # Processar kwargs primeiro (tem prioridade)
            if 'days_ahead' in kwargs:
                days_ahead = kwargs['days_ahead']
            if 'date' in kwargs:
                specific_date = kwargs['date']
                
            # Processar args se kwargs não tiver o parâmetro necessário
            elif len(args) > 0:
                # Verificar se o primeiro arg é uma possível data
                if isinstance(args[0], str) and ('/' in args[0] or '-' in args[0]):
                    specific_date = args[0]
                else:
                    # Caso contrário, considerar como days_ahead
                    days_ahead = args[0]
                    
            # Por último, processar um possível 'args' passado como kwargs
            elif 'args' in kwargs and isinstance(kwargs['args'], (list, tuple)) and len(kwargs['args']) > 0:
                if isinstance(kwargs['args'][0], str) and ('/' in kwargs['args'][0] or '-' in kwargs['args'][0]):
                    specific_date = kwargs['args'][0]
                else:
                    days_ahead = kwargs['args'][0]
            
            # 3. Corrigir ano da data se necessário
            if specific_date:
                # Se for uma string no formato DD/MM ou DD/MM/YYYY
                if isinstance(specific_date, str) and ('/' in specific_date or '-' in specific_date):
                    try:
                        # Adicionar ano atual se não especificado
                        if '/' in specific_date and len(specific_date.split('/')) == 2:
                            specific_date = f"{specific_date}/{current_date.year}"
                        elif '-' in specific_date and len(specific_date.split('-')) == 2:
                            specific_date = f"{specific_date}-{current_date.year}"
                        
                        # Garantir que o ano seja pelo menos o atual
                        if '/' in specific_date:
                            date_parts = specific_date.split('/')
                            if len(date_parts) == 3 and int(date_parts[2]) < current_date.year:
                                date_parts[2] = str(current_date.year)
                                specific_date = '/'.join(date_parts)
                        elif '-' in specific_date:
                            date_parts = specific_date.split('-')
                            if len(date_parts) == 3 and len(date_parts[0]) == 4 and int(date_parts[0]) < current_date.year:
                                date_parts[0] = str(current_date.year)
                                specific_date = '-'.join(date_parts)
                            
                        logger.debug(f"Data específica corrigida: {specific_date}")
                    except Exception as e:
                        logger.warning(f"Não foi possível processar data: {specific_date} - {e}")
            
            logger.debug(f"Verificando disponibilidade: date={specific_date}, days_ahead={days_ahead}")
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
                
                # Processar data específica se fornecida
                start_date = None
                if specific_date:
                    try:
                        # Tentar diferentes formatos de data
                        for fmt in ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m"]:
                            try:
                                parsed_date = datetime.strptime(specific_date, fmt)
                                # Se a data não tem ano, assumir ano atual
                                if fmt == "%d/%m":
                                    current_year = datetime.now().year
                                    parsed_date = parsed_date.replace(year=current_year)
                                
                                # Definir início do dia
                                start_date = datetime.combine(parsed_date.date(), datetime.min.time())
                                
                                # Se foi especificada uma data, mostrar apenas aquele dia
                                days_ahead = 1
                                logger.info(f"Data específica detectada: {start_date.date()}, mostrando apenas este dia")
                                break
                            except ValueError:
                                continue
                        
                        # Verificar se a data não está no passado
                        if start_date and start_date.date() < datetime.now().date():
                            # Se estivermos em dezembro e a data for no início do próximo ano (sem ano especificado)
                            if fmt == "%d/%m" and datetime.now().month == 12 and parsed_date.month < 6:
                                # Ajustar para o próximo ano
                                start_date = start_date.replace(year=current_year + 1)
                            else:
                                return "Não é possível agendar para datas passadas. Por favor, escolha uma data futura."
                        
                        if not start_date:
                            return "Formato de data inválido. Por favor, use formatos como '15/03/2025' ou '15/03'."
                            
                    except Exception as e:
                        logger.error(f"Erro ao processar data: {e}")
                        return "Não foi possível processar a data fornecida. Por favor, use um formato como '15/03/2025'."
                
                logger.debug(f"Buscando slots disponíveis para {days_ahead} dias a partir de {start_date or 'hoje'}")
                
                # Usar método do serviço de calendário com os parâmetros apropriados
                slots = await calendar_service.get_availability(
                    days_ahead=days_ahead,
                    start_date=start_date
                )
                
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
                        # Usar métodos utilitários do calendar_service para processar horários
                        slot_time = calendar_service.parse_iso_datetime(slot["time"])
                        local_time = calendar_service.convert_to_local(slot_time)
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
        except Exception as e:
            logger.error(f"Erro inesperado ao verificar disponibilidade: {e}")
            return "Desculpe, ocorreu um erro inesperado. Por favor, tente novamente."

    def _process_relative_date(self, message: str) -> Optional[str]:
        """
        Processa termos relativos de data diretamente da mensagem do usuário.
        
        Args:
            message: Mensagem do usuário contendo termos temporais relativos
        
        Returns:
            String de data no formato DD/MM/YYYY ou None se nenhum termo for encontrado
        """
        today = datetime.now().date()
        
        message = message.lower()
        
        # Detecção de termos relativos comuns
        if "amanhã" in message or "amanha" in message:
            tomorrow = today + timedelta(days=1)
            return tomorrow.strftime("%d/%m/%Y")
            
        elif "hoje" in message:
            return today.strftime("%d/%m/%Y")
            
        elif "semana que vem" in message or "próxima semana" in message:
            next_week = today + timedelta(days=7)
            return next_week.strftime("%d/%m/%Y")
            
        elif "daqui" in message and "dias" in message:
            # Tentar extrair "daqui X dias"
            match = re.search(r"daqui\s+(\d+)\s+dias", message)
            if match:
                days = int(match.group(1))
                future_date = today + timedelta(days=days)
                return future_date.strftime("%d/%m/%Y")
                
        elif "daqui" in message and "semanas" in message:
            # Tentar extrair "daqui X semanas"
            match = re.search(r"daqui\s+(\d+)\s+semanas", message)
            if match:
                weeks = int(match.group(1))
                future_date = today + timedelta(days=weeks*7)
                return future_date.strftime("%d/%m/%Y")
        
        # Se não encontrar termos relativos
        return None


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
        
        # Processar estrutura aninhada em 'args' (caso mais comum nas chamadas)
        if 'args' in kwargs and isinstance(kwargs['args'], list):
            args_list = kwargs['args']
            # Se args for uma lista de argumentos posicionais
            if len(args_list) >= 1 and isinstance(args_list[0], str):
                start_time = start_time or args_list[0]
            if len(args_list) >= 2 and isinstance(args_list[1], str):
                name = name or args_list[1]
            if len(args_list) >= 3 and isinstance(args_list[2], str):
                email = email or args_list[2]
            if len(args_list) >= 4 and isinstance(args_list[3], str):
                phone = phone or args_list[3]
            
            # Se o primeiro item é um dicionário (menos comum)
            elif len(args_list) > 0 and isinstance(args_list[0], dict):
                args_dict = args_list[0]
                start_time = start_time or args_dict.get('start_time')
                name = name or args_dict.get('name')
                email = email or args_dict.get('email')
                phone = phone or args_dict.get('phone')
                notes = notes or args_dict.get('notes')
        
        # Processar args posicionais diretamente (fallback)
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
            # Validar parâmetros obrigatórios
            if not all([start_time, name, email]):
                missing = []
                if not start_time: missing.append("data e hora")
                if not name: missing.append("nome")
                if not email: missing.append("email")
                return f"Para agendar, preciso dos seguintes dados: {', '.join(missing)}"
    
            # Verificar se os dados parecem ser valores padrão/genéricos
            if name and email and (
                name.lower() in ['cliente', 'customer', 'user', 'usuário', 'lead'] or
                email.lower() in ['cliente@dominio.com', 'email@dominio.com', 'user@email.com']
            ):
                return ("Para agendar a reunião, preciso de dados específicos do cliente. "
                        "Por favor, primeiro pergunte o nome completo e o email.")
    
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
            # IMPORTANTE: Criar o participante (attendee) e associá-lo à reunião
            try:
                attendee = await calendar_service.create_attendee(
                    booking_id=booking_id,
                    email=email,
                    name=name,
                    phone=phone
                )
                attendee_id = attendee.get("id")
                logger.info(f"Participante criado com sucesso: {attendee_id}")
            except Exception as e:
                # Se falhar a criação do participante, apenas registrar o erro
                # mas continuar, pois a reunião já foi agendada
                logger.error(f"Erro ao criar participante: {e}")
                attendee_id = None
            
            # Usar os dados que já possuímos
            current_number = self._current_number
            if current_number:
                booking_data = {
                    "booking_id": booking_id,
                    "email": email,  # Usar o email fornecido na solicitação
                    "name": name,    # Usar o nome fornecido na solicitação
                    "booking_created_at": datetime.now().isoformat()
                }
                
                # Adicionar attendee_id se tivermos conseguido criar
                if attendee_id:
                    booking_data["attendee_id"] = attendee_id
                
                # Adicione logs para debug
                logger.info(f"Salvando dados de agendamento para {current_number}: {booking_data}")
                
                try:
                    # Salvar no Supabase via context_manager
                    context_manager.update_context(current_number, booking_data)

                    # Verificação para confirmar o salvamento
                    verification = context_manager.get_context(current_number)
                    logger.info(f"VERIFICAÇÃO: booking_id salvo: {verification.get('booking_id')}")
                except Exception as e:
                    logger.error(f"ERRO ao salvar dados de agendamento: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
            
            # Buscar horário da reunião no objeto booking
            start_time_str = booking.get("startTime")
            if start_time_str:
                start_datetime = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            
            local_time = start_datetime.astimezone(ZoneInfo("America/Sao_Paulo"))
            
            # Retornar dados crus com prefixo especial em vez de JSON
            return f"AGENDAMENTO_SUCESSO|{local_time.strftime('%d/%m/%Y às %H:%M')}|{email}|{booking_id}"
                
        except CalendarServiceError as e:
            logger.error(f"Erro ao agendar reunião: {e}")
            return f"AGENDAMENTO_ERRO|{str(e)}"
        except Exception as e:
            logger.error(f"Erro inesperado ao agendar reunião: {e}")
            return "AGENDAMENTO_ERRO|Ocorreu um erro ao agendar a reunião"


class AsyncCalendarCancelTool(BaseCalendarTool):
    name: str = "calendar_cancel"
    description: str = "Cancelar uma reunião agendada pelo cliente"
    
    async def _arun(self, *args, **kwargs) -> str:
        """Cancela um agendamento existente."""
        try:
            # Obter o número atual usando context_manager
            current_number = self._current_number
            
            # Verificar solicitação de listagem apenas
            list_only = kwargs.get('list_only', False)
            
            # Verificar se é uma confirmação
            confirm = kwargs.get('confirm', False)
            
            # Obter número específico de agendamento (1, 2, etc)
            booking_number = None
            
            # Verificar args para possível número de agendamento ou confirmação
            if args and len(args) > 0:
                if isinstance(args[0], bool):
                    confirm = args[0]
                elif isinstance(args[0], (int, str)):
                    try:
                        booking_number = int(args[0])
                    except (ValueError, TypeError):
                        if isinstance(args[0], str) and args[0].lower() in ["confirm", "confirmar", "true", "sim", "yes"]:
                            confirm = True
                        elif isinstance(args[0], str) and args[0].lower() in ["atual", "atual", "current"]:
                            # Se já solicitou confirmação para 'atual' anteriormente, considerar como confirmado
                            if current_number and context_manager.get_context(current_number).get("pending_cancel_atual"):
                                confirm = True
                                # Limpar flag para evitar loop
                                client_context = context_manager.get_context(current_number) or {}
                                client_context["pending_cancel_atual"] = False
                                context_manager.save_context(current_number, client_context)
                            else:
                                # Marcar que foi solicitado 'atual' para futuras chamadas
                                if current_number:
                                    client_context = context_manager.get_context(current_number) or {}
                                    client_context["pending_cancel_atual"] = True
                                    context_manager.save_context(current_number, client_context)
                        elif args[0].lower() in ["1", "primeiro", "first", "um"]:
                            booking_number = 1
                        elif args[0].lower() in ["2", "segundo", "second", "dois"]:
                            booking_number = 2
                        elif args[0].lower() in ["3", "terceiro", "third", "três"]:
                            booking_number = 3
            
            # Verificar 'args' no formato de array dentro de kwargs
            if 'args' in kwargs and isinstance(kwargs['args'], (list, tuple)) and len(kwargs['args']) > 0:
                if isinstance(kwargs['args'][0], bool):
                    confirm = kwargs['args'][0]
                elif isinstance(kwargs['args'][0], (int, str)):
                    try:
                        booking_number = int(kwargs['args'][0])
                    except (ValueError, TypeError):
                        if isinstance(kwargs['args'][0], str) and kwargs['args'][0].lower() in ["confirm", "confirmar", "true", "sim", "yes"]:
                            confirm = True
                        elif isinstance(kwargs['args'][0], str) and kwargs['args'][0].lower() in ["atual", "atual", "current"]:
                            # Se já solicitou confirmação para 'atual' anteriormente, considerar como confirmado
                            if current_number and context_manager.get_context(current_number).get("pending_cancel_atual"):
                                confirm = True
                                # Limpar flag para evitar loop
                                client_context = context_manager.get_context(current_number) or {}
                                client_context["pending_cancel_atual"] = False
                                context_manager.save_context(current_number, client_context)
                            else:
                                # Marcar que foi solicitado 'atual' para futuras chamadas
                                if current_number:
                                    client_context = context_manager.get_context(current_number) or {}
                                    client_context["pending_cancel_atual"] = True
                                    context_manager.save_context(current_number, client_context)
                        elif kwargs['args'][0].lower() in ["1", "primeiro", "first", "um"]:
                            booking_number = 1
                        elif kwargs['args'][0].lower() in ["2", "segundo", "second", "dois"]:
                            booking_number = 2
                        elif kwargs['args'][0].lower() in ["3", "terceiro", "third", "três"]:
                            booking_number = 3
            
            # Variáveis para armazenar informações
            booking_id = None
            email = None
            attendee_id = None
            
            # Inicializar contexto a partir do Supabase, se possível
            client_context = {}
            if current_number:
                client_context = context_manager.get_context(current_number)
                logger.debug(f"Contexto recuperado para {current_number}: {client_context}")
                
                # Extrair informações do contexto
                booking_id = client_context.get("booking_id")
                attendee_id = client_context.get("attendee_id")
                email = client_context.get("email")
                
            # Verificar se temos informações para prosseguir
            if not any([booking_id, attendee_id, email]) and not current_number:
                # Usamos uma abordagem alternativa para acessar reservas recentes
                try:
                    # Buscar os agendamentos mais recentes
                    recent_bookings = await calendar_service._request(
                        "GET", 
                        "bookings", 
                        params={"status": "upcoming", "limit": 5}
                    )
                    
                    bookings = recent_bookings.get("bookings", [])
                    if bookings and len(bookings) > 0:
                        # Ordenar do mais recente para o mais antigo
                        bookings = sorted(
                            bookings, 
                            key=lambda b: b.get("createdAt", ""), 
                            reverse=True
                        )
                        
                        # Usar o mais recente
                        if not booking_number or booking_number == 1:
                            selected_booking = bookings[0]
                            booking_id = selected_booking.get("id")
                            
                            # Verificar se a solicitação foi feita com 'atual' explicitamente
                            direct_atual = False
                            if args and len(args) > 0 and isinstance(args[0], str) and args[0].lower() in ["atual", "current"]:
                                direct_atual = True
                            elif 'args' in kwargs and isinstance(kwargs['args'], (list, tuple)) and len(kwargs['args']) > 0 and isinstance(kwargs['args'][0], str) and kwargs['args'][0].lower() in ["atual", "current"]:
                                direct_atual = True
                            
                            # Se é uma solicitação direta com 'atual', cancelar imediatamente sem confirmação
                            if direct_atual and not confirm:
                                # Obter informações para mostrar na mensagem
                                start_time = self._format_date_time(selected_booking.get("startTime"))
                                title = selected_booking.get("title", "Demonstração Nerai")
                                
                                # Processar o cancelamento diretamente
                                result = await calendar_service.cancel_booking(booking_id)
                                
                                if result:
                                    return f"CANCELAMENTO_SUCESSO|{start_time}|{title}"
                                else:
                                    return "CANCELAMENTO_ERRO|Não foi possível cancelar o agendamento"
                            
                            if not confirm:
                                start_time = self._format_date_time(selected_booking.get("startTime"))
                                title = selected_booking.get("title", "Demonstração Nerai")
                                return f"CANCELAMENTO_CONFIRMAR|{start_time}|{title}"
                    else:
                        return "CANCELAMENTO_ERRO|Não encontrei nenhum agendamento para cancelar"
                        
                except Exception as e:
                    logger.error(f"Erro ao buscar agendamentos recentes: {e}")
                    return "CANCELAMENTO_ERRO|Não foi possível acessar informações de agendamento"
            
            # Se temos o contexto do cliente, prosseguir com o fluxo normal
            if attendee_id or email:
                # Buscar agendamentos do cliente
                bookings = await calendar_service.get_attendee_bookings(attendee_id=attendee_id, email=email)
                
                # Verificar se há agendamentos
                if not bookings or len(bookings) == 0:
                    return "Você não tem nenhum agendamento ativo no momento."
                    
                # Se a solicitação for apenas para listar os agendamentos
                if list_only:
                    if len(bookings) == 1:
                        booking = bookings[0]
                        start_time = self._format_date_time(booking.get("startTime"))
                        title = booking.get("title", "Demonstração Nerai")
                        
                        # Salvar booking_id no contexto para uso futuro
                        if current_number:
                            client_context["booking_id"] = booking.get("id")
                            context_manager.save_context(current_number, client_context)
                        
                        booking_id = booking.get("id")  # Salvar para uso posterior
                        
                        return (f"Você tem 1 agendamento:\n"
                                f"• {title} - {start_time}\n\n"
                                f"Para cancelar, use 'calendar_cancel(confirm=True)'.")
                    else:
                        # Armazenar IDs no contexto para referência futura
                        if current_number:
                            for i, booking in enumerate(bookings, 1):
                                client_context[f"booking_id_{i}"] = booking.get("id")
                            context_manager.save_context(current_number, client_context)
                        
                        message = "Você tem os seguintes agendamentos:\n\n"
                        for i, booking in enumerate(bookings, 1):
                            start_time = self._format_date_time(booking.get("startTime"))
                            title = booking.get("title", "Demonstração Nerai")
                            message += f"{i}. {title} - {start_time}\n"
                        
                        message += "\nPara cancelar um agendamento, use 'calendar_cancel(1)' ou 'calendar_cancel(\"primeiro\")'"
                        return message
                
                # Processamento da solicitação de cancelamento
                selected_booking = None
                
                # Se foi especificado um número específico de agendamento
                if booking_number and isinstance(booking_number, int):
                    # Buscar o booking_id correspondente no contexto
                    booking_id = client_context.get(f"booking_id_{booking_number}")
                    
                    # Se não encontrou no contexto mas temos menos que 5 agendamentos, tentar pelo índice
                    if not booking_id and 0 < booking_number <= len(bookings):
                        booking_id = bookings[booking_number-1].get("id")
                
                # Se temos booking_id (seja do contexto principal ou do booking_number)
                if booking_id:
                    # Buscar o booking específico
                    for booking in bookings:
                        if booking.get("id") == booking_id:
                            selected_booking = booking
                            break
                # Se não temos booking_id e há apenas um agendamento, usar ele
                elif len(bookings) == 1:
                    selected_booking = bookings[0]
                    booking_id = selected_booking.get("id")
                    # Salvar este booking_id para futuras referências
                    if current_number:
                        client_context["booking_id"] = booking_id
                        context_manager.save_context(current_number, client_context)
                
                # Se ainda não encontramos o agendamento
                if not selected_booking:
                    if len(bookings) == 1:
                        selected_booking = bookings[0]
                        booking_id = selected_booking.get("id")
                    else:
                        return "Por favor, especifique qual agendamento deseja cancelar usando 'calendar_cancel(1)' ou liste seus agendamentos com 'calendar_cancel(list_only=True)'."
                
                # Informações do agendamento
                start_time = self._format_date_time(selected_booking.get("startTime"))
                title = selected_booking.get("title", "Demonstração Nerai")
                
                # Se não foi solicitada confirmação, mostrar detalhes e solicitar
                if not confirm:
                    # Salvar o booking_id para quando a confirmação vier
                    if current_number:
                        client_context["pending_cancel_booking_id"] = booking_id
                        context_manager.save_context(current_number, client_context)
                    
                    return f"CANCELAMENTO_CONFIRMAR|{start_time}|{title}"
                
                # Se temos confirmação, verificar se temos um booking_id pendente
                if confirm and not booking_id and current_number:
                    booking_id = client_context.get("pending_cancel_booking_id")
            
            # Se chegamos aqui e temos um booking_id, prosseguir com o cancelamento
            if booking_id:
                # Confirmar e processar o cancelamento
                result = await calendar_service.cancel_booking(booking_id)
                
                if result:
                    # Limpar dados de agendamento do contexto se tivermos o current_number
                    if current_number:
                        for key in list(client_context.keys()):
                            if "booking_id" in key:
                                del client_context[key]
                        context_manager.save_context(current_number, client_context)
                            
                    # Retornar mensagem de sucesso
                    return f"CANCELAMENTO_SUCESSO|{start_time}|{title}"
                else:
                    return "CANCELAMENTO_ERRO|Não foi possível processar o cancelamento"
            else:
                return f"CANCELAMENTO_CONFIRMAR|{start_time}|{title}"
        
        except CalendarServiceError as e:
            logger.error(f"Erro ao cancelar agendamento: {e}")
            return f"CANCELAMENTO_ERRO|{str(e)}"
        except Exception as e:
            logger.error(f"Erro inesperado ao cancelar agendamento: {e}")
            logger.error(f"Detalhes: {str(e)}")
            return "CANCELAMENTO_ERRO|Ocorreu um erro inesperado ao cancelar agendamento"
    
    def _format_date_time(self, timestamp: Optional[str]) -> str:
        """Formata uma string de data/hora ISO para um formato legível."""
        if not timestamp:
            return "Data não especificada"
        
        try:
            # Usar métodos utilitários do calendar_service
            dt = calendar_service.parse_iso_datetime(timestamp)
            return calendar_service.format_datetime_human(dt)
        except Exception as e:
            logger.error(f"Erro ao formatar data/hora: {e}")
            return "Data/hora inválida"


class AsyncCalendarRescheduleTool(BaseCalendarTool):
    name: str = "calendar_reschedule"
    description: str = "Reagendar um compromisso existente para um novo horário"
    
    def __init__(self):
        super().__init__()
        self._last_call = None
        self._last_result = None
        self._cache_timeout = 5  # segundos
        self._last_args = None  # Novo: armazenar os últimos argumentos
    
    async def _arun(self, *args, **kwargs) -> str:
        """
        Reagenda um agendamento para um novo horário.
        
        Args:
            booking_id: ID do agendamento (opcional se armazenado no contexto)
            new_start_time: Novo horário no formato "YYYY-MM-DD HH:MM"
            
        Returns:
            String com os dados de reagendamento
        """
        # Criar uma chave única para esta chamada
        current_args = {
            'args': args,
            'kwargs': kwargs
        }
        
        # Verificar se é a mesma chamada recente
        current_time = datetime.now()
        if self._last_call and self._last_result and self._last_args:
            time_diff = (current_time - self._last_call).total_seconds()
            if time_diff < self._cache_timeout and self._last_args == current_args:
                return self._last_result
        
        # Extrair parâmetros dos argumentos
        booking_id = None
        new_start_time = None
        
        try:
            # Prioridade para kwargs
            if 'booking_id' in kwargs:
                booking_id = kwargs['booking_id']
            if 'new_start_time' in kwargs:
                new_start_time = kwargs['new_start_time']
            
            # Processar estrutura aninhada em 'args' (caso mais comum nas chamadas)
            if 'args' in kwargs and isinstance(kwargs['args'], list):
                args_list = kwargs['args']
                # Se args for uma lista de argumentos posicionais
                if len(args_list) >= 1 and isinstance(args_list[0], str):
                    booking_id = booking_id or args_list[0]
                if len(args_list) >= 2 and isinstance(args_list[1], str):
                    new_start_time = new_start_time or args_list[1]
                    
                # Se o primeiro item é um dicionário (menos comum)
                elif len(args_list) > 0 and isinstance(args_list[0], dict):
                    args_dict = args_list[0]
                    booking_id = booking_id or args_dict.get('booking_id')
                    new_start_time = new_start_time or args_dict.get('new_start_time')
            
            # Depois args posicionais (fallback)
            if booking_id is None and len(args) > 0:
                booking_id = args[0]
            if new_start_time is None and len(args) > 1:
                new_start_time = args[1]
            
            if not new_start_time:
                return "Por favor, forneça um novo horário para reagendamento."
            
            # Obter número atual do contexto
            current_number = self._current_number

            # Verificar se booking_id é a palavra-chave "atual"
            if isinstance(booking_id, str) and booking_id.lower() in ["atual", "atual", "current"]:
                logger.info("Reagendando o agendamento mais recente")
                # Buscar os agendamentos mais recentes
                try:
                    recent_bookings = await calendar_service._request(
                        "GET", 
                        "bookings", 
                        params={"status": "upcoming", "limit": 5}
                    )
                    
                    bookings = recent_bookings.get("bookings", [])
                    if bookings and len(bookings) > 0:
                        # Ordenar do mais recente para o mais antigo
                        bookings = sorted(
                            bookings, 
                            key=lambda b: b.get("createdAt", ""), 
                            reverse=True
                        )
                        
                        # Usar o mais recente
                        selected_booking = bookings[0]
                        booking_id = selected_booking.get("id")
                        logger.info(f"Agendamento mais recente encontrado: {booking_id}")
                    else:
                        return "Não encontrei nenhum agendamento ativo para reagendar."
                except Exception as e:
                    logger.error(f"Erro ao buscar agendamentos recentes: {e}")
                    return "Não foi possível acessar informações de agendamento."

            # Se booking_id não foi fornecido, tentar pegar do contexto
            if not booking_id and current_number:
                client_context = self._get_context_for_number(current_number)
                booking_id = client_context.get("booking_id")
            
            if not booking_id:
                return "Não foi possível identificar qual agendamento reagendar. Por favor, tente novamente com o ID específico."
            
            # Converter string de data/hora para objeto datetime
            try:
                # Tentar formato ISO primeiro
                new_datetime = calendar_service.parse_iso_datetime(new_start_time)
                # Garantir que está no fuso horário local
                brasil_tz = ZoneInfo("America/Sao_Paulo")
                if new_datetime.tzinfo is None:
                    new_datetime = new_datetime.replace(tzinfo=brasil_tz)
                else:
                    # Se já tem timezone, converter para local
                    new_datetime = new_datetime.astimezone(brasil_tz)
                
                # Log para debug
                logger.info(f"Horário local após conversão: {new_datetime}")
            except ValueError:
                try:
                    # Tentar formato YYYY-MM-DD HH:MM
                    new_datetime = datetime.strptime(new_start_time, "%Y-%m-%d %H:%M")
                    # Garantir que está no fuso horário local
                    brasil_tz = ZoneInfo("America/Sao_Paulo")
                    new_datetime = new_datetime.replace(tzinfo=brasil_tz)
                    # Log para debug
                    logger.info(f"Horário local após conversão: {new_datetime}")
                except ValueError:
                    return "Formato de data/hora inválido. Use o formato 'YYYY-MM-DD HH:MM' ou 'YYYY-MM-DDTHH:MM:SS'."
                
            # Reagendar
            result = await calendar_service.reschedule_booking(booking_id, new_datetime)
            
            # Verificar resposta
            if result:
                # Retornar dados crus com prefixo especial
                response = f"REAGENDAMENTO_SUCESSO|{new_datetime.strftime('%d/%m/%Y às %H:%M')}"
            else:
                response = "REAGENDAMENTO_ERRO|Não foi possível reagendar o compromisso"
            
            # Atualizar cache
            self._last_call = current_time
            self._last_result = response
            self._last_args = current_args
            
            return response
                
        except CalendarServiceError as e:
            logger.error(f"Erro ao reagendar: {e}")
            return f"REAGENDAMENTO_ERRO|{str(e)}"
        except Exception as e:
            logger.error(f"Erro não esperado ao reagendar: {e}")
            return "REAGENDAMENTO_ERRO|Ocorreu um erro ao reagendar"

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