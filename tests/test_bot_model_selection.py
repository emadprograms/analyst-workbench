import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import discord
import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

os.environ["DISABLE_INFISICAL"] = "1"

class MockInteraction:
    def __init__(self, message_content=""):
        self.response = MagicMock()
        self.response.edit_message = AsyncMock()
        self.response.defer = AsyncMock()
        self.followup = MagicMock()
        self.followup.send = AsyncMock()
        self.original_response = AsyncMock()
        self.mock_orig_msg = AsyncMock(spec=discord.Message)
        self.original_response.return_value = self.mock_orig_msg
        
        # Mock message attribute of interaction
        self.message = MagicMock()
        self.message.content = message_content

class TestBotModelSelection(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        from discord_bot.ui_components import (
            BuildTypeSelectionView, TickerSelectionView, ModelSelectionDropdown, 
            TempCardDispatchView, ThinkingLevelDropdown
        )
        self.BuildTypeSelectionView = BuildTypeSelectionView
        self.TickerSelectionView = TickerSelectionView
        self.ModelSelectionDropdown = ModelSelectionDropdown
        self.TempCardDispatchView = TempCardDispatchView
        self.ThinkingLevelDropdown = ThinkingLevelDropdown

    async def asyncTearDown(self):
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

    # --- Model Dropdown Tests ---

    async def test_model_dropdown_initialization(self):
        """Test ModelSelectionDropdown defaults to 3.5 Flash."""
        parent_view = MagicMock()
        parent_view.selected_model = "gemini-3.5-flash-free"
        
        dropdown = self.ModelSelectionDropdown(parent_view, "gemini-3.5-flash-free")
        self.assertEqual(dropdown.parent_view, parent_view)
        self.assertEqual(len(dropdown.options), 2)
        
        opt35 = [o for o in dropdown.options if o.value == "gemini-3.5-flash-free"][0]
        opt3 = [o for o in dropdown.options if o.value == "gemini-3-flash-free"][0]
        self.assertTrue(opt35.default)
        self.assertFalse(opt3.default)

    async def test_model_dropdown_callback_updates_model(self):
        """Test ModelSelectionDropdown callback updates selection."""
        parent_view = MagicMock()
        parent_view.selected_model = "gemini-3.5-flash-free"
        
        dropdown = self.ModelSelectionDropdown(parent_view, "gemini-3.5-flash-free")
        dropdown._values = ["gemini-3-flash-free"]
        
        interaction = MockInteraction("Building cards using **gemini-3.5-flash-free**")
        await dropdown.callback(interaction)
        
        self.assertEqual(parent_view.selected_model, "gemini-3-flash-free")
        interaction.response.edit_message.assert_called_once()
        
        opt35 = [o for o in dropdown.options if o.value == "gemini-3.5-flash-free"][0]
        opt3 = [o for o in dropdown.options if o.value == "gemini-3-flash-free"][0]
        self.assertFalse(opt35.default)
        self.assertTrue(opt3.default)

    # --- Thinking Level Dropdown Tests ---

    async def test_thinking_dropdown_initialization(self):
        """Test ThinkingLevelDropdown defaults to high."""
        parent_view = MagicMock()
        parent_view.selected_thinking_level = "high"
        
        dropdown = self.ThinkingLevelDropdown(parent_view, "high")
        self.assertEqual(len(dropdown.options), 4)
        
        high_opt = [o for o in dropdown.options if o.value == "high"][0]
        self.assertTrue(high_opt.default)
        for opt in dropdown.options:
            if opt.value != "high":
                self.assertFalse(opt.default)

    async def test_thinking_dropdown_callback_updates_level(self):
        """Test ThinkingLevelDropdown callback updates the thinking level."""
        parent_view = MagicMock()
        parent_view.selected_thinking_level = "high"
        
        dropdown = self.ThinkingLevelDropdown(parent_view, "high")
        dropdown._values = ["medium"]
        
        interaction = MockInteraction("Some content")
        await dropdown.callback(interaction)
        
        self.assertEqual(parent_view.selected_thinking_level, "medium")
        interaction.response.edit_message.assert_called_once()
        
        medium_opt = [o for o in dropdown.options if o.value == "medium"][0]
        high_opt = [o for o in dropdown.options if o.value == "high"][0]
        self.assertTrue(medium_opt.default)
        self.assertFalse(high_opt.default)

    # --- Dispatch Tests (Model + Thinking Level) ---

    async def test_build_type_dispatch_economy_with_defaults(self):
        """Test BuildTypeSelectionView dispatches Economy with default model and thinking level."""
        dispatch_callback = AsyncMock(return_value=(True, "Dispatched", "https://github.com/run-url"))
        
        view = self.BuildTypeSelectionView(
            target_date="2026-05-28",
            dispatch_callback=dispatch_callback,
            actions_url="https://github.com/actions",
            stock_tickers=["AAPL", "MSFT"],
            ticker_view_class=MagicMock()
        )
        
        self.assertEqual(view.selected_model, "gemini-3.5-flash-free")
        self.assertEqual(view.selected_thinking_level, "high")
        
        interaction = MockInteraction()
        await view.economy_btn.callback(interaction)
        
        dispatch_callback.assert_called_once_with({
            "target_date": "2026-05-28",
            "action": "update-economy",
            "model": "gemini-3.5-flash-free",
            "thinking_level": "high"
        })

    async def test_ticker_selection_dispatch_with_thinking_level(self):
        """Test TickerSelectionView dispatches with selected model and thinking level."""
        dispatch_callback = AsyncMock(return_value=(True, "Dispatched", None))
        
        view = self.TickerSelectionView(
            target_date="2026-05-28",
            stock_tickers=["AAPL", "MSFT"],
            dispatch_callback=dispatch_callback,
            actions_url="https://github.com/actions",
            selected_model="gemini-3-flash-free",
            selected_thinking_level="medium"
        )
        
        self.assertEqual(view.selected_model, "gemini-3-flash-free")
        self.assertEqual(view.selected_thinking_level, "medium")
        view.selected_tickers = {"AAPL"}
        
        interaction = MockInteraction()
        await view.dispatch_btn.callback(interaction)
        
        dispatch_callback.assert_called_once_with({
            "target_date": "2026-05-28",
            "action": "update-company",
            "tickers": "AAPL",
            "model": "gemini-3-flash-free",
            "thinking_level": "medium"
        })

    async def test_temp_card_dispatch_with_thinking_level(self):
        """Test TempCardDispatchView dispatches with model and thinking level."""
        dispatch_callback = AsyncMock(return_value=(True, "Dispatched", None))
        
        view = self.TempCardDispatchView(
            target_date="2026-05-28",
            tickers_str="AAPL,MSFT",
            dispatch_callback=dispatch_callback,
            actions_url="https://github.com/actions"
        )
        
        view.selected_model = "gemini-3-flash-free"
        view.selected_thinking_level = "low"
        
        interaction = MockInteraction()
        await view.dispatch_btn.callback(interaction)
        
        dispatch_callback.assert_called_once_with({
            "target_date": "2026-05-28",
            "action": "update-temp-company",
            "tickers": "AAPL,MSFT",
            "model": "gemini-3-flash-free",
            "thinking_level": "low"
        })

if __name__ == '__main__':
    unittest.main()
