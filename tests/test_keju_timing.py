import importlib
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


AGENT_DIR = Path(__file__).parents[1] / "agent"
RECOGNITION_DIR = AGENT_DIR / "custom" / "recognition"
sys.path.insert(0, str(AGENT_DIR))
os.environ.setdefault("MAA_DIRECT_RESOURCE", "1")


class FakeCustomRecognition:
    class AnalyzeArg:
        pass

    class AnalyzeResult:
        def __init__(self, box, detail):
            self.box = box
            self.detail = detail


maa = types.ModuleType("maa")
maa_context = types.ModuleType("maa.context")
maa_context.Context = type("Context", (), {})
maa_custom_recognition = types.ModuleType("maa.custom_recognition")
maa_custom_recognition.CustomRecognition = FakeCustomRecognition
maa_custom_action = types.ModuleType("maa.custom_action")
maa_custom_action.CustomAction = type("CustomAction", (), {})
custom = types.ModuleType("custom")
custom.__path__ = [str(AGENT_DIR / "custom")]
recognition = types.ModuleType("custom.recognition")
recognition.__path__ = [str(RECOGNITION_DIR)]
fake_utils = types.ModuleType("utils")
fake_utils.logger = types.SimpleNamespace(
    info=lambda message: None,
    warning=lambda message: None,
)

with patch.dict(
    sys.modules,
    {
        "maa": maa,
        "maa.context": maa_context,
        "maa.custom_recognition": maa_custom_recognition,
        "maa.custom_action": maa_custom_action,
        "custom": custom,
        "custom.recognition": recognition,
        "utils": fake_utils,
    },
):
    keju_hybrid = importlib.import_module("custom.recognition.keju_hybrid")


class FakeClock:
    def __init__(self, values):
        self._values = iter(values)

    def __call__(self):
        return next(self._values)


class StageTimingsTests(unittest.TestCase):
    def test_tracks_repeated_stages_and_reports_skipped_stages(self):
        clock = FakeClock([0.0, 0.125, 0.25, 0.375, 0.5, 1.0])
        timings = keju_hybrid._StageTimings(clock=clock)

        with timings.track("ocr"):
            pass
        with timings.track("ocr"):
            pass

        summary = timings.summary("ready")

        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["total_ms"], 1000)
        self.assertEqual(
            summary["stages"]["ocr"], {"elapsed_ms": 250, "calls": 2}
        )
        self.assertEqual(
            summary["stages"]["model_request"], {"elapsed_ms": 0, "calls": 0}
        )

    def test_failed_stage_is_still_recorded(self):
        clock = FakeClock([0.0, 0.125, 0.25, 0.5])
        timings = keju_hybrid._StageTimings(clock=clock)

        with self.assertRaisesRegex(RuntimeError, "failed"):
            with timings.track("model_request"):
                raise RuntimeError("failed")

        summary = timings.summary("unreliable")
        self.assertEqual(
            summary["stages"]["model_request"],
            {"elapsed_ms": 125, "calls": 1},
        )

    def test_summary_has_a_fixed_non_sensitive_schema(self):
        summary = keju_hybrid._build_timing_summary(
            "ready",
            {
                "model_request": {"elapsed_ms": 12, "calls": 1},
                "api_key=top-secret": {"elapsed_ms": 999, "calls": 1},
            },
            20,
        )
        serialized = json.dumps(summary)

        self.assertEqual(set(summary["stages"]), set(keju_hybrid.TIMING_STAGES))
        self.assertNotIn("top-secret", serialized)
        self.assertNotIn("api_key", serialized)


class AnalyzeAuditTests(unittest.TestCase):
    def setUp(self):
        self.recognizer = object.__new__(keju_hybrid.KejuHybridAnswer)

    def test_ready_path_publishes_timing_summary(self):
        result = object()

        def ready(recognizer, context, argv, timings):
            with timings.track("parse_decision"):
                recognizer.last_status = "ready"
            return result

        with patch.object(keju_hybrid.KejuHybridAnswer, "_analyze", ready), patch.object(
            keju_hybrid.logger, "info"
        ) as log_info:
            actual = self.recognizer.analyze(None, None)

        self.assertIs(actual, result)
        self.assertEqual(self.recognizer.last_timings["status"], "ready")
        self.assertEqual(
            self.recognizer.last_timings["stages"]["parse_decision"]["calls"], 1
        )
        self.assertIn('"status": "ready"', log_info.call_args.args[0])

    def test_unreliable_exception_still_publishes_timing_summary(self):
        def unreliable(recognizer, context, argv, timings):
            recognizer.last_status = "unreliable"
            with timings.track("model_request"):
                raise RuntimeError("no reliable answer")

        with patch.object(
            keju_hybrid.KejuHybridAnswer, "_analyze", unreliable
        ), patch.object(keju_hybrid.logger, "info") as log_info, self.assertRaisesRegex(
            RuntimeError, "no reliable answer"
        ):
            self.recognizer.analyze(None, None)

        self.assertEqual(self.recognizer.last_timings["status"], "unreliable")
        self.assertEqual(self.recognizer.last_reason, "no reliable answer")
        self.assertEqual(
            self.recognizer.last_timings["stages"]["model_request"]["calls"], 1
        )
        self.assertIn('"status": "unreliable"', log_info.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
