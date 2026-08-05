import unittest

from tools.resource_snapshots import (
    ResourceFieldEvidence,
    ResourceSnapshot,
    ResourceValues,
    ResourceYield,
    calculate_resource_yield,
)


BEFORE_TIME = "2026-07-30T02:00:00+00:00"
AFTER_TIME = "2026-07-30T02:10:00+00:00"


def snapshot(
    *,
    phase="before",
    captured_at=BEFORE_TIME,
    level=69,
    experience=1000,
    experience_required=5000,
    gold=10000,
    silver=20000,
    target_id="local-primary",
    character_id="character-1",
    snapshot_id=1,
):
    return ResourceSnapshot(
        target_id=target_id,
        character_id=character_id,
        phase=phase,
        captured_at=captured_at,
        snapshot_id=snapshot_id,
        values=ResourceValues(
            level=level,
            experience=experience,
            experience_required=experience_required,
            gold=gold,
            silver=silver,
        ),
    )


class ResourceSnapshotContractTests(unittest.TestCase):
    def test_snapshot_derives_status_and_serializes_field_evidence(self):
        evidence = ResourceFieldEvidence(
            status="captured",
            source_page="backpack",
            raw_text="10,000",
            confidence=0.98,
            box=(100, 600, 80, 20),
            frame_id="frame-1",
        )
        current = ResourceSnapshot(
            target_id="local-primary",
            phase="before",
            captured_at=BEFORE_TIME,
            values=ResourceValues(gold=10000),
            evidence={"gold": evidence},
        )

        self.assertEqual(current.status, "partial")
        self.assertEqual(current.to_payload()["values"]["gold"], 10000)
        self.assertEqual(
            current.to_payload()["evidence"]["gold"]["frameId"], "frame-1"
        )

    def test_snapshot_rejects_invalid_values_time_and_status(self):
        invalid = [
            {"values": ResourceValues(gold=None), "captured_at": "2026-07-30"},
            {"values": ResourceValues(gold=1), "status": "success"},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ResourceSnapshot(
                    target_id="local-primary",
                    phase="before",
                    captured_at=changes.get("captured_at", BEFORE_TIME),
                    values=changes["values"],
                    status=changes.get("status"),
                )
        with self.assertRaises(ValueError):
            ResourceValues(silver=True)

    def test_evidence_rejects_invalid_confidence_and_box(self):
        with self.assertRaises(ValueError):
            ResourceFieldEvidence("captured", "backpack", confidence=float("nan"))
        with self.assertRaises(ValueError):
            ResourceFieldEvidence("captured", "backpack", box=(1, 2, 0, 4))

    def test_evidence_status_must_match_field_value(self):
        captured = ResourceFieldEvidence("captured", "backpack")
        missing = ResourceFieldEvidence("missing", "backpack")
        cases = [
            (ResourceValues(), {"gold": captured}),
            (ResourceValues(gold=100), {"gold": missing}),
        ]
        for values, evidence in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                ResourceSnapshot(
                    target_id="local-primary",
                    phase="before",
                    captured_at=BEFORE_TIME,
                    values=values,
                    evidence=evidence,
                )


class ResourceYieldCalculationTests(unittest.TestCase):
    def test_same_level_calculates_positive_and_negative_net_changes(self):
        before = snapshot()
        after = snapshot(
            phase="after",
            captured_at=AFTER_TIME,
            experience=1300,
            gold=9800,
            silver=20750,
            snapshot_id=2,
        )

        result = calculate_resource_yield(before, after, task_status="completed")

        self.assertEqual(result.attribution_status, "reliable")
        self.assertEqual(result.experience_delta, 300)
        self.assertEqual(result.gold_delta, -200)
        self.assertEqual(result.silver_delta, 750)
        self.assertEqual(result.before_snapshot_id, 1)
        self.assertEqual(result.after_snapshot_id, 2)

    def test_single_level_up_uses_before_required_experience(self):
        before = snapshot(experience=4800, experience_required=5000)
        after = snapshot(
            phase="after",
            captured_at=AFTER_TIME,
            level=70,
            experience=450,
            experience_required=6000,
            snapshot_id=2,
        )

        result = calculate_resource_yield(before, after, task_status="completed")

        self.assertEqual(result.attribution_status, "reliable")
        self.assertEqual(result.experience_delta, 650)

    def test_multiple_level_up_requires_intermediate_thresholds(self):
        before = snapshot(level=69, experience=4800, experience_required=5000)
        after = snapshot(
            phase="after",
            captured_at=AFTER_TIME,
            level=71,
            experience=300,
            experience_required=7000,
            snapshot_id=2,
        )

        unknown = calculate_resource_yield(before, after, task_status="completed")
        known = calculate_resource_yield(
            before,
            after,
            task_status="completed",
            level_requirements={70: 6000},
        )

        self.assertEqual(unknown.attribution_status, "cross_level_unknown")
        self.assertIsNone(unknown.experience_delta)
        self.assertIn("level_requirement_missing:70", unknown.reasons)
        self.assertEqual(known.attribution_status, "reliable")
        self.assertEqual(known.experience_delta, 6500)

    def test_partial_snapshot_keeps_available_currency_deltas(self):
        before = snapshot(silver=None)
        after = snapshot(
            phase="after",
            captured_at=AFTER_TIME,
            experience=1100,
            gold=10100,
            silver=None,
            snapshot_id=2,
        )

        result = calculate_resource_yield(before, after, task_status="completed")

        self.assertEqual(result.attribution_status, "partial")
        self.assertEqual(result.experience_delta, 100)
        self.assertEqual(result.gold_delta, 100)
        self.assertIsNone(result.silver_delta)

    def test_already_completed_is_not_a_zero_yield(self):
        result = calculate_resource_yield(
            snapshot(),
            None,
            task_status="already_completed",
        )

        self.assertEqual(result.attribution_status, "not_run")
        self.assertIsNone(result.experience_delta)
        self.assertIsNone(result.gold_delta)
        self.assertIsNone(result.silver_delta)

    def test_missing_after_snapshot_is_unavailable(self):
        result = calculate_resource_yield(snapshot(), None, task_status="cancelled")

        self.assertEqual(result.attribution_status, "unavailable")
        self.assertEqual(result.reasons, ("resource_snapshot_unavailable",))

    def test_target_or_character_change_blocks_all_deltas(self):
        cases = [
            snapshot(
                phase="after",
                captured_at=AFTER_TIME,
                target_id="other-window",
                snapshot_id=2,
            ),
            snapshot(
                phase="after",
                captured_at=AFTER_TIME,
                character_id="character-2",
                snapshot_id=2,
            ),
        ]
        for after in cases:
            with self.subTest(after=after):
                result = calculate_resource_yield(
                    snapshot(), after, task_status="completed"
                )
                self.assertEqual(result.attribution_status, "interrupted")
                self.assertIsNone(result.gold_delta)

    def test_level_regression_is_interrupted(self):
        result = calculate_resource_yield(
            snapshot(),
            snapshot(
                phase="after",
                captured_at=AFTER_TIME,
                level=68,
                snapshot_id=2,
            ),
            task_status="completed",
        )

        self.assertEqual(result.attribution_status, "interrupted")
        self.assertIn("level_regressed", result.reasons)

    def test_external_interference_retains_deltas_but_lowers_attribution(self):
        after = snapshot(
            phase="after",
            captured_at=AFTER_TIME,
            experience=1200,
            gold=10300,
            silver=20400,
            snapshot_id=2,
        )

        result = calculate_resource_yield(
            snapshot(),
            after,
            task_status="completed",
            interference_reasons=("user_input",),
        )

        self.assertEqual(result.attribution_status, "interrupted")
        self.assertEqual(result.gold_delta, 300)
        self.assertIn("user_input", result.reasons)

    def test_time_order_is_strict(self):
        with self.assertRaisesRegex(ValueError, "时间顺序"):
            calculate_resource_yield(
                snapshot(captured_at=AFTER_TIME),
                snapshot(phase="after", captured_at=BEFORE_TIME, snapshot_id=2),
                task_status="completed",
            )

    def test_yield_contract_rejects_inconsistent_reliable_or_unavailable_data(self):
        with self.assertRaises(ValueError):
            ResourceYield("completed", "reliable", gold_delta=1)
        with self.assertRaises(ValueError):
            ResourceYield("cancelled", "unavailable", gold_delta=1)

    def test_calculator_rejects_string_interference_sequence(self):
        with self.assertRaises(ValueError):
            calculate_resource_yield(
                snapshot(),
                snapshot(phase="after", captured_at=AFTER_TIME, snapshot_id=2),
                task_status="completed",
                interference_reasons="user_input",
            )


if __name__ == "__main__":
    unittest.main()
