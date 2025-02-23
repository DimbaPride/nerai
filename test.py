import asyncio
import logging
from datetime import datetime, timezone, timedelta
from services.calendar_service import calendar_service
from zoneinfo import ZoneInfo

# Configurar logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_slots():
    try:
        logger.info("=== Testando busca de slots ===")
        
        # Data de início (UTC)
        start_date = datetime(2025, 2, 23, 23, 41, 33, tzinfo=timezone.utc)
        days_ahead = 7
        
        logger.info(f"Data/Hora Atual (UTC): {start_date.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Buscando slots para os próximos {days_ahead} dias")
        
        # Buscar slots disponíveis
        result = await calendar_service.get_availability(
            start_date=start_date,
            days_ahead=days_ahead
        )
        
        if result.get("slots"):
            logger.info("\nSlots disponíveis encontrados:")
            for date, slots in sorted(result["slots"].items()):
                logger.info(f"\nData: {date}")
                for slot in slots:
                    # Converter para horário local (America/Sao_Paulo)
                    utc_time = datetime.fromisoformat(slot["time"].replace('Z', '+00:00'))
                    local_tz = ZoneInfo("America/Sao_Paulo")
                    local_time = utc_time.astimezone(local_tz)
                    
                    # Mostrar horário local
                    logger.info(f"  - {local_time.strftime('%H:%M')} "
                              f"({local_time.tzname()}) "
                              f"[Duração: {slot.get('duration', 60)} min]")
        else:
            logger.warning("Nenhum slot disponível encontrado no período.")
            
    except Exception as e:
        logger.error(f"Erro no teste: {str(e)}")
        logger.error(f"Tipo do erro: {type(e)}")
        import traceback
        logger.error(f"Stack trace:\n{traceback.format_exc()}")
    finally:
        await calendar_service._close_session()

if __name__ == "__main__":
    asyncio.run(test_slots())