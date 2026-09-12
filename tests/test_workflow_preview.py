from __future__ import annotations

import unittest

from app.roast.preview import build_config_diff_preview, build_end_roast_preview, build_export_preview
from app.roast.workflow import RoastFlowState, RoastSessionBuffer, RoastStage, RoastWorkflow, WorkflowError


class RoastWorkflowTests(unittest.TestCase):
    def test_required_multi_batch_path_and_drop_lock(self) -> None:
        workflow = RoastWorkflow()
        self.assertEqual(workflow.start_acquisition(), 1)
        self.assertEqual(workflow.state, RoastFlowState.CONNECTED)
        self.assertEqual(workflow.stage, RoastStage.ROASTING)

        workflow.start_roast()
        self.assertEqual(workflow.state, RoastFlowState.READY_TO_CHARGE)
        events = []
        workflow.record_event("CHARGE", 0.0, events)
        events.append({"event_type": "CHARGE", "time_s": 0.0})
        self.assertEqual(workflow.state, RoastFlowState.ROASTING)
        workflow.record_event("DROP", 600.0, events)
        self.assertEqual(workflow.stage, RoastStage.AWAIT_END)
        self.assertEqual(workflow.state, RoastFlowState.FINISHED)
        with self.assertRaisesRegex(WorkflowError, "已 DROP"):
            workflow.cancel_event("CHARGE")

        self.assertEqual(workflow.finish_current_batch(), 1)
        self.assertEqual(workflow.stage, RoastStage.BETWEEN)
        self.assertEqual(workflow.state, RoastFlowState.CONNECTED)
        self.assertEqual(workflow.start_next_batch(), 2)
        self.assertEqual(workflow.stage, RoastStage.ROASTING)
        self.assertEqual(workflow.state, RoastFlowState.READY_TO_CHARGE)

    def test_invalid_transitions_are_rejected(self) -> None:
        workflow = RoastWorkflow()
        with self.assertRaisesRegex(WorkflowError, "下一锅"):
            workflow.start_next_batch()
        with self.assertRaisesRegex(WorkflowError, "非烘焙中"):
            workflow.record_event("CHARGE", 0.0, [])
        workflow.start_acquisition()
        workflow.stop_acquisition()
        self.assertEqual(workflow.state, RoastFlowState.IDLE)


class PreviewBuilderTests(unittest.TestCase):
    def test_previews_are_detached_and_contain_document_columns(self) -> None:
        workflow = RoastWorkflow()
        workflow.start_acquisition()
        buffer = RoastSessionBuffer(
            samples=[{"time_s": 5.0, "et": 180.0, "bt": 165.0, "it": 120.0, "work": 130.0}],
            events=[{"event_type": "CHARGE", "time_s": 2.0, "bt": 100.0}],
            actions=[{"time_s": 0.0, "gas_kpa": 1.8, "damper_level": 6, "rpm_level": 7}],
        )
        end_preview = build_end_roast_preview(workflow, buffer, {"coffee_name": "日晒耶加"})
        export_preview = build_export_preview(workflow, buffer)
        config_preview = build_config_diff_preview({"theme": "深色"}, {"theme": "浅色", "ror_window": 15})

        buffer.samples[0]["bt"] = 999.0
        self.assertEqual(end_preview["snapshot"]["samples"][0]["bt"], 165.0)
        self.assertEqual(end_preview["summary"]["target"], "charges")
        self.assertEqual(export_preview["columns"], ("time_s", "ET_C", "BT_C", "IT_C", "XT_C", "GAS_mbar", "DAM", "SPD"))
        self.assertEqual(export_preview["rows"][0]["GAS_mbar"], 18.0)
        self.assertEqual(len(config_preview["changes"]), 2)


if __name__ == "__main__":
    unittest.main()
