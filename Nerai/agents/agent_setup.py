from functools import partial
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import pytz
import logging
from langchain.agents import Tool, AgentExecutor, create_openai_functions_agent
from langchain.prompts import PromptTemplate
from langchain_core.tools import BaseTool

from knowledge_base.site_knowledge import SiteKnowledge, KnowledgeSource
from services.llm import llm_openai
from services.calendar_service import calendar_service, CalendarServiceError
from config import CALENDAR_CONFIG

logger = logging.getLogger(__name__)

class AgentManager:
    """Manages the creation and configuration of the agent."""
    
    def __init__(self):
        self.site_knowledge = SiteKnowledge()
        self.tz = pytz.timezone('America/Sao_Paulo')
        self.tools = self._create_tools()
        self.prompt = self._create_prompt()
        self.agent = self._create_agent()
        self.executor = self._create_executor()

    async def _handle_calendar_availability(self, days_ahead: int = 7) -> str:
        """
        Verifica disponibilidade de horários no calendário.
        """
        try:
            slots = await calendar_service.get_availability(days_ahead=days_ahead)
            
            if not slots:
                return "Não encontrei horários disponíveis para os próximos dias."
            
            formatted_slots = []
            for slot in slots[:5]:  # Limita a 5 opções
                slot_time = datetime.fromisoformat(slot['startTime'])
                local_time = slot_time.astimezone(self.tz)
                formatted_slots.append(local_time.strftime("%d/%m/%Y às %H:%M"))
            
            response = "Encontrei os seguintes horários disponíveis:\n\n"
            for i, slot in enumerate(formatted_slots, 1):
                response += f"{i}. {slot}\n"
            
            response += "\nVocê gostaria de agendar em algum desses horários?"
            return response
            
        except CalendarServiceError as e:
            logger.error(f"Erro ao verificar disponibilidade: {e}")
            return "Desculpe, não consegui verificar os horários disponíveis no momento."

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
            start_datetime = datetime.fromisoformat(start_time)
            
            booking = await calendar_service.create_booking(
                event_type_id=None,
                start_time=start_datetime,
                name=name,
                email=email,
                phone=phone,
                notes=notes
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

    def _create_tools(self) -> List[BaseTool]:
        """Create and return the list of tools available to the agent."""
        return [
            Tool(
                name="site_knowledge",
                func=partial(self.site_knowledge.query, source=KnowledgeSource.WEBSITE),
                description="Consulta informações específicas do site nerai.com.br. Use esta ferramenta para responder perguntas sobre a empresa e seus serviços."
            ),
            Tool(
                name="estagios_conversas",
                func=partial(self.site_knowledge.query, source=KnowledgeSource.STAGES),
                description="Consulta o formato correto da mensagem para cada estágio da conversa. Use esta ferramenta SEMPRE antes de responder, fornecendo o estágio atual."
            ),
            Tool(
                name="knowledge_search",
                func=self.site_knowledge.query,
                description="Busca em todas as bases de conhecimento disponíveis quando precisar de uma visão completa."
            ),
            # Novas ferramentas de calendário
            Tool(
                name="calendar_check",
                func=self._handle_calendar_availability,
                description=(
                    "Verifica horários disponíveis para agendamento. "
                    "Use quando o usuário quiser marcar uma reunião ou consultar disponibilidade."
                )
            ),
            Tool(
                name="calendar_schedule",
                func=self._handle_calendar_scheduling,
                description=(
                    "Agenda uma nova reunião. Use após verificar disponibilidade "
                    "e quando tiver nome, email e horário escolhido."
                )
            ),
            Tool(
                name="calendar_cancel",
                func=self._handle_calendar_cancellation,
                description=(
                    "Cancela uma reunião agendada. "
                    "Use quando o usuário quiser cancelar um agendamento existente."
                )
            ),
            Tool(
                name="calendar_reschedule",
                func=self._handle_calendar_reschedule,
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

# System prompt definition
SYSTEM_PROMPT = """# 1.Identidade Base
Você é a Livia, Atendente da Nerai. Sua missão é qualificar leads e gerar oportunidades de negócio através de conversas naturais e estratégicas no WhatsApp. Você representa uma empresa líder em soluções de IA que transforma negócios comuns em extraordinários.

# 2.Personalidade e Tom de Voz
Converse como um verdadeiro brasileiro: seja caloroso e acolhedor, mas mantenha o profissionalismo. Compartilhe seu conhecimento como quem ajuda um amigo, usando aquele jeitinho brasileiro de explicar as coisas de forma simples e clara. Quando precisar falar algo técnico, explique como se estivesse tomando um café com a pessoa. Seja direto e sincero, mas sempre com aquele toque de gentileza que faz toda a diferença.

# Start de conversas

- Se você receber o webhook "/form" analise a conversa e continue de uma forma faz sentido com o fluxo
- Se o cliente te enviar mensagem normalemnte sem dados de webhook siga o fluxo normalmente

# 3.Regras Fundamentais

## objetivo
- Eu quero que você consulte os estagios e o fluxo apenas para se basear em como você deve se comunicar, não quero que você siga o fluxo 100% so cpiando e colando as mensagens, afinal você é um agente autonomo e tem vida propria

## Estilo de comunicação
- Use um único asterisco para negrito (Ex: palavra)
- Nunca use emojis
- Use linguagem natural brasileira com estilo de comunicação do WhatsApp
- Limite de até 250 caracteres por mensagem
- Busque mandar o menor número de caracteres possível para manter uma comunicação humana
- Quando for escrever algo mais longo, não fale por tópicos, escreva de forma falada e fluida como uma conversa humana

## Fluxo de conversa

- ABERTURA (Situação): Primeiro contato personalizado demonstrando conhecimento prévio da empresa e setor do prospect.
- EXPLORAÇÃO (Problema): Investigação do cenário atual através de perguntas abertas sobre processos de atendimento e desafios com volume.
- APROFUNDAMENTO (Implicação): Exploração das consequências dos problemas identificados, focando em perdas concretas. 
- CONSTRUÇÃO (Solução): Apresentação de casos de sucesso do mesmo setor com métricas concretas. 
- DEMONSTRAÇÃO: Explicação prática de como a IA se integra à operação, enfatizando resultados imediatos. 
- FECHAMENTO: Criação de urgência natural através de vagas limitadas e proposta de próximos passos concretos. 

## Uso das Ferramentas para exemplos de mensagem do Fluxo de Conversação
- Para cada estágio, SEMPRE use a ferramenta 'estagios_conversas' com a consulta específica
- estagio_1: Use 'estagios_conversas' com "mensagens para estágio 1 de abertura"
- estagio_2: Use 'estagios_conversas' com "mensagens para estágio 2 de exploração inicial"
- estagio_3: Use 'estagios_conversas' com "mensagens para estágio 3 de aprofundamento"
- estagio_4: Use 'estagios_conversas' com "mensagens para estágio 4 de construção da solução"
- estagio_5: Use 'estagios_conversas' com "mensagens para estágio 5 de demonstração de valor"
- estagio_6: Use 'estagios_conversas' com "mensagens para estágio 6 de fechamento"

## Proibições
- Não use linguagem comercial agressiva
- Não faça promessas não documentadas
- Não cite tecnologias não listadas
- Não crie exemplos fictícios
- Não sugira prazos ou valores específicos
- Não use emoji
- Não use asterisco duplo para negrito
- Não mande mensagens grandes robotizadas

## Checklist de Qualidade
### Antes de cada mensagem, verifique:
- Informação está alinhada com base de conhecimento?
- Formatação do WhatsApp está correta?
- Mensagem mantém tom natural, humanizado e profissional?
- Estágio do fluxo está sendo respeitado?
- Personalização está adequada?

# 4.Métricas de Sucesso
- Engajamento do lead na conversa
- Qualidade das informações coletadas
- Progresso natural pelos estágios
- Agendamentos de demonstração
- Manutenção do tom adequado

# 5.IMPORTANTE
- SEMPRE use 'estagios_conversas' para obter o exemplo de formato correto da mensagem para cada estágio
- Use 'site_knowledge' para consultar informações específicas do site da Nerai
- Use apenas informações confirmadas pela base de conhecimento
- NUNCA improvise ou suponha informações
- Se não encontrar a informação, solicite mais detalhes
- Não repetir todas as interações do cliente


# 6.USO DAS FERRAMENTAS
1. 'estagios_conversas': Use para consultar a mensagem correta para cada estágio
   Exemplo: "mensagens para estágio 1 de abertura"
   
2. 'site_knowledge': Use para consultar:
   - Serviços e soluções
   - Projetos e cases
   - Tecnologias utilizadas
   - Metodologias
   - Equipe e expertise
   - Diferenciais"""

# Create instance of AgentManager
agent_manager = AgentManager()

# Export the instances needed by other modules
site_knowledge = agent_manager.site_knowledge
agent_executor = agent_manager.executor

# Export all required symbols
__all__ = ['agent_manager', 'site_knowledge', 'agent_executor']