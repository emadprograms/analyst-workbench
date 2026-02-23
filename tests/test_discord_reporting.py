import unittest
from unittest.mock import patch, MagicMock
from modules.core.logger import AppLogger
from datetime import date
import json

# We need to import main to test the send_webhook_report function
# Using a local import inside the test to avoid potential side effects from main.py's global scope
def get_send_webhook_report():
    import main
    return main.send_webhook_report

class TestDiscordReporting(unittest.TestCase):
    def setUp(self):
        self.logger = AppLogger("test_logger")

    def test_logger_captures_logs(self):
        self.logger.log("Test log")
        self.logger.error("Test error")
        self.logger.warning("Test warning")
        self.logger.log_code("print('hello')", "python")
        
        full_log = self.logger.get_full_log()
        
        self.assertIn("INFO: Test log", full_log)
        self.assertIn("ERROR: Test error", full_log)
        self.assertIn("WARNING: Test warning", full_log)
        self.assertIn("--- PYTHON BLOCK ---", full_log)
        self.assertIn("print('hello')", full_log)

    @patch('requests.post')
    @patch('modules.ai.ai_services.TRACKER')
    def test_send_webhook_report_with_artifacts(self, mock_tracker, mock_post):
        send_webhook_report = get_send_webhook_report()
        
        target_date = date(2026, 2, 22)
        webhook_url = "http://test-webhook.com"
        
        mock_tracker.get_discord_embeds.return_value = [{"title": "Test Embed"}]
        mock_tracker.metrics.artifacts = {
            "ECONOMY_CARD": '{"marketNarrative": "Bullish"}',
            "AAPL_CARD": '{"emotionalTone": "Strong"}'
        }
        
        self.logger.log("Capture this log")
        
        send_webhook_report(webhook_url, target_date, "run", "gemini-3-flash-free", logger=self.logger)
        
        # Verify requests.post was called with multipart/form-data
        self.assertTrue(mock_post.called)
        args, kwargs = mock_post.call_args
        
        # Check files (should have 3 files: 1 log + 2 cards)
        self.assertIn('files', kwargs)
        self.assertEqual(len(kwargs['files']), 3)
        self.assertIn('file', kwargs['files'])
        self.assertIn('ECONOMY_CARD', kwargs['files'])
        self.assertIn('AAPL_CARD', kwargs['files'])
        
        filename_eco, content_eco, type_eco = kwargs['files']['ECONOMY_CARD']
        self.assertEqual(filename_eco, "ECONOMY_CARD.json")
        self.assertEqual(type_eco, "application/json")

    @patch('requests.post')
    @patch('modules.ai.ai_services.TRACKER')
    def test_send_webhook_report_no_logs(self, mock_tracker, mock_post):
        send_webhook_report = get_send_webhook_report()
        
        target_date = date(2026, 2, 22)
        webhook_url = "http://test-webhook.com"
        
        mock_tracker.get_discord_embeds.return_value = [{"title": "Test Embed"}]
        
        # Logger with no logs
        empty_logger = AppLogger("empty_logger")
        
        send_webhook_report(webhook_url, target_date, "inspect", "none", logger=empty_logger)
        
        # Verify requests.post was called with json payload (standard)
        self.assertTrue(mock_post.called)
        args, kwargs = mock_post.call_args
        self.assertIn('json', kwargs)
        self.assertEqual(kwargs['json']['embeds'][0]['title'], "Test Embed")
        self.assertNotIn('files', kwargs)

    @patch('requests.post')
    @patch('modules.ai.ai_services.TRACKER')
    def test_send_webhook_report_inspect_skips_files(self, mock_tracker, mock_post):
        send_webhook_report = get_send_webhook_report()
        
        target_date = date(2026, 2, 22)
        webhook_url = "http://test-webhook.com"
        
        mock_tracker.get_discord_embeds.return_value = [{"title": "Test Embed"}]
        mock_tracker.metrics.artifacts = {}
        
        # Logger WITH logs
        logger = AppLogger("test_logger")
        logger.log("This is a log that should NOT be sent for inspect")
        
        send_webhook_report(webhook_url, target_date, "inspect", "none", logger=logger)
        
        # Verify only the first post (dashboard) was made, and second one (files) skipped
        # In main.py: 
        # requests.post(webhook_url, json=payload, timeout=15) <- Always sent
        # if files and action not in skip_files_actions: ... requests.post(...) <- Should be skipped
        
        # If we check mock_post.call_count, it should be 1
        self.assertEqual(mock_post.call_count, 1)
        
        args, kwargs = mock_post.call_args
        self.assertIn('json', kwargs)
        self.assertNotIn('files', kwargs)

if __name__ == '__main__':
    unittest.main()
