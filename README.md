# Livia - Assistente Virtual Inteligente da Nerai

## Visão Geral

Livia é uma assistente virtual inteligente desenvolvida para a Nerai, empresa especializada em soluções de Inteligência Artificial. Construída com tecnologias avançadas de processamento de linguagem natural, a Livia opera através do WhatsApp para oferecer uma experiência de atendimento humanizada, qualificar leads e agendar reuniões com potenciais clientes.

O diferencial da Livia está na sua capacidade de manter conversas naturais e contextualmente relevantes, utilizando recursos como reações a mensagens e envio de figurinhas para criar uma experiência que simula a interação humana.

## Estado Atual do Projeto

**Fase atual:** Desenvolvimento e aprimoramento contínuo

O projeto encontra-se em fase funcional com as seguintes capacidades implementadas e operacionais:

- ✅ Sistema de mensagens inteligente com simulação de digitação
- ✅ Qualificação de leads através de diálogo natural
- ✅ Integração completa com WhatsApp via Evolution API
- ✅ Agendamento, consulta e cancelamento de reuniões
- ✅ Envio de figurinhas contextuais
- ✅ Reações a mensagens com emojis
- ✅ Respostas contínuas após reações/figurinhas

## Funcionalidades Principais

### 1. Processamento de Mensagens Inteligente
- Simulação de digitação humana com velocidade variável
- Divisão natural de mensagens em parágrafos
- Pausas contextuais entre mensagens

### 2. Interação Humanizada
- Envio de reações a mensagens (👍, ❤️, 😂, etc.)
- Uso contextual de figurinhas (stickers)
- Combinação natural de texto, reações e figurinhas
- Continuidade da conversa após interações visuais

### 3. Gestão de Agendamentos
- Verificação de disponibilidade de horários
- Agendamento de reuniões e demonstrações
- Cancelamento e reagendamento
- Envio automático de confirmações

### 4. Base de Conhecimento
- Informações sobre a Nerai e seus serviços
- Respostas a perguntas frequentes
- Capacidade de qualificar leads

## Arquitetura do Sistema

O sistema é construído com uma arquitetura modular que separa responsabilidades e facilita a manutenção:

### Componentes Principais

#### 1. WhatsApp Client
Responsável pela comunicação com a API do WhatsApp (Evolution API), gerenciando o envio e recebimento de mensagens, figurinhas e reações.

#### 2. Smart Message Processor
Processa as mensagens para torná-las mais naturais, aplicando:
- Cálculo dinâmico de velocidade de digitação
- Divisão inteligente de mensagens longas
- Pausas contextuais

#### 3. Agent Manager
Gerencia o agente conversacional, integrando:
- Sistema de LLM (Large Language Model)
- Conjunto de ferramentas especializadas
- Contexto da conversa

#### 4. Ferramentas Especializadas
- `CalendarTools`: Gerenciam agendamentos e consultas de disponibilidade
- `StickerTool`: Permite envio contextual de figurinhas
- `ReactionTool`: Habilita reações a mensagens específicas
- `SiteKnowledge`: Base de conhecimento sobre a empresa

#### 5. Webhook e Gerenciamento de Conversas
- `app.py`: Gerencia webhooks e rotas da aplicação
- `conversation_manager.py`: Mantém o histórico de conversas
- `message_buffer.py`: Gerencia o buffer de mensagens para processamento assíncrono

## Fluxo de Funcionamento

1. Cliente envia mensagem via WhatsApp
2. Webhook recebe a mensagem e a encaminha para processamento
3. O Buffer de Mensagens gerencia a fila de processamento
4. AgentManager processa a mensagem usando o modelo LLM e ferramentas
5. SmartMessageProcessor formata a resposta para parecer mais humana
6. WhatsAppClient envia a resposta de volta ao cliente

## Tecnologias Utilizadas

- **Backend**: Python com Flask para o servidor web
- **Processamento de Linguagem**: LangChain e OpenAI
- **Comunicação**: Evolution API para integração com WhatsApp
- **Banco de Dados**: Sistema de armazenamento para histórico de conversas
- **Agendamento**: Integração com calendário para gestão de reuniões

## Próximos Passos

O desenvolvimento está focado nos seguintes aprimoramentos:

1. **Refinamento da NLU**: Melhorar a compreensão de intenções específicas dos clientes
2. **Expansão de Capacidades**: Adicionar novas ferramentas e integrações
3. **Personalização**: Aumentar a adaptabilidade da Livia a diferentes contextos
4. **Analytics**: Implementar métricas de desempenho e eficácia da assistente
5. **Escalabilidade**: Preparar o sistema para lidar com maior volume de conversas

## Como Utilizar

A Livia está configurada para operar em ambiente de produção, conectada a um número de WhatsApp dedicado. Para interagir com ela:

1. Envie uma mensagem para o número designado da Livia
2. Espere pela resposta, que simulará um atendente humano
3. Interaja naturalmente, como faria com um atendente real

## Contribuindo

O desenvolvimento da Livia é contínuo e focado em aprimorar a experiência de conversação. Novas funcionalidades são implementadas regularmente com base no feedback dos usuários e necessidades do negócio.

---

Desenvolvido com ❤️ para a Nerai - Soluções de Inteligência Artificial 