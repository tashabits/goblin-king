import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path

from app import record_work, revision


class ServiceTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX termination signal")
    def test_service_and_background_exit_cleanly(self):
        for arguments in ([], ["--background"]):
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as directory:
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).with_name("app.py")), *arguments],
                    env=os.environ | {"DATA_DIR": directory},
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                )
                try:
                    deadline = time.monotonic() + 5
                    evidence = Path(directory) / (
                        "activity.jsonl" if arguments else "sentinel"
                    )
                    while not evidence.exists() or not evidence.stat().st_size:
                        self.assertIsNone(process.poll(), "service exited before readiness")
                        self.assertLess(time.monotonic(), deadline, "service did not become ready")
                        if not arguments:
                            connection = HTTPConnection("127.0.0.1", 8080, timeout=0.2)
                            try:
                                connection.request("GET", "/health")
                                self.assertEqual(connection.getresponse().status, 200)
                            except OSError:
                                pass
                            finally:
                                connection.close()
                        time.sleep(0.02)
                    process.terminate()
                    self.assertEqual(process.wait(timeout=2), 0)
                    self.assertTrue(evidence.read_text().strip())
                    if arguments:
                        for line in evidence.read_text().splitlines():
                            self.assertIn("revision", json.loads(line))
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=2)

    def test_work_is_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["DATA_DIR"] = directory
            record_work()
            record_work()
            self.assertEqual(len((Path(directory) / "activity.jsonl").read_text().splitlines()), 2)

    def test_revision(self):
        os.environ["SOURCE_REVISION"] = "exact-commit"
        self.assertEqual(revision(), "exact-commit")
