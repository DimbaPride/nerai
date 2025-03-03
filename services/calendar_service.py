from typing import Dict, List, Optional
import aiohttp
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from config import CALENDAR_CONFIG
import requests
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

class CalendarServiceError(Exception):
    """Exceção personalizada para erros do serviço de calendário."""
    pass

class CalendarService:
    def __init__(self):
        """
        Inicializa o serviço de calendário com as configurações do Cal.com.
        """
        self.api_key = CALENDAR_CONFIG.api_key
        self.base_url = CALENDAR_CONFIG.base_url
        self.default_event_type_id = CALENDAR_CONFIG.default_event_type_id
        self.time_zone = CALENDAR_CONFIG.time_zone
        self.username = "nerai-xt0yj1"  # Username do Cal.com
        self.request_timeout = 30.0  # Timeout padrão em segundos
        
        # Headers básicos
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Retorna uma sessão HTTP existente ou cria uma nova.
        """
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers)
        return self._session

    async def _close_session(self):
        """
        Fecha a sessão HTTP se existir.
        """
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def get_event_types(self) -> List[Dict]:
        """
        Lista todos os event-types disponíveis.
        """
        try:
            result = await self._make_request(
                method="GET",
                endpoint="event-types",
                params={"apiKey": self.api_key}
            )
            return result.get("event_types", [])
        except Exception as e:
            logger.error(f"Erro ao listar event-types: {e}")
            return []

    async def get_event_type(self, event_type_id: int) -> Optional[Dict]:
        """
        Busca informações detalhadas de um event-type específico.
        """
        try:
            result = await self._make_request(
                method="GET",
                endpoint=f"event-types/{event_type_id}",
                params={"apiKey": self.api_key}
            )
            return result.get("event_type")
        except Exception as e:
            logger.error(f"Erro ao buscar event-type {event_type_id}: {e}")
            return None

    async def get_availability(
        self,
        event_type_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        days_ahead: int = 7
    ) -> Dict[str, Dict[str, List[Dict[str, str]]]]:
        """
        Busca horários disponíveis para agendamento.
        """
        event_type_id = event_type_id or self.default_event_type_id
        start_date = start_date or datetime.now(timezone.utc)
        end_date = start_date + timedelta(days=days_ahead)

        try:
            # Primeiro buscar o event type para obter o userId e duração
            event_info = await self._make_request(
                method="GET",
                endpoint=f"event-types/{event_type_id}",
                params={"apiKey": self.api_key}
            )
            
            if not event_info or "event_type" not in event_info:
                raise CalendarServiceError("Não foi possível obter informações do evento")
            
            event_type = event_info["event_type"]
            user_id = event_type["userId"]
            duration = event_type.get("length", 60)  # Duração em minutos
            
            # Endpoint para disponibilidade
            endpoint = f"users/{user_id}/availability"
            params = {
                "apiKey": self.api_key,
                "dateFrom": start_date.strftime("%Y-%m-%d"),
                "dateTo": end_date.strftime("%Y-%m-%d"),
                "eventTypeId": str(event_type_id),
                "timezone": self.time_zone
            }
            
            logger.debug(f"Buscando disponibilidade:")
            logger.debug(f"User ID: {user_id}")
            logger.debug(f"Event Type ID: {event_type_id}")
            logger.debug(f"Data Início: {params['dateFrom']}")
            logger.debug(f"Data Fim: {params['dateTo']}")
            
            result = await self._make_request(
                method="GET",
                endpoint=endpoint,
                params=params
            )
            
            # Processar os intervalos de disponibilidade
            slots_by_date = {}
            if "dateRanges" in result:
                for date_range in result["dateRanges"]:
                    # Converter strings para datetime
                    start = datetime.fromisoformat(date_range["start"].replace('Z', '+00:00'))
                    end = datetime.fromisoformat(date_range["end"].replace('Z', '+00:00'))
                    
                    # Criar slots com a duração especificada
                    current = start
                    while current + timedelta(minutes=duration) <= end:
                        date = current.strftime("%Y-%m-%d")
                        if date not in slots_by_date:
                            slots_by_date[date] = []
                        
                        slots_by_date[date].append({
                            "time": current.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                            "duration": duration
                        })
                        
                        # Avançar para o próximo slot
                        current += timedelta(minutes=duration)
            
            if slots_by_date:
                logger.info(f"Encontrados slots em {len(slots_by_date)} dias")
            else:
                logger.warning("Nenhum slot disponível encontrado no período")
                
            return {"slots": slots_by_date}
                
        except Exception as e:
            logger.error(f"Erro ao buscar disponibilidade: {e}")
            return {"slots": {}}

    async def schedule_event(
        self,
        event_type_id: int,
        start_time: datetime,
        name: str,
        email: str,
        notes: Optional[str] = None
    ) -> Dict:
        """
        Agenda um novo evento.
        
        Args:
            event_type_id: ID do tipo de evento
            start_time: Horário inicial do evento
            name: Nome do participante
            email: Email do participante
            notes: Notas adicionais (opcional)
            
        Returns:
            Informações do evento agendado
        """
        try:
            # Buscar informações do event type
            event_type = await self.get_event_type(event_type_id)
            if not event_type:
                raise CalendarServiceError("Event type não encontrado")

            slug = event_type.get("slug")
            if not slug:
                raise CalendarServiceError("Slug não encontrado no event type")

            # Preparar dados do agendamento
            endpoint = f"bookings"  # Endpoint corrigido para bookings
            payload = {
                "eventTypeId": event_type_id,
                "start": start_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "end": (start_time + timedelta(minutes=event_type.get("length", 60))).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "responses": {
                    "name": name,
                    "email": email,
                    "notes": notes if notes else ""
                },
                "timeZone": self.time_zone,
                "language": "pt",
                "metadata": {}
            }

            # Fazer a requisição de agendamento
            result = await self._make_request(
                method="POST",
                endpoint=endpoint,
                params={"apiKey": self.api_key},
                json_data=payload
            )

            logger.info(f"Evento agendado com sucesso para {name}: {start_time}")
            return result

        except Exception as e:
            logger.error(f"Erro ao agendar evento: {e}")
            raise CalendarServiceError(f"Falha ao agendar evento: {str(e)}")

    def schedule_event_sync(
        self,
        event_type_id: int,
        start_time: datetime,
        name: str,
        email: str,
        notes: Optional[str] = None
    ) -> Dict:
        try:
            # Garantir que o horário esteja no fuso horário correto
            if start_time.tzinfo is None:
                # Se não tem timezone, assumir Brasília
                brasil_tz = ZoneInfo("America/Sao_Paulo")
                start_time = start_time.replace(tzinfo=brasil_tz)
            
            # Converter explicitamente para UTC
            start_time_utc = start_time.astimezone(timezone.utc)
            end_time_utc = start_time_utc + timedelta(minutes=60)
            
            # Log para debug de fuso horário
            logger.info(f"Horário original: {start_time}")
            logger.info(f"Horário UTC para API: {start_time_utc}")
            
            # Montar a URL com o endpoint
            url = urljoin(self.base_url, "bookings")
            
            # Parâmetros de query
            querystring = {"apiKey": self.api_key}
            
            # Headers
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            # Payload exato como na documentação
            payload = {
                "eventTypeId": event_type_id,
                "start": start_time_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "end": end_time_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "responses": {
                    "name": name,
                    "email": email,
                    "notes": notes if notes else ""
                },
                "timeZone": self.time_zone,
                "language": "pt",
                "metadata": {}
            }
            
            logger.debug(f"Enviando requisição síncrona para {url}")
            logger.debug(f"Payload: {payload}")
            
            # Fazer a requisição de forma síncrona
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                params=querystring,
                timeout=30
            )
            
            # Verificar resposta
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"Evento agendado com sucesso para {name}: {start_time}")
            return result
            
        except requests.RequestException as e:
            error_msg = f"Falha na requisição para Cal.com: {str(e)}"
            logger.error(error_msg)
            raise CalendarServiceError(error_msg)
        except Exception as e:
            error_msg = f"Erro ao agendar evento: {str(e)}"
            logger.error(error_msg)
            raise CalendarServiceError(error_msg)

    def get_availability_sync(
        self,
        event_type_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        days_ahead: int = 7
    ) -> Dict[str, Dict[str, List[Dict[str, str]]]]:
        """
        Versão 100% síncrona da função get_availability.
        """
        try:
            event_type_id = event_type_id or self.default_event_type_id
            start_date = start_date or datetime.now(timezone.utc)
            end_date = start_date + timedelta(days=days_ahead)
            
            # Obter informações do event type
            url = f"{self.base_url}/event-types/{event_type_id}"
            params = {"apiKey": self.api_key}
            
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            event_info = response.json()
            
            if not event_info or "event_type" not in event_info:
                raise CalendarServiceError("Não foi possível obter informações do evento")
            
            event_type = event_info["event_type"]
            user_id = event_type["userId"]
            duration = event_type.get("length", 60)
            
            # Buscar disponibilidade
            url = f"{self.base_url}/users/{user_id}/availability"
            params = {
                "apiKey": self.api_key,
                "dateFrom": start_date.strftime("%Y-%m-%d"),
                "dateTo": end_date.strftime("%Y-%m-%d"),
                "eventTypeId": str(event_type_id),
                "timezone": self.time_zone
            }
            
            logger.info(f"Verificando disponibilidade para {days_ahead} dias")
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            result = response.json()
            
            # Processar os intervalos de disponibilidade
            slots_by_date = {}
            if "dateRanges" in result:
                for date_range in result["dateRanges"]:
                    # Converter strings para datetime
                    start = datetime.fromisoformat(date_range["start"].replace('Z', '+00:00'))
                    end = datetime.fromisoformat(date_range["end"].replace('Z', '+00:00'))
                    
                    # Criar slots com a duração especificada
                    current = start
                    while current + timedelta(minutes=duration) <= end:
                        date = current.strftime("%Y-%m-%d")
                        if date not in slots_by_date:
                            slots_by_date[date] = []
                        
                        slots_by_date[date].append({
                            "time": current.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                            "duration": duration
                        })
                        
                        # Avançar para o próximo slot
                        current += timedelta(minutes=duration)
        
            return {"slots": slots_by_date}
            
        except Exception as e:
            logger.error(f"Erro ao buscar disponibilidade: {e}")
            return {"slots": {}}

    def format_availability_response(slots_data: Dict[str, Dict[str, List[Dict[str, str]]]], timezone: str = "America/Sao_Paulo") -> str:
        """
        Formata os slots disponíveis em uma mensagem amigável para o usuário.
        """
        if not slots_data.get("slots"):
            return "Não há horários disponíveis no período solicitado."
        
        tz = ZoneInfo(timezone)
        message_parts = ["Horários disponíveis:"]
        
        for date, slots in sorted(slots_data["slots"].items()):
            # Converter data para formato local
            date_obj = datetime.strptime(date, "%Y-%m-%d")
            local_date = date_obj.strftime("%d/%m/%Y (%A)")
            
            # Traduzir dia da semana
            local_date = local_date.replace("Monday", "Segunda-feira")\
                                .replace("Tuesday", "Terça-feira")\
                                .replace("Wednesday", "Quarta-feira")\
                                .replace("Thursday", "Quinta-feira")\
                                .replace("Friday", "Sexta-feira")\
                                .replace("Saturday", "Sábado")\
                                .replace("Sunday", "Domingo")
            
            message_parts.append(f"\n📅 {local_date}")
            
            for slot in slots:
                # Converter horário para local
                utc_time = datetime.fromisoformat(slot["time"].replace('Z', '+00:00'))
                local_time = utc_time.astimezone(tz)
                
                message_parts.append(f"⏰ {local_time.strftime('%H:%M')} "
                                f"({slot.get('duration', 60)} minutos)")
        
        return "\n".join(message_parts)        
    
    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None
    ) -> Dict:
        """
        Realiza uma requisição à API do Cal.com com timeout adequado.
        """
        session = await self._get_session()
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        
        # Garantir que temos os parâmetros básicos
        params = params or {}
        if "apiKey" not in params:
            params["apiKey"] = self.api_key
        
        logger.debug(f"Requisição Cal.com:")
        logger.debug(f"URL: {url}")
        logger.debug(f"Método: {method}")
        logger.debug(f"Parâmetros: {params}")
        
        # VERSÃO SIMPLIFICADA SEM TIMEOUT COMPLEXO
        try:
            # Criar a requisição sem timeout explícito
            response = await session.request(
                method=method,
                url=url,
                params=params,
                json=json_data
            )
            
            response_text = await response.text()
            
            logger.debug(f"Status da resposta: {response.status}")
            
            if response.status >= 400:
                logger.error(f"Erro na API: {response_text}")
                raise CalendarServiceError(f"Erro na API ({response.status}): {response_text}")
            
            return await response.json()
            
        except aiohttp.ClientError as e:
            logger.error(f"Erro na requisição: {e}")
            raise CalendarServiceError(f"Erro na requisição: {str(e)}")
        except Exception as e:
            logger.error(f"Erro inesperado: {e}")
            raise CalendarServiceError(f"Erro inesperado: {str(e)}")

# Instância global do serviço
calendar_service = CalendarService()