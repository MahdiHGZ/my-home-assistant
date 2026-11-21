from pathlib import Path
import tempfile
import unittest

from cleaning_air import CleaningAir
from household import Household
from xiaomi_vacuum_utils import Status


class CleaningAirTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.household = Household(Path(directory.name) / "household.sqlite")
        self.purifier = {"power": False, "mode": 1}
        self.online = True
        self.writes = []
        self.routine = self._load()

    def _load(self):
        routine = CleaningAir(self.household)
        routine.purifier_state = lambda: self.purifier.copy() if self.online else None
        routine.write = self._write
        return routine

    def _write(self, name, value):
        self.writes.append((name, value))
        self.purifier[name] = value
        return True

    def arm(self, seconds=15):
        self.routine.configure({"enabled": True, "mode": "auto", "seconds": seconds})
        self.routine.observe(Status.CHARGING, 0)

    def begin(self):
        for now in (5, 10, 15):
            self.routine.observe(Status.SWEEPING, now)

    def test_opt_in_observes_transition_and_restores_after_docking(self):
        self.routine.observe(Status.SWEEPING, -5)
        self.assertEqual(self.writes, [])
        self.arm()
        self.begin()
        self.routine.observe(Status.GO_CHARGING, 20)
        self.routine.observe(Status.CHARGING, 30)
        self.assertEqual(self.writes, [("power", True), ("mode", 0)])
        for now in (35, 40, 45):
            self.routine.observe(Status.CHARGING, now)
        self.assertEqual(self.writes, [("power", True), ("mode", 0), ("mode", 1), ("power", False)])
        self.assertEqual(self.routine.status()["phase"], "armed")

    def test_missed_observation_restart_and_manual_setting_win(self):
        self.arm(seconds=0)
        self.routine.observe(Status.SWEEPING, 5)
        self.routine = self._load()
        self.routine.observe(None, 8)
        self.routine.observe(Status.SWEEPING, 10)
        self.routine.observe(Status.SWEEPING, 15)
        self.purifier["mode"] = 2
        self.routine.observe(Status.SWEEPING_AND_MOPPING, 20)
        self.assertEqual(self.routine.status()["phase"], "manual")
        self.routine.observe(Status.IDLE, 25)
        self.routine.observe(Status.IDLE, 30)
        self.assertEqual(self.writes, [("power", True), ("mode", 0), ("power", False)])
        self.assertEqual(self.purifier["mode"], 2)

    def test_offline_purifier_recovers_without_repeating_writes(self):
        self.arm(seconds=0)
        self.online = False
        self.routine.observe(Status.SWEEPING, 5)
        self.assertEqual(self.routine.status()["connection"], "purifier_offline")
        self.online = True
        self.begin()
        self.online = False
        self.routine.observe(Status.PAUSED, 20)
        self.online = True
        for now in (25, 30, 35):
            self.routine.observe(Status.PAUSED, now)
        self.assertEqual(self.writes, [("power", True), ("mode", 0), ("mode", 1), ("power", False)])

    def test_repeated_cleaning_during_settle_resets_timer(self):
        self.arm()
        self.begin()
        self.routine.observe(Status.GO_CHARGING, 20)
        self.routine.observe(None, 35)
        self.routine.observe(Status.MOPPING, 40)
        self.routine.observe(Status.IDLE, 45)
        self.routine.observe(Status.IDLE, 55)
        self.assertEqual(self.writes, [("power", True), ("mode", 0)])
        self.routine.observe(Status.IDLE, 60)
        self.assertEqual(self.writes[-1], ("mode", 1))

    def test_restart_does_not_replay_an_ambiguous_command(self):
        self.arm()
        def uncertain(name, value):
            self.writes.append((name, value))
            raise TimeoutError
        self.routine.write = uncertain
        self.routine.observe(Status.SWEEPING, 5)
        self.routine = self._load()
        self.routine.observe(Status.SWEEPING, 10)
        self.routine.observe(Status.SWEEPING, 15)
        self.assertEqual(self.writes, [("power", True)])

    def test_enabling_during_running_does_not_trigger_until_next_start(self):
        self.routine.configure({"enabled": True, "mode": "auto", "seconds": 0})
        self.begin()
        self.assertEqual(self.writes, [])
        self.routine.observe(Status.CHARGING, 20)
        self.routine.observe(Status.SWEEPING, 25)
        self.assertEqual(self.writes, [("power", True)])
