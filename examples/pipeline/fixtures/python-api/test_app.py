import os
import tempfile
import unittest
from pathlib import Path

from app import record_work, revision


class ServiceTest(unittest.TestCase):
    def test_work_is_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["DATA_DIR"] = directory
            record_work()
            record_work()
            self.assertEqual(len((Path(directory) / "activity.jsonl").read_text().splitlines()), 2)

    def test_revision(self):
        os.environ["SOURCE_REVISION"] = "exact-commit"
        self.assertEqual(revision(), "exact-commit")
