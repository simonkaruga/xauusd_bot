import time
from apscheduler.schedulers.blocking import BlockingScheduler
from trading_bot import TradingBot
from logger import logger
from config import Config

def main():
    bot = TradingBot()
    
    if not bot.start():
        logger.error("Failed to start bot")
        return
    
    scheduler = BlockingScheduler()
    
    # Run trading cycle every 5 minutes during trading hours
    scheduler.add_job(
        bot.run_once,
        'cron',
        hour=f'{Config.TRADING_START_HOUR}-{Config.TRADING_END_HOUR-1}',
        minute='*/5',
        day_of_week=Config.TRADING_DAYS
    )
    
    logger.info(f"Scheduler started - Trading hours: {Config.TRADING_START_HOUR}:00-{Config.TRADING_END_HOUR}:00 GMT")
    logger.info("Bot will check for signals every 5 minutes during trading session")
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received")
        bot.stop()

if __name__ == "__main__":
    main()
