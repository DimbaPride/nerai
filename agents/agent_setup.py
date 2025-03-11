from functools import partial
from typing import Dict, List, Optional, Type, Any
from datetime import datetime
import traceback
import logging
import pytz

from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain.tools import Tool, BaseTool
from langchain.prompts import PromptTemplate

from knowledge_base.site_knowledge import SiteKnowledge, KnowledgeSource
from services.llm import llm_openai
from services.calendar_service import calendar_service, CalendarServiceError

# Importar as classes de ferramentas de calendário do novo arquivo
from agents.calendar_tools import (
    AsyncCalendarCheckTool,
    AsyncCalendarScheduleTool,
    AsyncCalendarCancelTool,
    AsyncCalendarRescheduleTool
)

logger = logging.getLogger(__name__)

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