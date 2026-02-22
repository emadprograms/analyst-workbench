import logging
import sys

class AppLogger:
    """
    A standard logger for the application that replaces the Streamlit-based logger.
    Logs to both the standard logger and prints to stdout/stderr.
    """
    def __init__(self, logger_name="analyst_workbench"):
        self.logger = logging.getLogger(logger_name)
        # Disable propagation
        self.logger.propagate = False
        
        # Captured logs for Discord reporting
        self.logs = []
        
        # Clear any existing handlers to avoid duplicates
        if self.logger.handlers:
            for handler in self.logger.handlers[:]:
                self.logger.removeHandler(handler)
        
        # Configure a single handler
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter('%(levelname)s: %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

        # Also silence the root logger if it was accidentally initialized elsewhere
        root_logger = logging.getLogger()
        if not root_logger.handlers:
            # Prevent direct printing from root if not configured
            root_logger.addHandler(logging.NullHandler())
        else:
            # If root has handlers, we might still see duplicates if other libs use root
            # but setting propagate=False on our logger should handle most cases.
            pass

    def log(self, message: str):
        """Logs an info message and captures it."""
        self.logger.info(message)
        self.logs.append(f"INFO: {message}")

    def error(self, message: str):
        """Logs an error message and captures it."""
        self.logger.error(message)
        self.logs.append(f"ERROR: {message}")

    def warning(self, message: str):
        """Logs a warning message and captures it."""
        self.logger.warning(message)
        self.logs.append(f"WARNING: {message}")

    def log_code(self, code: str, language: str = 'text'):
        """Logs a code block and captures it."""
        self.logger.info(f"--- {language.upper()} BLOCK ---")
        self.logs.append(f"--- {language.upper()} BLOCK ---")
        for line in code.splitlines():
            self.logger.info(line)
            self.logs.append(line)

    def get_full_log(self) -> str:
        """Returns the full history of captured logs as a single string."""
        return "\n".join(self.logs)
