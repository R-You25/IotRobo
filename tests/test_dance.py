import json
import sys
import types
import unittest
from unittest.mock import Mock, patch

try:
    import serial
except ModuleNotFoundError:
    sys.modules["serial"] = types.SimpleNamespace(Serial=object)

import arm_control


class DanceTests(unittest.TestCase):
    def setUp(self):
        self.angles = patch.dict(arm_control.current_angles)
        self.angles.start()
        self.addCleanup(self.angles.stop)

    def test_all_variants_through_dispatch(self):
        for variant in arm_control.DANCE_VARIANTS:
            with self.subTest(variant=variant.__name__):
                arm_control.current_angles.update({4: 60, 3: 80})
                ser = Mock()
                ser.readline.return_value = b'{"status":"ok"}\n'
                with patch.object(arm_control.random, "choice", return_value=variant) as choose:
                    with patch.object(arm_control.time, "sleep"):
                        result = json.loads(arm_control.dispatch_tool(ser, "dance", {}))
                choose.assert_called_once_with(arm_control.DANCE_VARIANTS)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["message"], variant.__name__ + " finished")
                self.assertEqual(result["angles"], {"right": 150, "left": 150})
                self.assertTrue(ser.write.called)

    def test_error_and_dynamic_channel_angles_are_preserved(self):
        def variant(ser):
            arm_control.current_angles.update({7: 113, 8: 47})
            return json.dumps({"status": "error", "message": "dance interrupted", "detail": "timeout"})

        with patch.dict(arm_control.CHANNELS, {"right": 7, "left": 8}):
            with patch.object(arm_control.random, "choice", return_value=variant):
                result = json.loads(arm_control.dance(Mock()))
        self.assertEqual(result, {
            "status": "error", "message": "dance interrupted", "detail": "timeout",
            "angles": {"right": 113, "left": 47},
        })

    def test_voice_prompt_includes_dance_results(self):
        prompt = arm_control.build_system_prompt_en()
        self.assertIn("raise_hands, dance,", prompt)
        self.assertIn("most recent tool result", prompt)


if __name__ == "__main__":
    unittest.main()
