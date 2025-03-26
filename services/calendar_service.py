import asyncio
import json
import aiohttp
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Union, Any
from zoneinfo import ZoneInfo
from config import CALENDAR_CONFIG
import time

# Reduzir logs das requisições HTTP
logging.getLogger('aiohttp').setLevel(logging.ERROR)

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
        self.username = "agencia-nerai"  # Username do Cal.com
        self._session = None
        
        # Headers padrão para todas as requisições
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
    
    @property
    def current_datetime(self) -> datetime:
        """
        Retorna a data e hora atual no fuso horário do Brasil.
        
        Returns:
            datetime: Data e hora atual no fuso horário America/Sao_Paulo
        """
        brasil_tz = ZoneInfo("America/Sao_Paulo")
        return datetime.now(brasil_tz)
    
    def format_date_human(self, dt: Optional[datetime] = None, format_str: str = "%d/%m/%Y") -> str:
        """
        Formata uma data para exibição ao usuário.
        Se nenhuma data for fornecida, usa a data atual.
        
        Args:
            dt: Data para formatar (opcional)
            format_str: String de formato
            
        Returns:
            String formatada com a data
        """
        if dt is None:
            dt = self.current_datetime
        return dt.strftime(format_str)
    
    # === MÉTODOS UTILITÁRIOS DE TIMEZONE ===
    
    def convert_to_utc(self, dt: datetime) -> datetime:
        """
        Converte um datetime para UTC.
        
        Args:
            dt: Objeto datetime para converter
            
        Returns:
            Objeto datetime em UTC
        """
        # Se não tem timezone, assume que é o timezone local (Brasil)
        if dt.tzinfo is None:
            brasil_tz = ZoneInfo("America/Sao_Paulo")
            dt = dt.replace(tzinfo=brasil_tz)
            
        # Converte para UTC
        return dt.astimezone(timezone.utc)
    
    def convert_to_local(self, dt: datetime) -> datetime:
        """
        Converte um datetime para o fuso horário local (Brasil).
        
        Args:
            dt: Objeto datetime para converter
            
        Returns:
            Objeto datetime no fuso horário do Brasil
        """
        # Se não tem timezone, assume que é UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
            
        # Converte para o fuso horário local
        brasil_tz = ZoneInfo("America/Sao_Paulo")
        return dt.astimezone(brasil_tz)
    
    def parse_iso_datetime(self, iso_string: str) -> datetime:
        """
        Converte uma string ISO para um objeto datetime com timezone.
        
        Args:
            iso_string: String no formato ISO (YYYY-MM-DDTHH:MM:SS.sss)
            
        Returns:
            Objeto datetime com timezone
        """
        # Tentar converter para datetime
        dt = datetime.fromisoformat(iso_string)
        
        # Se não tem timezone, assume que é o timezone local (Brasil)
        if dt.tzinfo is None:
            brasil_tz = ZoneInfo("America/Sao_Paulo")
            dt = dt.replace(tzinfo=brasil_tz)
            
        return dt
    
    def format_datetime_to_iso(self, dt: datetime) -> str:
        """
        Formata um datetime para string ISO.
        
        Args:
            dt: Objeto datetime para formatar
            
        Returns:
            String no formato ISO (YYYY-MM-DDTHH:MM:SS.000Z)
        """
        # Garantir que tem timezone
        if dt.tzinfo is None:
            brasil_tz = ZoneInfo("America/Sao_Paulo")
            dt = dt.replace(tzinfo=brasil_tz)
            
        # Formatar no padrão ISO com Z para UTC
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    def format_datetime_human(self, dt: datetime, format_str: str = "%d/%m/%Y às %H:%M") -> str:
        """
        Formata um datetime para exibição ao usuário no fuso horário local.
        
        Args:
            dt: Objeto datetime para formatar
            format_str: String de formato
            
        Returns:
            String formatada para exibição
        """
        # Converter para fuso horário local
        local_dt = self.convert_to_local(dt)
        
        # Formatar de acordo com o formato especificado
        return local_dt.strftime(format_str)
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Retorna uma sessão HTTP existente ou cria uma nova."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers)
        return self._session
    
    async def close(self):
        """Fecha a sessão HTTP quando não for mais necessária."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def _request(self, 
                    method: str, 
                    endpoint: str, 
                    params: Optional[Dict] = None, 
                    json_data: Optional[Dict] = None,
                    timeout: int = 30) -> Dict[str, Any]:
        """
        Método base para fazer requisições à API do Cal.com.
        """
        session = await self._get_session()
        
        # Remover qualquer barra inicial ou final para evitar duplicações
        base_url_clean = self.base_url.rstrip('/')
        endpoint_clean = endpoint.lstrip('/')
        
        # Construir URL completa
        url = f"{base_url_clean}/{endpoint_clean}"
        
        # Garantir que o API key está nos parâmetros
        params = params or {}
        if "apiKey" not in params:
            params["apiKey"] = self.api_key
            
        logger.debug(f"Requisição Cal.com: {method} {url}")
        
        try:
            async with session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                timeout=timeout
            ) as response:
                response_text = await response.text()
                
                if response.status >= 400:
                    logger.error(f"Erro na API ({response.status}): {response_text}")
                    raise CalendarServiceError(f"Erro na API ({response.status}): {response_text}")
                
                # Alguns endpoints retornam string vazia em caso de sucesso (ex: DELETE)
                if not response_text:
                    return {}
                    
                return await response.json()
                
        except aiohttp.ClientError as e:
            logger.error(f"Erro de conexão com Cal.com: {e}")
            raise CalendarServiceError(f"Erro de conexão: {str(e)}")
        except asyncio.TimeoutError:
            logger.error(f"Timeout na requisição para {url}")
            raise CalendarServiceError("Timeout na conexão com o servidor")
        except Exception as e:
            logger.error(f"Erro inesperado na requisição: {e}")
            raise CalendarServiceError(f"Erro inesperado: {str(e)}")
    
    # === VERIFICAÇÃO DE DISPONIBILIDADE ===
    
    async def get_availability(self, 
                            event_type_id: Optional[Union[int, str]] = None,
                            start_date: Optional[datetime] = None,
                            days_ahead: int = 7) -> Dict:
        """
        Busca horários disponíveis para agendamento usando o endpoint de slots.
        
        Args:
            event_type_id: ID do tipo de evento (opcional, usa o padrão se não fornecido)
            start_date: Data inicial (opcional, usa hoje se não fornecido)
            days_ahead: Quantidade de dias à frente para verificar
            
        Returns:
            Dicionário com slots disponíveis organizados por data
        """
        event_type_id = event_type_id or self.default_event_type_id
        
        # Se não foi fornecida uma data inicial, usar data atual
        if not start_date:
            start_date = datetime.now(timezone.utc)
            
        # Calcular data final com base na quantidade de dias
        end_date = start_date + timedelta(days=days_ahead)
        
        # Garantir que as datas têm timezone correto (UTC)
        start_date = self.convert_to_utc(start_date)
        end_date = self.convert_to_utc(end_date)
        
        # Formatar datas como strings ISO 8601 para a API
        start_time_str = self.format_datetime_to_iso(start_date)
        end_time_str = self.format_datetime_to_iso(end_date)
        
        # Parâmetros para busca de slots
        params = {
            "eventTypeId": str(event_type_id),
            "startTime": start_time_str,
            "endTime": end_time_str
        }
        
        logger.debug(f"Consultando slots de {start_time_str} até {end_time_str}")
        
        try:
            # Buscar slots disponíveis
            result = await self._request(
                "GET",
                "slots",
                params=params
            )
            
            logger.info(f"Slots disponíveis obtidos para event_type_id={event_type_id}")
            return result
            
        except Exception as e:
            logger.error(f"Erro ao buscar disponibilidade: {e}")
            # Retornar estrutura vazia em caso de erro
            return {"slots": {}}
    
    # === CRIAÇÃO DE AGENDAMENTO ===
    
    async def schedule_event(self, 
                           event_type_id: Union[int, str],
                           start_time: datetime,
                           name: str, 
                           email: str,
                           phone: Optional[str] = None,
                           notes: Optional[str] = None) -> Dict:
        """
        Agenda um evento no calendário.
        """
        event_type_id = event_type_id or self.default_event_type_id
        
        # Converter para UTC
        start_time_utc = self.convert_to_utc(start_time)
            
        # Calcular horário final (1 hora após início)
        end_time_utc = start_time_utc + timedelta(minutes=60)
        
        # Preparar payload conforme documentação da API
        payload = {
            "eventTypeId": int(event_type_id),
            "start": self.format_datetime_to_iso(start_time_utc),
            "end": self.format_datetime_to_iso(end_time_utc),
            "responses": {
                "name": name,
                "email": email,
                "location": {
                    "value": "inPerson",
                    "optionValue": ""
                }
            },
            "timeZone": CALENDAR_CONFIG.time_zone,
            "language": "pt",
            "metadata": {},
            "user": self.username,
            "users": [self.username],
            "usernameList": [self.username]
        }
        
        # Adicionar campos opcionais
        if phone:
            payload["responses"]["phone"] = phone
        if notes:
            payload["description"] = notes
        
        try:
            # Log do payload para debug
            logger.debug(f"Payload de agendamento: {json.dumps(payload, indent=2)}")
            
            # Fazer requisição para criar o agendamento
            booking = await self._request("POST", "bookings", json_data=payload)
            logger.info(f"Agendamento criado com sucesso: {booking.get('id')}")
            return booking
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Erro ao criar agendamento: {error_msg}")
            
            if "no_available_users_found_error" in error_msg:
                # Este é um problema de configuração do Cal.com - o tipo de evento 
                # não tem usuários/hosts associados ou eles não têm disponibilidade configurada
                logger.error("ERRO DE CONFIGURAÇÃO: O tipo de evento não tem usuários (hosts) associados "
                             "ou os usuários não têm disponibilidade configurada para este horário.")
                logger.error(f"Evento ID: {event_type_id}, Usuário: {self.username}, Horário: {start_time}")
                
                # Sugestões para resolver:
                logger.error("SOLUÇÃO: Acesse o painel do Cal.com e verifique:")
                logger.error("1. Se o tipo de evento existe e tem o ID correto")
                logger.error("2. Se o usuário 'agencia-nerai' está associado a este tipo de evento")
                logger.error("3. Se o usuário tem disponibilidade configurada para este horário")
            
            # Repassar o erro original para tratamento adequado
            raise CalendarServiceError(f"Falha ao criar agendamento: {error_msg}")
    
    # === GERENCIAMENTO DE PARTICIPANTES ===
    
    async def create_attendee(self,
                            booking_id: Union[int, str],
                            email: str,
                            name: str,
                            phone: Optional[str] = None) -> Dict:
        """
        Cria um novo participante associado a um agendamento existente.
        
        Args:
            booking_id: ID do agendamento
            email: Email do participante
            name: Nome do participante
            phone: Telefone do participante (opcional)
            
        Returns:
            Dados do participante criado
        """
        try:
            payload = {
                "bookingId": booking_id,
                "email": email,
                "name": name,
                "timeZone": self.time_zone
            }
            
            # Adicionar telefone se fornecido
            if phone:
                payload["phone"] = self._format_phone_number(phone)
                
            # Enviar requisição para criar participante
            result = await self._request(
                "POST",
                "attendees",
                json_data=payload
            )
            
            logger.info(f"Participante {name} criado para o agendamento {booking_id}")
            return result.get("attendee", {})
            
        except Exception as e:
            logger.error(f"Erro ao criar participante: {e}")
            raise CalendarServiceError(f"Falha ao criar participante: {str(e)}")
    
    async def get_attendee(self, attendee_id: Union[int, str]) -> Dict:
        """
        Busca informações de um participante pelo ID.
        
        Args:
            attendee_id: ID do participante
            
        Returns:
            Dados do participante
        """
        try:
            result = await self._request(
                "GET", 
                f"attendees/{attendee_id}"
            )
            
            return result.get("attendee", {})
            
        except Exception as e:
            logger.error(f"Erro ao buscar participante {attendee_id}: {e}")
            raise CalendarServiceError(f"Falha ao buscar participante: {str(e)}")
    
    async def get_attendee_bookings(self, attendee_id: Union[int, str], 
                              email: Optional[str] = None) -> List[Dict]:
        """
        Obtém todas as reservas de um determinado participante.
        Pode usar o attendee_id ou email para buscar.
        
        Args:
            attendee_id: ID do participante
            email: Email do participante (alternativa ao ID)
            
        Returns:
            Lista de agendamentos do participante
        """
        try:
            # Buscar usando attendee_id se disponível
            if attendee_id:
                # Endpoint para buscar bookings por attendee_id
                result = await self._request("GET", f"attendees/{attendee_id}/bookings")
                return result.get("bookings", [])
                
            # Alternativa: buscar usando email
            elif email:
                # Buscar todos os agendamentos pendentes/confirmados
                all_bookings = await self._request("GET", "bookings", 
                                                  params={"status": "upcoming"})
                
                # Filtrar aqueles que correspondem ao email
                if "bookings" in all_bookings:
                    return [
                        booking for booking in all_bookings["bookings"]
                        if any(att.get("email") == email for att in booking.get("attendees", []))
                    ]
                
            return []
        except Exception as e:
            logger.error(f"Erro ao buscar agendamentos do participante: {e}")
            return []
    
    # === GERENCIAMENTO DE AGENDAMENTOS ===
    
    async def get_booking(self, booking_id: Union[int, str]) -> Dict:
        """
        Busca informações detalhadas de um agendamento.
        
        Args:
            booking_id: ID do agendamento
            
        Returns:
            Detalhes completos do agendamento
        """
        try:
            result = await self._request(
                "GET", 
                f"bookings/{booking_id}"
            )
            
            return result.get("booking", {})
            
        except Exception as e:
            logger.error(f"Erro ao buscar agendamento {booking_id}: {e}")
            raise CalendarServiceError(f"Falha ao buscar agendamento: {str(e)}")
    
    async def cancel_booking(self, booking_id: Union[int, str]) -> bool:
        """
        Cancela um agendamento existente.
        
        Args:
            booking_id: ID do agendamento a cancelar
            
        Returns:
            True se cancelado com sucesso, False caso contrário
        """
        try:
            # Garantir que booking_id seja um inteiro
            if isinstance(booking_id, str):
                if booking_id.isdigit():
                    booking_id = int(booking_id)
                else:
                    logger.error(f"ID de agendamento inválido: {booking_id}")
                    return False
            
            # Log para debug
            logger.info(f"Cancelando agendamento: {booking_id}")
            
            # Fazer requisição para cancelar
            result = await self._request(
                "DELETE", 
                f"bookings/{booking_id}/cancel",
                json_data={
                    "cancellationReason": "Cancelado pelo cliente via WhatsApp"
                }
            )
            
            logger.info(f"Agendamento {booking_id} cancelado com sucesso")
            return True
        except Exception as e:
            logger.error(f"Erro ao cancelar agendamento {booking_id}: {e}")
            return False
    
    async def reschedule_booking(self, 
                               booking_id: Union[int, str], 
                               new_start_time: datetime) -> Dict:
        """
        Reagenda um agendamento existente para um novo horário.
        
        Args:
            booking_id: ID do agendamento
            new_start_time: Novo horário inicial
            
        Returns:
            Detalhes do agendamento atualizado
        """
        try:
            # Buscar informações do agendamento atual para manter consistência
            booking = await self.get_booking(booking_id)
            
            # Garantir que o horário está no fuso horário local
            if new_start_time.tzinfo is None:
                brasil_tz = ZoneInfo("America/Sao_Paulo")
                new_start_time = new_start_time.replace(tzinfo=brasil_tz)
            
            # Log do horário local
            logger.info(f"Horário local para reagendamento: {new_start_time}")
            
            # Converter para UTC
            new_start_time_utc = self.convert_to_utc(new_start_time)
            logger.info(f"Horário UTC para reagendamento: {new_start_time_utc}")
            
            # Determinar a duração do agendamento original
            duration_minutes = 60  # Valor padrão
            if booking and "startTime" in booking and "endTime" in booking:
                start = self.parse_iso_datetime(booking["startTime"])
                end = self.parse_iso_datetime(booking["endTime"])
                duration_minutes = int((end - start).total_seconds() / 60)
            
            # Calcular novo horário final em UTC
            new_end_time_utc = new_start_time_utc + timedelta(minutes=duration_minutes)
            
            # Preparar payload para reagendamento
            payload = {
                "startTime": self.format_datetime_to_iso(new_start_time_utc),
                "endTime": self.format_datetime_to_iso(new_end_time_utc)
            }
            
            # Log do payload
            logger.info(f"Payload de reagendamento: {payload}")
            
            # Enviar requisição de reagendamento
            result = await self._request(
                "PATCH",
                f"bookings/{booking_id}",
                json_data=payload
            )
            
            # Log do horário local para verificação
            logger.info(f"Agendamento {booking_id} reagendado para {new_start_time}")
            return result
            
        except Exception as e:
            logger.error(f"Erro ao reagendar agendamento {booking_id}: {e}")
            raise CalendarServiceError(f"Falha ao reagendar: {str(e)}")
    
    
    def format_availability_response(self, slots_data: Dict, tz_name: str = "America/Sao_Paulo") -> str:
        """
        Formata os slots disponíveis em uma mensagem amigável para o usuário.
        
        Args:
            slots_data: Dados de disponibilidade retornados por get_availability
            tz_name: Nome do fuso horário para exibição
            
        Returns:
            Mensagem formatada com horários disponíveis
        """
        if not slots_data.get("slots"):
            return "Não há horários disponíveis no período solicitado."
        
        tz = ZoneInfo(tz_name)
        message_parts = ["Horários disponíveis:"]
        
        weekday_translation = {
            "Monday": "Segunda-feira",
            "Tuesday": "Terça-feira",
            "Wednesday": "Quarta-feira",
            "Thursday": "Quinta-feira",
            "Friday": "Sexta-feira",
            "Saturday": "Sábado",
            "Sunday": "Domingo"
        }
        
        for date, slots in sorted(slots_data["slots"].items()):
            # Converter data para formato local
            date_obj = datetime.strptime(date, "%Y-%m-%d")
            weekday = date_obj.strftime("%A")
            local_date = f"{date_obj.strftime('%d/%m/%Y')} ({weekday_translation.get(weekday, weekday)})"
            
            message_parts.append(f"\n📅 {local_date}")
            
            for slot in slots:
                # Converter horário para local
                slot_time = slot.get("time", "")
                if slot_time:
                    slot_time_obj = self.parse_iso_datetime(slot_time)
                    local_time = self.convert_to_local(slot_time_obj)
                    
                    message_parts.append(f"⏰ {local_time.strftime('%H:%M')}")
        
        return "\n".join(message_parts)

# Instância global do serviço
calendar_service = CalendarService()