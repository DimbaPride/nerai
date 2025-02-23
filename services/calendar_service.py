import logging
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from config import CALENDAR_CONFIG

logger = logging.getLogger(__name__)

class CalendarServiceError(Exception):
    """Exceção customizada para erros do serviço de calendário."""
    pass

class CalendarService:
    """Serviço para gerenciar integrações com Cal.com."""

    def __init__(self):
        """Inicializa o serviço de calendário."""
        self.api_key = CALENDAR_CONFIG.api_key
        self.base_url = CALENDAR_CONFIG.base_url
        self.default_event_type_id = CALENDAR_CONFIG.default_event_type_id
        self.time_zone = CALENDAR_CONFIG.time_zone
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Retorna uma sessão HTTP, criando uma nova se necessário.
        
        Returns:
            aiohttp.ClientSession: Sessão HTTP ativa
        """
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers)
        return self._session

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
            method: Método HTTP (GET, POST, etc)
            endpoint: Endpoint da API
            params: Parâmetros da query string
            json_data: Dados para enviar no corpo da requisição
            
        Returns:
            Dict: Resposta da API
            
        Raises:
            CalendarServiceError: Se houver erro na requisição
        """
        session = await self._get_session()
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        
        try:
            async with session.request(
                method=method,
                url=url,
                params=params,
                json=json_data
            ) as response:
                response.raise_for_status()
                return await response.json()
                
        except aiohttp.ClientError as e:
            error_msg = f"Erro na requisição {method} {url}: {str(e)}"
            logger.error(error_msg)
            raise CalendarServiceError(error_msg)

    async def get_availability(
        self,
        event_type_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        days_ahead: int = 7
    ) -> List[Dict]:
        """
        Busca horários disponíveis para agendamento.
        
        Args:
            event_type_id: ID do tipo de evento (usa o padrão se None)
            start_date: Data inicial para buscar disponibilidade (usa hoje se None)
            days_ahead: Número de dias para buscar disponibilidade
            
        Returns:
            List[Dict]: Lista de slots disponíveis
        """
        event_type_id = event_type_id or self.default_event_type_id
        start_date = start_date or datetime.now()
        end_date = start_date + timedelta(days=days_ahead)

        try:
            return await self._make_request(
                method="GET",
                endpoint="availability",
                params={
                    "eventTypeId": event_type_id,
                    "startTime": start_date.isoformat(),
                    "endTime": end_date.isoformat(),
                    "timeZone": self.time_zone
                }
            )
        except Exception as e:
            logger.error(f"Erro ao buscar disponibilidade: {e}")
            return []

    async def create_booking(
        self,
        event_type_id: Optional[int],
        start_time: datetime,
        name: str,
        email: str,
        notes: Optional[str] = None,
        phone: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Cria um novo agendamento.
        
        Args:
            event_type_id: ID do tipo de evento
            start_time: Horário inicial do agendamento
            name: Nome do participante
            email: Email do participante
            notes: Notas adicionais
            phone: Número de telefone
            
        Returns:
            Optional[Dict]: Detalhes do agendamento ou None se falhar
        """
        event_type_id = event_type_id or self.default_event_type_id
        
        try:
            payload = {
                "eventTypeId": event_type_id,
                "start": start_time.isoformat(),
                "end": (start_time + timedelta(minutes=CALENDAR_CONFIG.default_duration)).isoformat(),
                "name": name,
                "email": email,
                "timeZone": self.time_zone,
            }
            
            if notes:
                payload["notes"] = notes
            if phone:
                payload["phone"] = phone

            return await self._make_request(
                method="POST",
                endpoint="bookings",
                json_data=payload
            )
            
        except Exception as e:
            logger.error(f"Erro ao criar agendamento: {e}")
            return None

    async def cancel_booking(
        self,
        booking_id: str,
        reason: Optional[str] = None
    ) -> bool:
        """
        Cancela um agendamento existente.
        
        Args:
            booking_id: ID do agendamento
            reason: Motivo do cancelamento
            
        Returns:
            bool: True se cancelado com sucesso
        """
        try:
            payload = {"reason": reason} if reason else {}
            
            await self._make_request(
                method="POST",
                endpoint=f"bookings/{booking_id}/cancel",
                json_data=payload
            )
            return True
            
        except Exception as e:
            logger.error(f"Erro ao cancelar agendamento {booking_id}: {e}")
            return False

    async def reschedule_booking(
        self,
        booking_id: str,
        new_start_time: datetime
    ) -> Optional[Dict]:
        """
        Reagenda um compromisso existente.
        
        Args:
            booking_id: ID do agendamento
            new_start_time: Novo horário do compromisso
            
        Returns:
            Optional[Dict]: Detalhes do agendamento atualizado ou None se falhar
        """
        try:
            payload = {
                "start": new_start_time.isoformat(),
                "end": (new_start_time + timedelta(minutes=CALENDAR_CONFIG.default_duration)).isoformat(),
                "timeZone": self.time_zone
            }
            
            return await self._make_request(
                method="PATCH",
                endpoint=f"bookings/{booking_id}/reschedule",
                json_data=payload
            )
            
        except Exception as e:
            logger.error(f"Erro ao reagendar compromisso {booking_id}: {e}")
            return None

    async def get_booking(self, booking_id: str) -> Optional[Dict]:
        """
        Busca detalhes de um agendamento específico.
        
        Args:
            booking_id: ID do agendamento
            
        Returns:
            Optional[Dict]: Detalhes do agendamento ou None se não encontrado
        """
        try:
            return await self._make_request(
                method="GET",
                endpoint=f"bookings/{booking_id}"
            )
            
        except Exception as e:
            logger.error(f"Erro ao buscar agendamento {booking_id}: {e}")
            return None

    async def close(self):
        """Fecha a sessão HTTP."""
        if self._session and not self._session.closed:
            await self._session.close()

# Instância global do serviço
calendar_service = CalendarService()