import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image

from tools.automation_calibration import CaptureSignature
from tools.macos_window_adapter import (
    LegacyMacOSWindowBackend,
    _window_content_rect,
)
from tools.macos_window_backend import CapturedFrame, map_frame_point_to_screen


def frame(
    *,
    image_size=(1020, 718),
    source="maa",
    source_size=None,
    content_rect=None,
    pixel_scale=None,
):
    source_size = source_size or image_size
    content_rect = content_rect or (0, 0, source_size[0], source_size[1])
    return CapturedFrame(
        image=np.ones((image_size[1], image_size[0], 3), dtype=np.uint8),
        signature=CaptureSignature(
            source=source,
            image_size=image_size,
            source_size=source_size,
            content_rect=content_rect,
            pixel_scale=pixel_scale,
        ),
        capture_method=source,
    )


class FakeNativeInput:
    def __init__(self, geometry):
        self.geometry = geometry
        self.window_id = None
        self.clicks = []
        self.drags = []

    def bind_window(self, window_id):
        self.window_id = window_id

    def window_geometry(self):
        return self.geometry

    def click_screen(self, point):
        self.clicks.append(point)
        return point

    def drag_screen(self, start, end):
        self.drags.append((start, end))


class SequenceGeometryNativeInput(FakeNativeInput):
    def __init__(self, geometry, later_geometries):
        super().__init__(geometry)
        self.later_geometries = list(later_geometries)

    def window_geometry(self):
        if self.later_geometries:
            return self.later_geometries.pop(0)
        return self.geometry


class FakeCapture:
    def __init__(self, captured_frame):
        self.frame = captured_frame
        self.last_frame = None

    def capture(self, guard):
        guard.checkpoint()
        self.last_frame = self.frame
        return self.frame.image


class SequenceCapture:
    def __init__(self, frames):
        self.frames = list(frames)
        self.last_frame = None
        self.capture_count = 0

    def capture(self, guard):
        guard.checkpoint()
        self.last_frame = self.frames[min(self.capture_count, len(self.frames) - 1)]
        self.capture_count += 1
        return self.last_frame.image


class FrameGeometryTests(unittest.TestCase):
    def test_system_capture_excludes_shadow_and_title_bar(self):
        pixels = np.zeros((1776, 2238, 4), dtype=np.uint8)
        pixels[1:1775, 1:2237, 3] = 80
        pixels[42:1682, 68:2170, 3] = 255
        captured = Image.fromarray(pixels, "RGBA")

        self.assertEqual(
            _window_content_rect(captured, 2),
            (68, 90, 2102, 1592),
        )

    def test_maps_retina_pixels_into_negative_origin_content_rect(self):
        current = frame(image_size=(2040, 1436))
        current = CapturedFrame(
            image=current.image,
            signature=current.signature,
            window_bounds_points=(-1020.0, 40.0, 1020.0, 746.0),
            content_rect_points=(-1020.0, 68.0, 1020.0, 718.0),
            scale_factor=2.0,
            capture_method="native",
        )

        self.assertEqual(
            map_frame_point_to_screen((1020, 718), current),
            (-510.0, 427.0),
        )

    def test_rejects_pixel_outside_frame(self):
        current = frame()

        with self.assertRaisesRegex(ValueError, "超出当前画面"):
            map_frame_point_to_screen((1020, 10), current)

    def test_geometry_payload_is_structured_for_read_only_preview(self):
        current = frame()
        current = CapturedFrame(
            image=current.image,
            signature=current.signature,
            window_bounds_points=(100.0, 40.0, 1020.0, 746.0),
            content_rect_points=(100.0, 68.0, 1020.0, 718.0),
            scale_factor=1.0,
            capture_method="maa",
        )

        payload = current.geometry_payload()

        self.assertEqual(payload["windowBoundsPoints"], [100.0, 40.0, 1020.0, 746.0])
        self.assertEqual(payload["contentRectPoints"], [100.0, 68.0, 1020.0, 718.0])
        self.assertEqual(payload["scaleFactor"], 1.0)


class LegacyMacOSWindowBackendTests(unittest.TestCase):
    def backend(self, captured_frame, geometry=(100.0, -50.0, 923.0, 748.0, 77)):
        native = FakeNativeInput(geometry)
        capture = FakeCapture(captured_frame)
        backend = LegacyMacOSWindowBackend(
            target_id="local-primary",
            window_id=42,
            controller=Mock(),
            capture_driver=capture,
            native_input=native,
        )
        return backend, native

    def test_maa_frame_derives_content_rect_without_title_bar_constant(self):
        backend, _native = self.backend(frame(image_size=(923, 720)))

        captured = backend.capture(Mock())

        self.assertEqual(captured.window_bounds_points, (100.0, -50.0, 923.0, 748.0))
        self.assertEqual(captured.content_rect_points, (100.0, -22.0, 923.0, 720.0))
        self.assertEqual(captured.scale_factor, 1.0)

    def test_system_frame_uses_retained_pixel_scale_and_content_crop(self):
        current = frame(
            source="system",
            source_size=(2040, 1492),
            content_rect=(0, 56, 2040, 1436),
            pixel_scale=2.0,
        )
        backend, _native = self.backend(
            current,
            geometry=(100.0, -50.0, 1020.0, 746.0, 77),
        )

        captured = backend.capture(Mock())

        self.assertEqual(captured.content_rect_points, (100.0, -22.0, 1020.0, 718.0))
        self.assertEqual(captured.scale_factor, 2.0)

    def test_transient_capture_geometry_mismatch_is_recaptured_before_use(self):
        mismatched = frame(
            source="system",
            source_size=(1822, 1326),
            content_rect=(0, 56, 1822, 1270),
            pixel_scale=2.0,
        )
        stable = frame(
            source="system",
            source_size=(1816, 1326),
            content_rect=(0, 56, 1816, 1270),
            pixel_scale=2.0,
        )
        native = FakeNativeInput((-1510.0, 65.0, 908.0, 663.0, 77))
        capture = SequenceCapture([mismatched, stable])
        backend = LegacyMacOSWindowBackend(
            target_id="local-primary",
            window_id=42,
            controller=Mock(),
            capture_driver=capture,
            native_input=native,
        )
        guard = Mock()

        captured = backend.capture(guard)

        self.assertEqual(capture.capture_count, 2)
        guard.wait.assert_called_once_with(1.0)
        self.assertEqual(
            captured.content_rect_points,
            (-1510.0, 93.0, 908.0, 635.0),
        )

    def test_persistent_capture_geometry_mismatch_still_stops_safely(self):
        mismatched = frame(
            source="system",
            source_size=(1824, 1326),
            content_rect=(0, 56, 1824, 1270),
            pixel_scale=2.0,
        )
        native = FakeNativeInput((-1510.0, 65.0, 908.0, 663.0, 77))
        capture = SequenceCapture([mismatched])
        backend = LegacyMacOSWindowBackend(
            target_id="local-primary",
            window_id=42,
            controller=Mock(),
            capture_driver=capture,
            native_input=native,
        )
        guard = Mock()

        with self.assertRaisesRegex(
            RuntimeError,
            r"截图内容=912x635.*目标窗口=908x663.*连续 8 次不一致",
        ):
            backend.capture(guard)

        self.assertEqual(capture.capture_count, 8)
        self.assertEqual(guard.wait.call_count, 7)
        self.assertIsNone(backend.last_frame)

    def test_window_move_updates_origin_without_invalidating_frame(self):
        backend, native = self.backend(frame(image_size=(923, 720)))
        guard = Mock()
        guard.perform_input.side_effect = lambda function, *args: function(*args)
        captured = backend.capture(guard)
        native.geometry = (300.0, 40.0, 923.0, 748.0, 77)

        result = backend.click((461.5, 360), captured, guard)

        self.assertEqual(result, (761.5, 428.0))
        self.assertEqual(native.clicks, [(761.5, 428.0)])

    def test_resize_after_capture_stops_before_input(self):
        backend, native = self.backend(frame(image_size=(923, 720)))
        guard = Mock()
        captured = backend.capture(guard)
        native.geometry = (100.0, -50.0, 1000.0, 748.0, 77)

        with self.assertRaisesRegex(RuntimeError, "窗口尺寸已变化"):
            backend.click((100, 100), captured, guard)

        guard.perform_input.assert_not_called()

    def test_transient_input_geometry_mismatch_is_rechecked_before_click(self):
        current = frame(image_size=(923, 720))
        native = SequenceGeometryNativeInput(
            (100.0, -50.0, 923.0, 748.0, 77),
            [],
        )
        backend = LegacyMacOSWindowBackend(
            target_id="local-primary",
            window_id=42,
            controller=Mock(),
            capture_driver=FakeCapture(current),
            native_input=native,
        )
        guard = Mock()
        guard.perform_input.side_effect = lambda function, *args: function(*args)
        captured = backend.capture(guard)
        native.later_geometries = [
            (100.0, -50.0, 1000.0, 748.0, 77),
            (100.0, -50.0, 923.0, 748.0, 77),
        ]

        result = backend.click((461.5, 360), captured, guard)

        self.assertEqual(result, (561.5, 338.0))
        self.assertEqual(native.clicks, [(561.5, 338.0)])

    def test_read_only_preview_maps_point_without_native_input(self):
        backend, native = self.backend(frame(image_size=(923, 720)))
        captured = backend.capture(Mock())

        result = backend.preview_point((461.5, 360), captured)

        self.assertEqual(result, (561.5, 338.0))
        self.assertEqual(native.clicks, [])
        self.assertEqual(native.drags, [])

    def test_resize_after_capture_invalidates_read_only_preview(self):
        backend, native = self.backend(frame(image_size=(923, 720)))
        captured = backend.capture(Mock())
        native.geometry = (100.0, -50.0, 1000.0, 748.0, 77)

        with self.assertRaisesRegex(RuntimeError, "窗口尺寸已变化"):
            backend.preview_point((100, 100), captured)

        self.assertEqual(native.clicks, [])
        self.assertEqual(native.drags, [])

    def test_drag_maps_both_points_from_same_frame(self):
        backend, native = self.backend(frame(image_size=(923, 720)))
        guard = Mock()
        guard.perform_input.side_effect = lambda function, *args: function(*args)
        captured = backend.capture(guard)

        backend.drag((100, 200), (300, 400), captured, guard)

        self.assertEqual(len(native.drags), 1)
        start, end = native.drags[0]
        self.assertAlmostEqual(start[0], 200.0)
        self.assertAlmostEqual(start[1], 178.0)
        self.assertAlmostEqual(end[0], 400.0)
        self.assertAlmostEqual(end[1], 378.0)


if __name__ == "__main__":
    unittest.main()
