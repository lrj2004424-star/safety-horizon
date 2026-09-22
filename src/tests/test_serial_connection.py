import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from safety_monitor.serial_connection import SerialConnection, select_uno, SILENT
from computer_buzzer_simulator import _run


class SerialConnectionTests(unittest.TestCase):
    def port(self, device="uno", vid=0x2341, pid=0x0043):
        return SimpleNamespace(device=device, vid=vid, pid=pid)

    def test_unknown_and_ambiguous_devices_are_not_selected(self):
        self.assertIsNone(select_uno([self.port(vid=123)]))
        self.assertIsNone(select_uno([self.port("one"), self.port("two")]))
        self.assertEqual(select_uno([self.port()]), "uno")

    def test_open_failure_is_retried_no_more_than_once_per_second(self):
        factory = Mock(side_effect=OSError("disconnected"))
        link = SerialConnection(factory, lambda: [self.port()])
        self.assertFalse(link.tick(0))
        self.assertFalse(link.tick(0.5))
        self.assertEqual(factory.call_count, 1)
        self.assertFalse(link.tick(1))
        self.assertEqual(factory.call_count, 2)

    def test_waits_for_reset_and_initializes_silently(self):
        device = Mock()
        device.write.side_effect = lambda data: len(data)
        link = SerialConnection(Mock(return_value=device), lambda: [self.port()])
        self.assertFalse(link.tick(0))
        device.write.assert_not_called()
        self.assertFalse(link.tick(1.9))
        self.assertTrue(link.tick(2))
        device.write.assert_called_once_with(SILENT)
        self.assertEqual(link.generation, 1)

    def test_write_failure_closes_then_reconnects_silently(self):
        first, second = Mock(), Mock()
        first.write.side_effect = [len(SILENT), OSError("unplugged")]
        second.write.side_effect = lambda data: len(data)
        link = SerialConnection(Mock(side_effect=[first, second]), lambda: [self.port()])
        link.tick(0)
        link.tick(2)
        self.assertFalse(link.send(b"command"))
        self.assertFalse(link.ready)
        first.close.assert_called_once()
        self.assertFalse(link.tick(3))
        self.assertTrue(link.tick(5))
        self.assertEqual(link.generation, 2)
        second.write.assert_called_once_with(SILENT)

    def test_short_write_is_connection_failure(self):
        device = Mock()
        device.write.return_value = 1
        link = SerialConnection(Mock(return_value=device), lambda: [self.port()])
        link.tick(0)
        self.assertFalse(link.tick(2))
        self.assertEqual(link.reason, "serial_disconnected")

    def test_unplug_does_not_replay_hardware_owned_review_on_speaker(self):
        args = SimpleNamespace(review_json="unused", output_json="unused", audio=False,
            audio_if_no_uno=True, max_age_s=1.5, poll_s=0.1)
        player = Mock()
        states = [("HIGH_RISK", 80, 3, "authorized", "decision1", True)] * 2
        running = Mock(side_effect=[True, True, False])
        with patch("computer_buzzer_simulator._read_state", side_effect=states), \
             patch("computer_buzzer_simulator._uno_present", side_effect=[True, False]), \
             patch("computer_buzzer_simulator._write_output"), \
             patch("computer_buzzer_simulator.time.sleep"):
            _run(args, player, running)
        player.play.assert_not_called()
