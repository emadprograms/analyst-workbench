import unittest
from modules.core.tracker import ExecutionTracker

class TestTrackerTables(unittest.TestCase):
    def test_company_card_validation_tables(self):
        tracker = ExecutionTracker()
        tracker.start("Company_Card_Update")
        
        # Mock some ticker outcomes
        tracker.metrics.ticker_outcomes = {
            "AAPL": {"status": "success"},
            "MSFT": {"status": "success"}
        }
        
        # Add some dummy issues to verify markers
        tracker.metrics.quality_reports = {
            "AAPL": [{"rule": "SCHEMA_MISSING"}]
        }
        tracker.metrics.data_reports = {
            "MSFT": [{"rule": "DATA_BIAS_CONTRADICTION"}]
        }
        
        q_table, d_table, i_table = tracker._build_validation_tables()
        
        # Check standard headers exist
        self.assertIn("Sch | Plc | Act | Con | Scr | Ton | Par | Pln | Sub", q_table)
        self.assertIn("Bias | Trnd | Gaps | HiLo | Sup | Vol | Date", d_table)
        
        # AAPL should fail Sch but pass others
        self.assertIn("AAPL    |  F  |  .  |  .  |  .  |  .  |  .  |  .  |  .  |  . ", q_table)
        
        # MSFT should fail Bias in data table
        self.assertIn("MSFT    |  F   |  .   |  .   |  .   |  .  |  .  |  .  ", d_table)

    def test_economy_card_validation_tables(self):
        tracker = ExecutionTracker()
        tracker.start("Economy_Card_Update")
        
        # Mock some ticker outcomes (for Economy, the "ticker" is often just "ECONOMY")
        tracker.metrics.ticker_outcomes = {
            "ECONOMY": {"status": "success"}
        }
        
        # Add some dummy issues to verify markers
        tracker.metrics.quality_reports = {
            "ECONOMY": [{"rule": "ECON_BAD_BIAS"}]
        }
        tracker.metrics.data_reports = {
            "ECONOMY": [{"rule": "DATA_ECON_BIAS_MISMATCH"}, {"rule": "DATA_BREADTH_MISMATCH"}]
        }
        
        q_table, d_table, i_table = tracker._build_validation_tables()
        
        # Check ECONOMY headers exist
        self.assertIn("Sch | Plc | Act | Bias | Rot | Sub", q_table)
        self.assertIn("Bias | Sect | Brdt | Intr | Rtn | Date | Gaps | HiLo | Sup", d_table)
        
        # ECONOMY should fail Bias in quality table
        self.assertIn("ECONOMY |  .  |  .  |  .  |  F   |  .  |  . ", q_table)
        
        # ECONOMY should fail Bias and Brdt in data table
        self.assertIn("ECONOMY |  F   |  .   |  F   |  .   |  .  |  .   |  .   |  .   |  . ", d_table)

if __name__ == "__main__":
    unittest.main()
