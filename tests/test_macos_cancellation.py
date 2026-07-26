import signal
import unittest

from tools.macos_cancellation import CancellationGuard, CancellationRequested


class FakeTasker:
    def __init__(self):
        self.stop_calls = 0
        self.stop_job = FakeStopJob()

    def post_stop(self):
        self.stop_calls += 1
        return self.stop_job


class FakeStopJob:
    def __init__(self):
        self.wait_calls = 0

    def wait(self):
        self.wait_calls += 1


class CancellationGuardTests(unittest.TestCase):
    def test_stop_calls_tasker_once_and_blocks_future_input(self):
        tasker = FakeTasker()
        guard = CancellationGuard()
        guard.bind_tasker(tasker)
        calls = []
        guard.perform_input(calls.append, "before")

        guard.request_stop()
        guard.request_stop()
        guard.wait_for_tasker_stop()

        self.assertEqual(tasker.stop_calls, 1)
        self.assertEqual(tasker.stop_job.wait_calls, 1)
        with self.assertRaises(CancellationRequested):
            guard.perform_input(calls.append, "after")
        self.assertEqual(calls, ["before"])

    def test_tasker_bound_after_stop_is_stopped_immediately(self):
        guard = CancellationGuard()
        guard.request_stop()
        tasker = FakeTasker()
        guard.bind_tasker(tasker)
        self.assertEqual(tasker.stop_calls, 1)

    def test_signal_handler_stops_tasker_and_raises(self):
        tasker = FakeTasker()
        guard = CancellationGuard()
        guard.bind_tasker(tasker)
        with self.assertRaises(CancellationRequested):
            guard._handle_signal(signal.SIGTERM, None)
        self.assertTrue(guard.cancelled)
        self.assertEqual(tasker.stop_calls, 1)


if __name__ == "__main__":
    unittest.main()
