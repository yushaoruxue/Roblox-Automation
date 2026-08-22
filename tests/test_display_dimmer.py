import pathlib
import sys
import unittest


PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from display_dimmer import build_dimmed_ramp


class GammaRampTests(unittest.TestCase):
    def test_normal_level_preserves_original_ramp(self):
        original = [round(index * 65535 / 255) for index in range(256)] * 3
        self.assertEqual(build_dimmed_ramp(original, 100), original)

    def test_darkest_level_keeps_windows_compatible_endpoint(self):
        original = [round(index * 65535 / 255) for index in range(256)] * 3
        ramp = build_dimmed_ramp(original, 0)
        for channel in range(3):
            offset = channel * 256
            self.assertEqual(ramp[offset], 0)
            self.assertEqual(ramp[offset + 255], 32768)
            self.assertLess(ramp[offset + 128], 9000)
            self.assertEqual(
                ramp[offset : offset + 256],
                sorted(ramp[offset : offset + 256]),
            )

    def test_level_is_clamped(self):
        original = [round(index * 65535 / 255) for index in range(256)] * 3
        self.assertEqual(
            build_dimmed_ramp(original, -10),
            build_dimmed_ramp(original, 0),
        )
        self.assertEqual(
            build_dimmed_ramp(original, 150),
            build_dimmed_ramp(original, 100),
        )


if __name__ == "__main__":
    unittest.main()
