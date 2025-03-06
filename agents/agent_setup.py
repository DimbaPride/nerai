from functools import partial
from typing import Dict, List, Optional, Type, Any
from datetime import datetime
import traceback

import re
from zoneinfo import ZoneInfo
import pytz
import logging
import json
import asyncio
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain.tools import BaseTool, Tool
from langchain_community.tools import StructuredTool
from langchain.prompts import PromptTemplate
from langchain.agents.agent_types import AgentType

from knowledge_base.site_knowledge import SiteKnowledge, KnowledgeSource
from services.llm import llm_openai
from services.calendar_service import calendar_service, CalendarServiceError
from config import CALENDAR_CONFIG
from pydantic import BaseModel, Field

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
# Classes personalizadas para ferramentas assíncronas com tratamento flexível de argumentos
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


class AgentManager:
    """Gerencia a criação e configuração do agente."""
    
    def __init__(self):
        self.site_knowledge = SiteKnowledge()
        self.tz = pytz.timezone('America/Sao_Paulo')
        self.whatsapp_context = {}  # Dicionário para armazenar contexto por número WhatsApp

        # Inicializar componentes do agente
        self.tools = self._create_tools()
        self.prompt = self._create_prompt()
        self.agent = self._create_agent()
        self.executor = self._create_executor()
    
    def set_whatsapp_number(self, number):
        """Define o número de WhatsApp do contexto atual"""
        if number:
            logger.debug(f"Definindo número de WhatsApp atual: {number}")
            if number not in self.whatsapp_context:
                self.whatsapp_context[number] = {}
            self.whatsapp_context["current"] = number
    
    async def get_user_context(self, email=None, whatsapp_number=None):
        """
        Busca informações contextuais do usuário baseado no email ou número de WhatsApp.
        """
        context = {}
        
        # Primeiro verificar contexto local
        if whatsapp_number and whatsapp_number in self.whatsapp_context:
            context.update(self.whatsapp_context[whatsapp_number])
            
            # Se já temos attendee_id, buscar informações atualizadas
            if context.get("attendee_id"):
                try:
                    attendee = await calendar_service.get_attendee(context["attendee_id"])
                    if attendee:
                        context["name"] = attendee.get("name")
                        context["email"] = attendee.get("email")
                        return context
                except Exception as e:
                    logger.error(f"Erro ao buscar attendee: {e}")
        
        return context

    def _create_tools(self) -> List[BaseTool]:
        """Cria e retorna a lista de ferramentas disponíveis para o agente."""
        # Ferramentas síncronas
        sync_tools = [
            Tool(
                name="site_knowledge",
                func=partial(self.site_knowledge.query, source=KnowledgeSource.WEBSITE),
                description="Consulta informações específicas do site nerai.com.br. Use esta ferramenta para responder perguntas sobre a empresa e seus serviços."
            ),
            
            Tool(
                name="knowledge_search",
                func=self.site_knowledge.query,
                description="Busca em todas as bases de conhecimento disponíveis quando precisar de uma visão completa."
            )
        ]
        
        # Ferramentas assíncronas
        async_tools = [
            AsyncCalendarCheckTool(self.whatsapp_context),
            AsyncCalendarScheduleTool(self.whatsapp_context),
            AsyncCalendarCancelTool(self.whatsapp_context),
            AsyncCalendarRescheduleTool(self.whatsapp_context)
        ]
        
        # Combinar todas as ferramentas
        return sync_tools + async_tools

    def _create_prompt(self) -> PromptTemplate:
        """Cria e configura o template de prompt."""
        template = (
            "{system_prompt}\n\n"
            "Histórico da Conversa:\n{history}\n\n"
            "Solicitação Atual: {input}\n\n"
            "Histórico de Ações:\n{agent_scratchpad}\n"
        )
        prompt = PromptTemplate.from_template(template)
        return prompt.partial(system_prompt=SYSTEM_PROMPT)

    def _create_agent(self):
        """Cria o agente OpenAI functions."""
        return create_openai_functions_agent(
            llm_openai,
            self.tools,
            self.prompt
        )

    def _create_executor(self) -> AgentExecutor:
        """Cria o executor do agente."""
        return AgentExecutor(
            agent=self.agent,
            tools=self.tools,
            verbose=True,
            return_intermediate_steps=False,
            max_iterations=10,
            handle_tool_error=True,
            agent_kwargs={
                "extra_prompt_messages": []
            }
        )

    async def initialize(self):
        """Inicializa a base de conhecimento."""
        await self.site_knowledge.initialize()
        
    async def run(self, user_message, history=None):
        """
        Executa o agente de forma assíncrona.
        
        Args:
            user_message: Mensagem do usuário
            history: Histórico da conversa
            
        Returns:
            Resposta do agente
        """
        history = history or []
        logger.debug(f"Executando agente com mensagem: {user_message}")
        
        try:
            # Usar versão assíncrona do executor
            result = await self.executor.ainvoke(
                {
                    "input": user_message,
                    "history": history
                }
            )
            
            # Processar o resultado para garantir que retornamos uma string
            logger.debug(f"Tipo de resultado: {type(result)}")
            
            if isinstance(result, dict) and "output" in result:
                # Formato mais comum em versões recentes do LangChain
                return result["output"]
            elif isinstance(result, dict):
                # Procurar outras chaves possíveis que contenham a resposta
                for key in ["response", "result", "answer", "content"]:
                    if key in result:
                        return result[key]
                # Se não encontrar nenhuma chave conhecida
                return str(result)
            elif isinstance(result, list) and result:
                # Se for uma lista, pegar o último elemento (geralmente a resposta final)
                if isinstance(result[-1], str):
                    return result[-1]
                elif isinstance(result[-1], dict) and "content" in result[-1]:
                    return result[-1]["content"]
                else:
                    return str(result[-1])
            elif isinstance(result, str):
                # Se já for uma string, retornar diretamente
                return result
            else:
                # Para qualquer outro tipo, converter para string
                return str(result) if result else "Não consegui processar sua solicitação."
        except Exception as e:
            logger.error(f"Erro ao executar agente: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return "Desculpe, ocorreu um erro ao processar sua mensagem. Por favor, tente novamente."


SYSTEM_PROMPT = """# 1.Identidade Base
Você é a Livia, Atendente da Nerai. Sua missão é qualificar leads e gerar oportunidades de negócio através de conversas naturais e estratégicas no WhatsApp. Você representa uma empresa líder em soluções de IA que transforma negócios comuns em extraordinários.

# 2.Personalidade e Tom de Voz
Converse como um verdadeiro brasileiro: seja caloroso e acolhedor, mas mantenha o profissionalismo. Compartilhe seu conhecimento como quem ajuda um amigo, usando aquele jeitinho brasileiro de explicar as coisas de forma simples e clara. Quando precisar falar algo técnico, explique como se estivesse tomando um café com a pessoa. Seja direto e sincero, mas sempre com aquele toque de gentileza que faz toda a diferença.

# Start de conversas

- Se você receber o webhook "/form" analise a conversa e continue de uma forma faz sentido com o fluxo
- Se o cliente te enviar mensagem normalemnte sem dados de webhook siga o fluxo normalmente

# 3.Regras Fundamentais

## Estilo de comunicação
- Use um único asterisco para negrito (Ex: *palavra*)
- Nunca use emojis
- Use linguagem natural brasileira com estilo de comunicação do WhatsApp
- Limite de até 250 caracteres por mensagem
- Busque mandar o menor número de caracteres possível para manter uma comunicação humana
- Quando for escrever algo mais longo, não fale por tópicos, escreva de forma falada e fluida como uma conversa humana

## Fluxo de conversa

- Inicie com um cumprimento personalizado, demonstrando conhecimento prévio da empresa e setor do prospect quando possível.
- Investigue o cenário atual através de perguntas abertas sobre processos de atendimento e desafios com volume.
- Explore as consequências dos problemas identificados, focando em perdas concretas.
- Apresente casos de sucesso do mesmo setor com métricas concretas.
- Explique de forma prática como a IA se integra à operação, enfatizando resultados imediatos.
- Crie urgência natural e proponha próximos passos concretos.

## Fluxo de Agendamento
Quando o cliente demonstrar interesse em agendar uma demonstração:

1. Verificação de Disponibilidade:
   - Use 'calendar_check' para buscar horários disponíveis
   - Apresente as opções de forma clara e objetiva
   - Mantenha o tom natural da conversa

2. Coleta de Informações:
   - Após o cliente escolher um horário, colete APENAS:
     - Nome completo
     - Email profissional
   - NÃO peça o telefone do cliente, será usado automaticamente o número do WhatsApp atual
   - Faça isso de forma natural, como parte da conversa

3. Confirmação do Agendamento:
   - Use 'calendar_schedule' com os dados coletados
   - Formato da data deve ser: YYYY-MM-DDTHH:MM:SS
   - Confirme os detalhes do agendamento
   - Explique os próximos passos

4. Pós-Agendamento:
   - Reforce que um email de confirmação será enviado
   - Mantenha o tom acolhedor
   - Pergunte se há mais alguma dúvida

## Uso das Ferramentas de Calendário
1. 'calendar_check': Use para verificar disponibilidade
   - Exemplo: calendar_check(7) para próximos 7 dias

2. 'calendar_schedule': Use para agendar reunião
   - Parâmetros necessários:
     - start_time: "YYYY-MM-DDTHH:MM:SS"
     - name: "Nome completo"
     - email: "email@dominio.com"
     - phone: Não é necessário fornecer, será usado automaticamente o número do WhatsApp

3. 'calendar_cancel': Use para cancelar uma reunião
   - Parâmetro necessário: booking_id (ou 'atual' para a reserva mais recente)

4. 'calendar_reschedule': Use para reagendar uma reunião
   - Parâmetros necessários:
     - booking_id (ou 'atual' para a reserva mais recente)
     - new_start_time: "YYYY-MM-DDTHH:MM:SS"

## Proibições
- Não use linguagem comercial agressiva
- Não faça promessas não documentadas
- Não cite tecnologias não listadas
- Não crie exemplos fictícios
- Não sugira prazos ou valores específicos
- Não use emoji
- Não use asterisco duplo para negrito
- Não mande mensagens grandes robotizadas
- Não agende sem confirmar todos os dados necessários
- Não confirme agendamento sem usar calendar_schedule

## Checklist de Qualidade
### Antes de cada mensagem, verifique:
- Informação está alinhada com base de conhecimento?
- Formatação do WhatsApp está correta?
- Mensagem mantém tom natural, humanizado e profissional?
- Personalização está adequada?
- Dados de agendamento estão completos e corretos?

# 4.Métricas de Sucesso
- Engajamento do lead na conversa
- Qualidade das informações coletadas
- Agendamentos de demonstração
- Manutenção do tom adequado
- Taxa de confirmação de agendamentos

# 5.IMPORTANTE
- Use 'site_knowledge' para consultar informações específicas do site da Nerai
- Use apenas informações confirmadas pela base de conhecimento
- NUNCA improvise ou suponha informações
- Se não encontrar a informação, solicite mais detalhes
- Não repetir todas as interações do cliente
- Sempre confirme os dados antes de agendar
- Sempre use as ferramentas de calendário na ordem correta

# 6.USO DAS FERRAMENTAS
1. 'site_knowledge': Use para consultar:
   - Serviços e soluções
   - Projetos e cases
   - Tecnologias utilizadas
   - Metodologias
   - Equipe e expertise
   - Diferenciais

2. 'calendar_check': Use para verificar disponibilidade de horários

3. 'calendar_schedule': Use para confirmar agendamentos

4. 'calendar_cancel': Use para cancelar agendamentos existentes

5. 'calendar_reschedule': Use para reagendar compromissos existentes"""

# Criar instância do AgentManager
agent_manager = AgentManager()

# Exportar as instâncias necessárias para outros módulos
site_knowledge = agent_manager.site_knowledge
agent_executor = agent_manager.executor

# Exportar todos os símbolos necessários
__all__ = ['agent_manager', 'site_knowledge', 'agent_executor']