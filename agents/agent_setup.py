from functools import partial, wraps
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import re
import pytz
import logging
import asyncio
from langchain.agents import Tool, AgentExecutor, create_openai_functions_agent
from langchain.prompts import PromptTemplate
from langchain_core.tools import BaseTool

from knowledge_base.site_knowledge import SiteKnowledge, KnowledgeSource
from langchain_core.tools import StructuredTool
from services.llm import llm_openai
from services.calendar_service import calendar_service, CalendarServiceError
from config import CALENDAR_CONFIG

logger = logging.getLogger(__name__)

def with_event_loop(func):
    """Decorador para garantir que existe um loop de eventos."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        try:
            # Importante: Não fechar o loop aqui
            return loop.run_until_complete(func(*args, **kwargs))
        except Exception as e:
            logger.error(f"Erro no loop de eventos: {e}")
            raise
    return wrapper

class AgentManager:
    """Manages the creation and configuration of the agent."""
    
    def __init__(self):
        self.site_knowledge = SiteKnowledge()
        self.tz = pytz.timezone('America/Sao_Paulo')
                # Garantir que temos um loop de eventos principal
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        self.tools = self._create_tools()
        self.prompt = self._create_prompt()
        self.agent = self._create_agent()
        self.executor = self._create_executor()
      
    @with_event_loop
    async def sync_calendar_check(self, days_ahead=7):
        """
        Wrapper síncrono para _handle_calendar_availability.
        
        Args:
            days_ahead (int|str): Número de dias para verificar disponibilidade. Padrão é 7 dias.
            
        Returns:
            str: Mensagem formatada com os horários disponíveis ou mensagem de erro.
        """
        logger.debug(f"Iniciando verificação de disponibilidade para {days_ahead} dias")
        try:
            result = await self._handle_calendar_availability(days_ahead)
            logger.debug("Verificação de disponibilidade concluída com sucesso")
            return result
        except Exception as e:
            logger.error(f"Erro no sync_calendar_check: {e}")
        return "Desculpe, ocorreu um erro ao verificar os horários disponíveis. Por favor, tente novamente."
    
    @with_event_loop
    async def sync_calendar_schedule(
        self,
        start_time: str,
        name: str,
        email: str,
        phone: Optional[str] = None,
        notes: Optional[str] = None
    ) -> str:
        """Wrapper síncrono para _handle_calendar_scheduling"""
        logger.debug(f"Iniciando agendamento para {name} em {start_time}")
        try:
            # Verificar se os dados parecem ser valores padrão/genéricos
            if name.lower() in ['cliente', 'customer', 'user', 'usuário', 'lead'] or \
               email.lower() in ['cliente@dominio.com', 'email@dominio.com', 'user@email.com']:
                return ("Para agendar a reunião, preciso de dados específicos do cliente. "
                        "Por favor, primeiro pergunte o nome completo e o email profissional.")

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
            except ValueError:
                return "Por favor, forneça uma data e hora válidas no formato YYYY-MM-DDTHH:MM:SS"
    
            # Usar implementação síncrona para evitar problemas com asyncio
            try:
                booking = calendar_service.schedule_event_sync(
                    event_type_id=calendar_service.default_event_type_id,
                    start_time=start_datetime,
                    name=name,
                    email=email,
                    notes=f"Telefone: {phone}\n{notes if notes else ''}"
                )
                
                if not booking:
                    return "Desculpe, não foi possível realizar o agendamento. Por favor, tente outro horário."
                
                local_time = start_datetime.astimezone(self.tz)
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
            logger.error(f"Erro no sync_calendar_schedule: {e}")
            return "Desculpe, ocorreu um erro ao agendar a reunião. Por favor, tente novamente."

    @with_event_loop
    async def sync_calendar_cancel(self, booking_id: str):
        """Wrapper síncrono para _handle_calendar_cancellation"""
        logger.debug(f"Iniciando cancelamento do agendamento {booking_id}")
        try:
            result = await self._handle_calendar_cancellation(booking_id=booking_id)
            logger.debug("Cancelamento concluído com sucesso")
            return result
        except Exception as e:
            logger.error(f"Erro no sync_calendar_cancel: {e}")
            return "Desculpe, ocorreu um erro ao cancelar a reunião."

    @with_event_loop
    async def sync_calendar_reschedule(self, booking_id: str, new_start_time: str):
        """Wrapper síncrono para _handle_calendar_reschedule"""
        logger.debug(f"Iniciando reagendamento de {booking_id} para {new_start_time}")
        try:
            result = await self._handle_calendar_reschedule(
                booking_id=booking_id,
                new_start_time=new_start_time
            )
            logger.debug("Reagendamento concluído com sucesso")
            return result
        except Exception as e:
            logger.error(f"Erro no sync_calendar_reschedule: {e}")
            return "Desculpe, ocorreu um erro ao reagendar a reunião."

    async def _handle_calendar_availability(self, days_ahead: int = 7) -> str:
        """
        Verifica disponibilidade de horários no calendário.
        """
        try:
            # Garantir que days_ahead seja um inteiro
            if isinstance(days_ahead, str):
                days_ahead = int(days_ahead)
            
            # Validar o range de dias
            if days_ahead < 1:
                days_ahead = 7
            elif days_ahead > 60:  # Limite máximo de 60 dias
                days_ahead = 60
                
            logger.debug(f"Buscando slots disponíveis para os próximos {days_ahead} dias")
            
            # ALTERAÇÃO PRINCIPAL: usar método SÍNCRONO em vez do assíncrono
            slots = calendar_service.get_availability_sync(days_ahead=days_ahead)
            
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
                    local_time = slot_time.astimezone(self.tz)
                    response_parts.append(f"- {local_time.strftime('%H:%M')} ({slot['duration']} min)")
            
            response_parts.append("\nVocê gostaria de agendar em algum desses horários?")
            return "\n".join(response_parts)
                
        except ValueError as e:
            logger.error(f"Erro ao converter days_ahead para inteiro: {e}")
            return ("Desculpe, mas preciso de um número válido de dias para verificar. "
                "Por exemplo: para ver os próximos 7 dias, use 'calendar_check 7'")
                
        except CalendarServiceError as e:
            logger.error(f"Erro ao verificar disponibilidade: {e}")
            return ("Desculpe, estou com dificuldades para verificar os horários disponíveis no momento. "
                "Pode tentar novamente em alguns instantes?")
                
        except Exception as e:
            logger.error(f"Erro inesperado ao verificar disponibilidade: {e}")
            return "Desculpe, ocorreu um erro inesperado. Por favor, tente novamente."

    async def _handle_calendar_scheduling(
        self,
        start_time: str,
        name: str,
        email: str,
        phone: Optional[str] = None,
        notes: Optional[str] = None
    ) -> str:
        """
        Agenda uma nova reunião.
        """
        try:
            # Converter o horário para UTC se necessário
            start_datetime = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            
            # Verificar se o horário ainda está disponível
            available_slots = await calendar_service.get_availability(
                start_date=start_datetime,
                days_ahead=1
            )
            
            # Verificar se o horário escolhido está nos slots disponíveis
            slot_available = False
            if available_slots.get("slots"):
                date_key = start_datetime.strftime("%Y-%m-%d")
                if date_key in available_slots["slots"]:
                    for slot in available_slots["slots"][date_key]:
                        slot_time = datetime.fromisoformat(slot["time"].replace('Z', '+00:00'))
                        if abs((slot_time - start_datetime).total_seconds()) < 60:  # Diferença de 1 minuto
                            slot_available = True
                            break
            
            if not slot_available:
                return ("Desculpe, mas este horário não está mais disponível. "
                    "Gostaria de verificar outros horários?")
            
            # Agendar o evento
            booking = await calendar_service.schedule_event(
                event_type_id=calendar_service.default_event_type_id,
                start_time=start_datetime,
                name=name,
                email=email,
                notes=f"Telefone: {phone}\n{notes if notes else ''}"
            )
            
            if not booking:
                return "Desculpe, não foi possível realizar o agendamento. Por favor, tente outro horário."
            
            local_time = start_datetime.astimezone(self.tz)
            return (
                f"Ótimo! Sua reunião foi agendada com sucesso para "
                f"{local_time.strftime('%d/%m/%Y às %H:%M')}.\n\n"
                f"Você receberá um e-mail de confirmação em {email} "
                f"com os detalhes da reunião e o link de acesso."
            )
            
        except CalendarServiceError as e:
            logger.error(f"Erro ao agendar reunião: {e}")
            return "Desculpe, ocorreu um erro ao tentar agendar a reunião. Por favor, tente novamente."

    async def _handle_calendar_cancellation(self, booking_id: str) -> str:
        """
        Cancela um agendamento existente.
        """
        try:
            success = await calendar_service.cancel_booking(booking_id)
            
            if success:
                return "Sua reunião foi cancelada com sucesso."
            return "Não foi possível cancelar a reunião. Por favor, verifique o código do agendamento."
            
        except CalendarServiceError as e:
            logger.error(f"Erro ao cancelar reunião: {e}")
            return "Desculpe, ocorreu um erro ao tentar cancelar a reunião."

    async def _handle_calendar_reschedule(
        self,
        booking_id: str,
        new_start_time: str
    ) -> str:
        """
        Reagenda um compromisso existente.
        """
        try:
            new_datetime = datetime.fromisoformat(new_start_time)
            
            booking = await calendar_service.reschedule_booking(
                booking_id=booking_id,
                new_start_time=new_datetime
            )
            
            if not booking:
                return "Não foi possível reagendar a reunião. Por favor, verifique o código do agendamento e o novo horário."
            
            local_time = new_datetime.astimezone(self.tz)
            return (
                f"Sua reunião foi reagendada com sucesso para "
                f"{local_time.strftime('%d/%m/%Y às %H:%M')}."
            )
            
        except CalendarServiceError as e:
            logger.error(f"Erro ao reagendar reunião: {e}")
            return "Desculpe, ocorreu um erro ao tentar reagendar a reunião."

    
    # Agora, substitua a ferramenta calendar_schedule no método _create_tools():
    def _create_tools(self) -> List[BaseTool]:
        """Create and return the list of tools available to the agent."""
        return [
            Tool(
                name="site_knowledge",
                func=partial(self.site_knowledge.query, source=KnowledgeSource.WEBSITE),
                description="Consulta informações específicas do site nerai.com.br. Use esta ferramenta para responder perguntas sobre a empresa e seus serviços."
            ),
            
            Tool(
                name="knowledge_search",
                func=self.site_knowledge.query,
                description="Busca em todas as bases de conhecimento disponíveis quando precisar de uma visão completa."
            ),
            
            Tool(
                name="calendar_check",
                func=self.sync_calendar_check,
                description=(
                    "Verifica horários disponíveis para agendamento nos próximos 7 dias. "
                    "Use quando o usuário quiser marcar uma reunião ou consultar disponibilidade."
                )
            ),
            
            # Substituir Tool por StructuredTool para resolver o problema de múltiplos argumentos
            StructuredTool.from_function(
                func=self.calendar_schedule_wrapper,  # Use o wrapper em vez da função direta
                name="calendar_schedule",
                description=(
                    "Agenda uma nova reunião. Requer os seguintes parâmetros:\n"
                    "- start_time: Data e hora no formato YYYY-MM-DDTHH:MM:SS\n"
                    "- name: Nome completo do cliente\n"
                    "- email: Email do cliente\n"
                    "- phone: (opcional) Será usado o número do WhatsApp automaticamente\n"
                    "- notes: (opcional) Observações adicionais"
                )
            ),
            
            Tool(
                name="calendar_cancel",
                func=self.sync_calendar_cancel,
                description=(
                    "Cancela uma reunião agendada. "
                    "Use quando o usuário quiser cancelar um agendamento existente."
                )
            ),
            
            Tool(
                name="calendar_reschedule",
                func=self.sync_calendar_reschedule,
                description=(
                    "Reagenda uma reunião existente para um novo horário. "
                    "Use quando o usuário quiser mudar o horário de um agendamento."
                )
            )
        ]

    def _create_prompt(self) -> PromptTemplate:
        """Create and configure the prompt template."""
        template = (
            "{system_prompt}\n\n"
            "Histórico da Conversa:\n{history}\n\n"
            "Solicitação Atual: {input}\n\n"
            "Histórico de Ações:\n{agent_scratchpad}\n"
        )
        prompt = PromptTemplate.from_template(template)
        return prompt.partial(system_prompt=SYSTEM_PROMPT)

    def _create_agent(self):
        """Create the OpenAI functions agent."""
        return create_openai_functions_agent(
            llm_openai,
            self.tools,
            self.prompt
        )

    def _create_executor(self) -> AgentExecutor:
        """Create the agent executor."""
        return AgentExecutor(
            agent=self.agent,
            tools=self.tools,
            verbose=True
        )

    async def initialize(self):
        """Initialize the knowledge base."""
        await self.site_knowledge.initialize()

    def calendar_schedule_wrapper(self, **kwargs):
        """
        Wrapper robusto para sync_calendar_schedule que lida com qualquer forma de entrada
        """
        logger.debug(f"calendar_schedule_wrapper chamado com: {kwargs}")
        
        # Extrair os parâmetros corretamente de qualquer fonte possível
        start_time = None
        name = None
        email = None
        phone = None
        notes = None
        
        # CASO 1: Se os parâmetros foram passados diretamente no kwargs
        if 'start_time' in kwargs:
            start_time = kwargs.get('start_time')
        elif 'date' in kwargs:  # nome alternativo do parâmetro
            start_time = kwargs.get('date')
            
        if 'name' in kwargs:
            name = kwargs.get('name')
            
        if 'email' in kwargs:
            email = kwargs.get('email')
            
        if 'phone' in kwargs:
            phone = kwargs.get('phone')
            
        if 'notes' in kwargs:
            notes = kwargs.get('notes')
        
        # CASO 2: Se os dados vieram em um dicionário dentro de 'args'
        if 'args' in kwargs and len(kwargs['args']) > 0:
            args = kwargs['args']
            if isinstance(args[0], dict):
                arg_dict = args[0]
                if start_time is None and 'start_time' in arg_dict:
                    start_time = arg_dict.get('start_time')
                if name is None and 'name' in arg_dict:
                    name = arg_dict.get('name')
                if email is None and 'email' in arg_dict:
                    email = arg_dict.get('email')
                if phone is None and 'phone' in arg_dict:
                    phone = arg_dict.get('phone')
                if notes is None and 'notes' in arg_dict:
                    notes = arg_dict.get('notes')
        
        # Verificar se os dados essenciais foram encontrados
        logger.debug(f"Parâmetros extraídos: start_time={start_time}, name={name}, email={email}, phone={phone}")
        
        if not all([start_time, name, email]):
            missing = []
            if not start_time: missing.append("data e hora")
            if not name: missing.append("nome")
            if not email: missing.append("email")
            return f"Não foi possível agendar porque faltam os seguintes dados: {', '.join(missing)}"
        
        # Agora é seguro chamar o método real
        return self.sync_calendar_schedule(
            start_time=start_time,
            name=name,
            email=email,
            phone=phone or "",
            notes=notes or ""
        )

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

3. 'calendar_schedule': Use para confirmar agendamentos"""

# Create instance of AgentManager
agent_manager = AgentManager()

# Export the instances needed by other modules
site_knowledge = agent_manager.site_knowledge
agent_executor = agent_manager.executor

# Export all required symbols
__all__ = ['agent_manager', 'site_knowledge', 'agent_executor']