import unittest
from unittest.mock import patch, MagicMock
from modules.ai.ai_services import filter_daily_news_for_macro, summarize_news_with_gemini
from modules.core.logger import AppLogger

class TestGetNewsFeatures(unittest.TestCase):

    def test_filter_daily_news_for_macro_empty(self):
        self.assertEqual(filter_daily_news_for_macro(""), "")
        self.assertEqual(filter_daily_news_for_macro(None), "")

    def test_filter_daily_news_for_macro_only_macro(self):
        news = "ENTITY: Global [MACRO]\nFed raises rates.\n\nENTITY: Market [MACRO]\nUnemployment drops."
        result = filter_daily_news_for_macro(news)
        self.assertIn("Fed raises rates.", result)
        self.assertIn("Unemployment drops.", result)
        self.assertIn("ENTITY: Global [MACRO]", result)
        
    def test_filter_daily_news_for_macro_mixed(self):
        news = "ENTITY: Global [MACRO]\nFed raises rates.\n\nENTITY: AAPL [SECTOR:Tech]\nApple earnings up.\n\nENTITY: Market [MACRO]\nUnemployment drops."
        result = filter_daily_news_for_macro(news)
        self.assertIn("Fed raises rates.", result)
        self.assertIn("Unemployment drops.", result)
        self.assertNotIn("Apple earnings up.", result)
        self.assertNotIn("AAPL", result)

    def test_filter_daily_news_for_macro_no_macro(self):
        news = "ENTITY: AAPL [SECTOR:Tech]\nApple earnings up."
        result = filter_daily_news_for_macro(news)
        self.assertEqual(result, "No macro news found for today.")

    @patch('modules.ai.ai_services.call_gemini_api')
    def test_summarize_news_with_gemini_macro(self, mock_call):
        mock_call.return_value = "- Fed raised rates.\n- Market is bullish."
        logger = AppLogger("test")
        
        result = summarize_news_with_gemini("ENTITY: Global [MACRO]\nFed raised rates.", "MACRO", logger)
        
        self.assertEqual(result, "- Fed raised rates.\n- Market is bullish.")
        # Verify call_gemini_api was called correctly
        self.assertTrue(mock_call.called)
        args, kwargs = mock_call.call_args
        self.assertIn("[MACRO NEWS]", args[0])
        self.assertEqual(kwargs['model_name'], "gemini-3-flash-free")

    @patch('modules.ai.ai_services.call_gemini_api')
    def test_summarize_news_with_gemini_company(self, mock_call):
        mock_call.return_value = "- AAPL earnings up."
        logger = AppLogger("test")
        
        result = summarize_news_with_gemini("ENTITY: AAPL [SECTOR:Tech]\nApple earnings up.", "AAPL", logger)
        
        self.assertEqual(result, "- AAPL earnings up.")
        # Verify call_gemini_api was called correctly
        args, kwargs = mock_call.call_args
        self.assertIn("[NEWS FOR AAPL]", args[0])
        self.assertEqual(kwargs['model_name'], "gemini-3-flash-free")

    def test_summarize_news_with_gemini_no_news(self):
        logger = AppLogger("test")
        result1 = summarize_news_with_gemini("No macro news found for today.", "MACRO", logger)
        result2 = summarize_news_with_gemini("No specific company or sector news found for today.", "AAPL", logger)
        result3 = summarize_news_with_gemini("   ", "AAPL", logger)
        
        self.assertEqual(result1, "No news found to summarize for this target.")
        self.assertEqual(result2, "No news found to summarize for this target.")
        self.assertEqual(result3, "No news found to summarize for this target.")

if __name__ == '__main__':
    unittest.main()
