import time
from datetime import datetime, timezone
from logger import logger


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class HealthMonitor:
    def __init__(self):
        self.last_heartbeat = _utcnow()
        self.error_count = 0
        self.max_errors = 5
        self.is_healthy = True

    def heartbeat(self):
        self.last_heartbeat = _utcnow()

    def check_health(self) -> bool:
        now = _utcnow()
        time_since_heartbeat = (now - self.last_heartbeat).total_seconds()

        if time_since_heartbeat > 600:
            logger.error(f"Bot unresponsive for {time_since_heartbeat/60:.1f} minutes")
            self.is_healthy = False
            return False

        if self.error_count >= self.max_errors:
            logger.error(f"Too many consecutive errors: {self.error_count}")
            self.is_healthy = False
            return False

        # Verify MT5 terminal is connected and account is accessible
        try:
            import MetaTrader5 as mt5
            account = mt5.account_info()
            if account is None:
                logger.error(f"MT5 account_info() returned None: {mt5.last_error()}")
                self.is_healthy = False
                return False

            # Verify we have free margin to trade
            if account.margin_free <= 0:
                logger.error(f"No free margin: {account.margin_free}")
                self.is_healthy = False
                return False

            # Verify terminal is connected to broker
            terminal = mt5.terminal_info()
            if terminal is None or not terminal.connected:
                logger.error("MT5 terminal is not connected to broker")
                self.is_healthy = False
                return False

        except Exception as e:
            logger.error(f"Health check MT5 error: {e}")
            self.is_healthy = False
            return False

        self.is_healthy = True
        return True

    def record_error(self, error_msg: str):
        self.error_count += 1
        logger.error(f"Error #{self.error_count}: {error_msg}")
        if self.error_count >= self.max_errors:
            logger.critical("Max errors reached — bot needs attention")

    def reset_errors(self):
        if self.error_count > 0:
            logger.info(f"Errors cleared (was {self.error_count})")
        self.error_count = 0

    def get_status(self) -> dict:
        return {
            'healthy': self.is_healthy,
            'last_heartbeat': self.last_heartbeat.isoformat(),
            'error_count': self.error_count,
            'seconds_since_heartbeat': (_utcnow() - self.last_heartbeat).total_seconds(),
        }

    def auto_recover(self) -> bool:
        """Attempt recovery by shutting down and re-initialising MT5."""
        logger.warning("Attempting auto-recovery...")
        self.error_count = 0
        self.heartbeat()
        try:
            import MetaTrader5 as mt5
            mt5.shutdown()
            time.sleep(3)
            if mt5.initialize():
                logger.info("Auto-recovery: MT5 re-initialised successfully")
                return True
            logger.error(f"Auto-recovery: MT5 re-init failed: {mt5.last_error()}")
            return False
        except Exception as e:
            logger.error(f"Auto-recovery error: {e}")
            return False
