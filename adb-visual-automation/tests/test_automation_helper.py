import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "automation_helper.py"
SPEC = importlib.util.spec_from_file_location("automation_helper", HELPER_PATH)
HELPER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(HELPER)


class AutomationHelperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state_dir = self.root / "state"
        self.template_path = self.root / "template.png"
        self.actions_path = self.root / "actions.json"
        self.screen_path = self.root / "screen.png"
        self.pattern = self.make_pattern()
        cv2.imwrite(str(self.template_path), self.pattern)
        self.template_hash = hashlib.sha256(self.template_path.read_bytes()).hexdigest()
        self.write_actions()

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def make_pattern():
        pattern = np.full((28, 32), 24, dtype=np.uint8)
        cv2.rectangle(pattern, (2, 2), (29, 25), 220, 2)
        cv2.line(pattern, (4, 22), (26, 5), 150, 3)
        cv2.circle(pattern, (12, 10), 5, 80, -1)
        pattern[6:9, 23:27] = 245
        return pattern

    def write_actions(self, threshold=0.8, min_margin=0.08):
        payload = {
            "schema_version": 1,
            "engine_version": "1.0.0",
            "actions": {
                "test.open": {
                    "package": "test.package",
                    "pre_page": "test.before",
                    "post_page": "test.after",
                    "vision_fallback": {
                        "enabled": True,
                        "target": "test_button",
                        "roi": [0.1, 0.55, 0.9, 0.95],
                        "tap_offset": [0.5, 0.5],
                        "min_area_ratio": 0.005,
                        "max_area_ratio": 0.1,
                        "min_evidence_count": 2,
                    },
                    "roi": [0.0, 0.5, 1.0, 1.0],
                    "threshold": threshold,
                    "min_margin": min_margin,
                    "tap_offset": [0.5, 0.5],
                    "scale_tolerance": 0.0,
                    "scale_step": 0.02,
                    "templates": [
                        {
                            "id": "test-template-v1",
                            "file": self.template_path.name,
                            "sha256": self.template_hash,
                            "base_width": 200,
                            "base_height": 400,
                            "rotation": 0,
                            "compatible_version_codes": ["1"],
                        }
                    ],
                }
            },
        }
        self.actions_path.write_text(json.dumps(payload), encoding="utf-8")

    def run_helper(self, command, expected_code=0):
        completed = subprocess.run(
            [
                sys.executable,
                str(HELPER_PATH),
                "--actions",
                str(self.actions_path),
                "--templates-dir",
                str(self.root),
                "--state-dir",
                str(self.state_dir),
                *command,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, expected_code, completed.stderr or completed.stdout)
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        return json.loads(completed.stdout)

    @staticmethod
    def context_args(screen_width=200, screen_height=400, input_width=400, input_height=800):
        return [
            "--action",
            "test.open",
            "--package",
            "test.package",
            "--version-name",
            "1.0",
            "--version-code",
            "1",
            "--screen-width",
            str(screen_width),
            "--screen-height",
            str(screen_height),
            "--input-width",
            str(input_width),
            "--input-height",
            str(input_height),
            "--orientation",
            "portrait",
            "--rotation",
            "0",
            "--pre-page",
            "test.before",
        ]

    def write_screen(self, positions):
        screen = np.full((400, 200), 8, dtype=np.uint8)
        for x, y in positions:
            screen[y : y + self.pattern.shape[0], x : x + self.pattern.shape[1]] = self.pattern
        cv2.imwrite(str(self.screen_path), screen)

    def test_context_key_changes_with_binding(self):
        base = {
            "action_id": "test.open",
            "package": "test.package",
            "version_name": "1.0",
            "version_code": "1",
            "screen_width": 200,
            "screen_height": 400,
            "input_width": 400,
            "input_height": 800,
            "orientation": "portrait",
            "rotation": 0,
            "pre_page": "test.before",
        }
        base_key = HELPER.context_key(base)
        for field, value in {
            "version_code": "2",
            "screen_width": 201,
            "input_height": 801,
            "rotation": 1,
            "pre_page": "test.other",
        }.items():
            changed = dict(base)
            changed[field] = value
            self.assertNotEqual(base_key, HELPER.context_key(changed), field)

    def test_match_returns_input_coordinate_from_original_png(self):
        self.write_screen([(80, 300)])
        result = self.run_helper(
            ["match", *self.context_args(), "--screen", str(self.screen_path)]
        )
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["source"], "opencv")
        self.assertEqual(result["coordinate"], {"x": 192, "y": 628})
        self.assertGreaterEqual(result["score"], 0.8)
        self.assertEqual(result["screenshot_bounds"]["x"], 80)
        self.assertEqual(result["screenshot_bounds"]["y"], 300)

    def test_match_rejects_ambiguous_candidates(self):
        self.write_screen([(30, 280), (130, 330)])
        result = self.run_helper(
            ["match", *self.context_args(), "--screen", str(self.screen_path)],
            expected_code=12,
        )
        self.assertEqual(result["status"], "ambiguous")
        self.assertNotIn("coordinate", result)

    def test_match_rejects_reported_screen_size_mismatch(self):
        self.write_screen([(80, 300)])
        result = self.run_helper(
            [
                "match",
                *self.context_args(screen_width=201),
                "--screen",
                str(self.screen_path),
            ],
            expected_code=20,
        )
        self.assertEqual(result["status"], "invalid_screen")
        self.assertNotIn("coordinate", result)

    def test_feedback_stores_then_invalidates_verified_coordinate(self):
        stored = self.run_helper(
            [
                "feedback",
                *self.context_args(),
                "--source",
                "opencv",
                "--result",
                "success",
                "--observed-page",
                "test.after",
                "--x",
                "192",
                "--y",
                "628",
                "--template-id",
                "test-template-v1",
                "--template-sha256",
                self.template_hash,
                "--score",
                "0.96",
                "--second-score",
                "0.5",
            ]
        )
        self.assertEqual(stored["status"], "stored")

        lookup = self.run_helper(["lookup", *self.context_args()])
        self.assertEqual(lookup["coordinate"], {"x": 192, "y": 628})

        refreshed = self.run_helper(
            [
                "feedback",
                *self.context_args(),
                "--source",
                "cache",
                "--result",
                "success",
                "--observed-page",
                "test.after",
                "--x",
                "192",
                "--y",
                "628",
            ]
        )
        self.assertEqual(refreshed["success_count"], 2)
        cache = json.loads((self.state_dir / "cache-v1.json").read_text(encoding="utf-8"))
        entry = next(iter(cache["entries"].values()))
        self.assertEqual(entry["source"], "opencv")
        self.assertEqual(entry["template_sha256"], self.template_hash)
        self.assertEqual(entry["score"], 0.96)

        invalidated = self.run_helper(
            [
                "feedback",
                *self.context_args(),
                "--source",
                "cache",
                "--result",
                "failure",
                "--observed-page",
                "test.wrong",
            ]
        )
        self.assertEqual(invalidated["status"], "invalidated")
        miss = self.run_helper(["lookup", *self.context_args()], expected_code=10)
        self.assertEqual(miss["status"], "miss")

    def test_action_config_change_invalidates_cached_coordinate(self):
        self.run_helper(
            [
                "feedback",
                *self.context_args(),
                "--source",
                "opencv",
                "--result",
                "success",
                "--observed-page",
                "test.after",
                "--x",
                "192",
                "--y",
                "628",
                "--template-id",
                "test-template-v1",
                "--template-sha256",
                self.template_hash,
                "--score",
                "0.96",
                "--second-score",
                "0.5",
            ]
        )
        payload = json.loads(self.actions_path.read_text(encoding="utf-8"))
        payload["actions"]["test.open"]["tap_offset"] = [0.75, 0.75]
        self.actions_path.write_text(json.dumps(payload), encoding="utf-8")

        miss = self.run_helper(["lookup", *self.context_args()], expected_code=10)
        self.assertEqual(miss["status"], "miss")

    def test_feedback_rejects_unverified_opencv_evidence(self):
        result = self.run_helper(
            [
                "feedback",
                *self.context_args(),
                "--source",
                "opencv",
                "--result",
                "success",
                "--observed-page",
                "test.after",
                "--x",
                "192",
                "--y",
                "628",
                "--template-id",
                "test-template-v1",
                "--template-sha256",
                "0" * 64,
                "--score",
                "0.96",
                "--second-score",
                "0.5",
            ],
            expected_code=20,
        )
        self.assertEqual(result["status"], "invalid_feedback")
        self.assertFalse((self.state_dir / "cache-v1.json").exists())

    def test_vision_target_validates_allowlist_roi_and_feedback(self):
        self.write_screen([(80, 300)])
        found = self.run_helper(
            [
                "vision-target",
                *self.context_args(),
                "--screen",
                str(self.screen_path),
                "--target",
                "test_button",
                "--x1",
                "0.3",
                "--y1",
                "0.7",
                "--x2",
                "0.5",
                "--y2",
                "0.8",
                "--evidence-count",
                "2",
            ]
        )
        self.assertEqual(found["status"], "found")
        self.assertEqual(found["source"], "ai_vision")
        self.assertEqual(found["coordinate"], {"x": 160, "y": 600})

        stored = self.run_helper(
            [
                "feedback",
                *self.context_args(),
                "--source",
                "ai_vision",
                "--result",
                "success",
                "--observed-page",
                "test.after",
                "--x",
                "160",
                "--y",
                "600",
                "--screen",
                str(self.screen_path),
                "--screen-sha256",
                found["screen_sha256"],
                "--vision-target",
                "test_button",
                "--x1",
                "0.3",
                "--y1",
                "0.7",
                "--x2",
                "0.5",
                "--y2",
                "0.8",
                "--evidence-count",
                "2",
            ]
        )
        self.assertEqual(stored["status"], "stored")
        lookup = self.run_helper(["lookup", *self.context_args()])
        self.assertEqual(lookup["coordinate"], {"x": 160, "y": 600})

    def test_vision_target_rejects_wrong_target_and_outside_roi(self):
        self.write_screen([(80, 300)])
        wrong = self.run_helper(
            [
                "vision-target",
                *self.context_args(),
                "--screen",
                str(self.screen_path),
                "--target",
                "dangerous_button",
                "--x1",
                "0.3",
                "--y1",
                "0.7",
                "--x2",
                "0.5",
                "--y2",
                "0.8",
            ],
            expected_code=20,
        )
        self.assertEqual(wrong["status"], "invalid_vision_target")
        outside = self.run_helper(
            [
                "vision-target",
                *self.context_args(),
                "--screen",
                str(self.screen_path),
                "--target",
                "test_button",
                "--x1",
                "0.3",
                "--y1",
                "0.3",
                "--x2",
                "0.5",
                "--y2",
                "0.4",
            ],
            expected_code=13,
        )
        self.assertEqual(outside["status"], "vision_outside_safe_roi")

    def test_engine_version_mismatch_is_rejected(self):
        payload = json.loads(self.actions_path.read_text(encoding="utf-8"))
        payload["engine_version"] = "2.0.0"
        self.actions_path.write_text(json.dumps(payload), encoding="utf-8")

        result = self.run_helper(["doctor"], expected_code=20)
        self.assertEqual(result["status"], "incompatible_engine")

    def test_atomic_write_never_leaves_temporary_file(self):
        target = self.state_dir / "cache-v1.json"
        HELPER.atomic_write_json(target, {"schema_version": 1, "entries": {"a": {"active": True}}})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["entries"]["a"]["active"], True)
        self.assertEqual(list(self.state_dir.glob(".cache-v1.json.*")), [])

    def test_qrcode_geometry_detection(self):
        if not hasattr(cv2, "QRCodeEncoder_create"):
            self.skipTest("OpenCV build has no QRCodeEncoder")
        encoder = cv2.QRCodeEncoder_create()
        qr = encoder.encode("adb-visual-automation-test")
        qr = cv2.resize(qr, (300, 300), interpolation=cv2.INTER_NEAREST)
        canvas = np.full((600, 400), 255, dtype=np.uint8)
        canvas[150:450, 50:350] = qr
        qr_path = self.root / "qr.png"
        cv2.imwrite(str(qr_path), canvas)
        result = self.run_helper(["verify-qrcode", "--image", str(qr_path)])
        self.assertEqual(result["status"], "found")
        self.assertGreater(result["area_ratio"], 0.05)

    def test_qrcode_uses_later_valid_detection_variant(self):
        if not hasattr(cv2, "QRCodeEncoder_create"):
            self.skipTest("OpenCV build has no QRCodeEncoder")
        encoder = cv2.QRCodeEncoder_create()
        qr = encoder.encode("adb-visual-later-valid-variant")
        qr = cv2.resize(qr, (300, 300), interpolation=cv2.INTER_NEAREST)
        canvas = np.zeros((600, 400), dtype=np.uint8)
        canvas[150:450, 50:350] = cv2.bitwise_not(qr)
        qr_path = self.root / "qr-later-valid.png"
        cv2.imwrite(str(qr_path), canvas)

        original_detector = HELPER.import_cv

        class Detector:
            def __init__(self):
                self.calls = 0
                self.real = cv2.QRCodeDetector()

            def detect(self, image):
                self.calls += 1
                if self.calls == 1:
                    return True, np.array([[[20, 20], [380, 20], [220, 50], [20, 580]]], dtype=np.float32)
                return self.real.detect(image)

        class CVProxy:
            def __getattr__(self, name):
                if name == "QRCodeDetector":
                    return Detector
                return getattr(cv2, name)

        HELPER.import_cv = lambda: (CVProxy(), np)
        try:
            args = type("Args", (), {"image": qr_path})()
            with self.assertRaises(SystemExit) as raised:
                HELPER.command_verify_qrcode(args)
            self.assertEqual(raised.exception.code, 0)
        finally:
            HELPER.import_cv = original_detector

    def test_qrcode_inverted_geometry_detection(self):
        if not hasattr(cv2, "QRCodeEncoder_create"):
            self.skipTest("OpenCV build has no QRCodeEncoder")
        encoder = cv2.QRCodeEncoder_create()
        qr = encoder.encode("adb-visual-inverted-test")
        qr = cv2.resize(qr, (300, 300), interpolation=cv2.INTER_NEAREST)
        canvas = np.zeros((600, 400), dtype=np.uint8)
        canvas[150:450, 50:350] = cv2.bitwise_not(qr)
        qr_path = self.root / "qr-inverted.png"
        cv2.imwrite(str(qr_path), canvas)
        result = self.run_helper(["verify-qrcode", "--image", str(qr_path)])
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["detection_variant"], "inverted")
        self.assertGreater(result["area_ratio"], 0.05)


if __name__ == "__main__":
    unittest.main()
