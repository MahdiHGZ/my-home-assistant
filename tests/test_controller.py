"""Concurrency and timeout tests for the shared action controller."""

import threading
import time
import unittest

import main


class ControllerQueueTests(unittest.TestCase):
    def test_queued_action_is_cancelled_after_timeout(self):
        controller = main.Controller()
        blocker_started = threading.Event()
        release_blocker = threading.Event()
        executed = []

        def blocker():
            blocker_started.set()
            release_blocker.wait(2)

        first = threading.Thread(target=lambda: controller.run_action(blocker, timeout=2))
        first.start()
        self.assertTrue(blocker_started.wait(1))

        with self.assertRaises(main.ActionCancelledError) as caught:
            controller.run_action(lambda: executed.append("late"), timeout=0.01)
        release_blocker.set()
        first.join(1)
        time.sleep(0.02)

        self.assertEqual(executed, [])
        self.assertEqual(controller.job_state(caught.exception.job_id)["status"], "cancelled")

    def test_running_action_returns_observable_job_id(self):
        controller = main.Controller()
        started = threading.Event()
        release = threading.Event()

        def slow():
            started.set()
            release.wait(2)
            return "finished"

        with self.assertRaises(main.ActionInProgressError) as caught:
            controller.run_action(slow, timeout=0.01)
        self.assertTrue(started.is_set())
        self.assertEqual(controller.job_state(caught.exception.job_id)["status"], "running")

        release.set()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if controller.job_state(caught.exception.job_id)["status"] == "done":
                break
            time.sleep(0.005)
        self.assertEqual(controller.job_state(caught.exception.job_id)["status"], "done")


if __name__ == "__main__":
    unittest.main()
