import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import discord
from discord.ext import commands
import sys
import os
from datetime import datetime, date

# Add project root to sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

# Pre-import environment setup
os.environ["DISABLE_INFISICAL"] = "1"

# Mock discord.py objects
class MockContext:
    def __init__(self):
        self.send = AsyncMock()
        self.message = MagicMock()
        self.author = MagicMock()
        # Ensure return value of send is an awaitable that returns a mock message
        self.mock_msg = AsyncMock(spec=discord.Message)
        self.send.return_value = self.mock_msg

class MockInteraction:
    def __init__(self):
        self.response = MagicMock()
        self.response.send_message = AsyncMock()
        self.response.edit_message = AsyncMock()
        self.response.send_modal = AsyncMock()
        self.response.defer = AsyncMock()
        self.followup = MagicMock()
        self.followup.send = AsyncMock()
        self.original_response = AsyncMock()
        self.mock_orig_msg = AsyncMock(spec=discord.Message)
        self.original_response.return_value = self.mock_orig_msg

class TestBotGetNews(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # We need to import the bot and command from discord_bot.bot
        from discord_bot.bot import getnews, get_target_date
        self.getnews = getnews
        self.get_target_date = get_target_date

    async def asyncTearDown(self):
        # Cleanup global connections
        try:
            from modules.ai.ai_services import KEY_MANAGER
            if KEY_MANAGER:
                KEY_MANAGER.close()
        except:
            pass
        
        try:
            from modules.core.config import infisical_mgr
            if infisical_mgr:
                infisical_mgr.close()
        except:
            pass

    @patch('discord_bot.bot.get_target_date')
    @patch('discord_bot.bot.get_daily_inputs')
    @patch('modules.ai.ai_services.summarize_news_with_gemini')
    async def test_getnews_full_args_macro(self, mock_summarize, mock_inputs, mock_date):
        """Test !getnews 0 macro"""
        ctx = MockContext()
        mock_date.return_value = "2026-03-03"
        mock_inputs.return_value = ("ENTITY: Global [MACRO]\nNews", None)
        mock_summarize.return_value = "Summary of news"
        
        await self.getnews(ctx, "0", "macro")
        
        ctx.send.assert_called()
        msg = ctx.send.return_value
        msg.edit.assert_called()
        edit_kwargs = msg.edit.call_args[1]
        self.assertIn("embeds", edit_kwargs)
        embed = edit_kwargs["embeds"][0]
        self.assertEqual(embed.title, "📰 MACRO News Summary | 2026-03-03")

    @patch('discord_bot.bot.get_target_date')
    async def test_getnews_no_args_shows_date_selection(self, mock_date):
        """Test !getnews (no args)"""
        ctx = MockContext()
        mock_date.return_value = None
        
        await self.getnews(ctx)
        
        ctx.send.assert_called()
        args, kwargs = ctx.send.call_args
        self.assertEqual(args[0], "🗓️ **Select Date for News Summary:**")
        self.assertIn("view", kwargs)
        self.assertEqual(type(kwargs["view"]).__name__, "DateSelectionView")

    @patch('discord_bot.bot.get_target_date')
    async def test_getnews_only_target_defaults_to_today(self, mock_date):
        """Test !getnews AAPL - should default to today and fetch news"""
        ctx = MockContext()
        mock_date.return_value = "2026-03-03"
        
        # We also need to mock get_daily_inputs to avoid DB error
        with patch('discord_bot.bot.get_daily_inputs', return_value=(None, None)):
            await self.getnews(ctx, "AAPL")
        
        ctx.send.assert_called()
        args, kwargs = ctx.send.call_args
        self.assertIn("Fetching and summarizing AAPL news", args[0])
        self.assertIn("2026-03-03", args[0])

    @patch('discord_bot.bot.get_target_date')
    @patch('discord_bot.bot.get_daily_inputs')
    async def test_getnews_not_found(self, mock_inputs, mock_date):
        """Test !getnews 0 AAPL when no news in DB"""
        ctx = MockContext()
        mock_date.return_value = "2026-03-03"
        mock_inputs.return_value = (None, None)
        
        await self.getnews(ctx, "0", "AAPL")
        
        msg = ctx.send.return_value
        msg.edit.assert_called_with(content="❌ **NO NEWS FOUND** for **2026-03-03**.")

    async def test_target_selection_view_macro(self):
        """Test clicking Macro in TargetSelectionView"""
        from discord_bot.ui_components import TargetSelectionView
        finish_callback = AsyncMock()
        view = TargetSelectionView("2026-03-03", finish_callback)
        
        interaction = MockInteraction()
        await view.macro_btn.callback(interaction)
        
        finish_callback.assert_called_with(interaction, "2026-03-03", "MACRO")

    async def test_target_selection_view_company(self):
        """Test clicking Company in TargetSelectionView"""
        from discord_bot.ui_components import TargetSelectionView, TargetTickerModal
        finish_callback = AsyncMock()
        view = TargetSelectionView("2026-03-03", finish_callback)
        
        interaction = MockInteraction()
        await view.company_btn.callback(interaction)
        
        interaction.response.send_modal.assert_called()
        modal = interaction.response.send_modal.call_args[0][0]
        self.assertIsInstance(modal, TargetTickerModal)
        self.assertEqual(modal.target_date, "2026-03-03")

if __name__ == '__main__':
    unittest.main()
