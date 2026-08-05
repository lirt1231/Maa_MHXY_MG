import unittest

from tools.automation_calibration import CaptureSignature, CaptureStabilityGate


def signature(
    *,
    source="system",
    image_size=(1020, 718),
    source_size=(2040, 1492),
    content_rect=(0, 56, 2040, 1436),
    pixel_scale=2.0,
):
    return CaptureSignature(
        source=source,
        image_size=image_size,
        source_size=source_size,
        content_rect=content_rect,
        pixel_scale=pixel_scale,
    )


class CaptureSignatureTests(unittest.TestCase):
    def test_rejects_content_rect_outside_raw_capture(self):
        with self.assertRaisesRegex(ValueError, "超出原始截图"):
            signature(content_rect=(0, 56, 2040, 1500))

    def test_description_contains_diagnostic_geometry(self):
        description = signature().describe()

        self.assertIn("source=system", description)
        self.assertIn("image=1020x718", description)
        self.assertIn("raw=2040x1492", description)

    def test_payload_is_structured_for_runtime_diagnostics(self):
        payload = signature().to_payload()

        self.assertEqual(payload["captureSource"], "system")
        self.assertEqual(payload["imageSize"], [1020, 718])
        self.assertEqual(payload["contentRect"], [0, 56, 2040, 1436])


class CaptureStabilityGateTests(unittest.TestCase):
    def test_first_capture_locks_baseline_and_matching_capture_remains_valid(self):
        gate = CaptureStabilityGate()
        current = signature()

        self.assertIs(gate.observe(current), current)
        self.assertIs(gate.observe(current), current)
        self.assertIs(gate.validate(current), current)

    def test_raw_window_resize_invalidates_fixed_output_capture(self):
        gate = CaptureStabilityGate()
        gate.observe(signature())
        resized = signature(
            source_size=(1800, 1320),
            content_rect=(0, 48, 1800, 1272),
        )

        with self.assertRaisesRegex(RuntimeError, "旧标定已失效"):
            gate.observe(resized)

    def test_transparent_capture_padding_change_keeps_calibration_valid(self):
        gate = CaptureStabilityGate()
        baseline = signature(
            source_size=(1952, 1462),
            content_rect=(68, 108, 1816, 1270),
        )
        repadded = signature(
            source_size=(2040, 1550),
            content_rect=(112, 132, 1816, 1270),
        )

        gate.observe(baseline)

        self.assertIs(gate.observe(repadded), repadded)
        self.assertIs(gate.validate(repadded), repadded)

    def test_capture_source_change_invalidates_calibration(self):
        gate = CaptureStabilityGate()
        gate.observe(signature())
        maa = signature(
            source="maa",
            source_size=(1020, 718),
            content_rect=(0, 0, 1020, 718),
            pixel_scale=None,
        )

        with self.assertRaisesRegex(RuntimeError, "旧标定已失效"):
            gate.observe(maa)

    def test_missing_or_stale_scene_signature_cannot_authorize_input(self):
        gate = CaptureStabilityGate()
        current = signature()
        gate.observe(current)

        with self.assertRaisesRegex(RuntimeError, "缺少截图标定签名"):
            gate.validate(None)
        with self.assertRaisesRegex(RuntimeError, "已失效"):
            gate.validate(
                signature(
                    source_size=(1800, 1320),
                    content_rect=(0, 48, 1800, 1272),
                )
            )


if __name__ == "__main__":
    unittest.main()
