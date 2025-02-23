from typing import Dict, List, Optional
import aiohttp
import logging
from datetime import datetime, timedelta, timezone
from config import CALENDAR_CONFIG

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
            endpoint = f"users/{self.username}/bookings"
            payload = {
                "eventTypeId": event_type_id,
                "start": start_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "end": (start_time + timedelta(minutes=event_type.get("length", 60))).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "name": name,
                "email": email,
                "timeZone": self.time_zone,
                "language": "pt",
                "metadata": {}
            }

            if notes:
                payload["notes"] = notes

            # Fazer a requisição de agendamento
            result = await self._make_request(
                method="POST",
                endpoint=endpoint,
                params={"apiKey": self.api_key},
                json_data=payload
            )

            return result

        except Exception as e:
            logger.error(f"Erro ao agendar evento: {e}")
            raise CalendarServiceError(f"Falha ao agendar evento: {str(e)}")

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None
    ) -> Dict:
        """
        Realiza uma requisição à API do Cal.com.
        
        Args:
            method: Método HTTP
            endpoint: Endpoint da API
            params: Parâmetros da query
            json_data: Dados JSON para POST/PUT
            
        Returns:
            Resposta da API em formato JSON
        """
        try:
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
            
            async with session.request(
                method=method,
                url=url,
                params=params,
                json=json_data
            ) as response:
                response_text = await response.text()
                
                logger.debug(f"Status da resposta: {response.status}")
                logger.debug(f"Headers da resposta: {dict(response.headers)}")
                logger.debug(f"Corpo da resposta: {response_text[:200]}...")
                
                if response.status == 401:
                    raise CalendarServiceError("Erro de autenticação. Verifique sua API key.")
                elif response.status == 404:
                    raise CalendarServiceError(f"Endpoint não encontrado: {url}")
                elif response.status >= 400:
                    raise CalendarServiceError(f"Erro na API: {response_text}")
                
                return await response.json()
                
        except aiohttp.ClientError as e:
            logger.error(f"Erro na requisição: {e}")
            raise CalendarServiceError(f"Erro na requisição: {str(e)}")
        except Exception as e:
            logger.error(f"Erro inesperado: {e}")
            raise CalendarServiceError(f"Erro inesperado: {str(e)}")

# Instância global do serviço
calendar_service = CalendarService()