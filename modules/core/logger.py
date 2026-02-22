import logging
import sys

class AppLogger:
    """
    A standard logger for the application that replaces the Streamlit-based logger.
    Logs to both the standard logger and prints to stdout/stderr.
    """
    def __init__(self, logger_name="analyst_workbench"):
        self.logger = logging.getLogger(logger_name)
        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def log(self, message: str):
        """Logs an info message."""
        self.logger.info(message)
        print(f"INFO: {message}")

    def error(self, message: str):
        """Logs an error message."""
        self.logger.error(message)
        print(f"ERROR: {message}", file=sys.stderr)

    def warning(self, message: str):
        """Logs a warning message."""
        self.logger.warning(message)
        print(f"WARNING: {message}")

    def log_code(self, code: str, language: str = 'text'):
        """Logs a code block."""
        print(f"--- {language.upper()} BLOCK START ---")
        print(code)
        print(f"--- {language.upper()} BLOCK END ---")
