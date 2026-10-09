from pathlib import Path
import tempfile
import unittest
from dorsopathy_platform.runtime import RuntimeLock

class RuntimeLockTests(unittest.TestCase):
    def test_active_controller_is_exclusive_and_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            first=RuntimeLock(Path(tmp))
            try:
                with self.assertRaises(ValueError):RuntimeLock(Path(tmp))
            finally:first.close()
            second=RuntimeLock(Path(tmp));second.close();second.close()
    def test_lock_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/'target';target.write_text('unchanged')
            (root/'.runtime.lock').symlink_to(target)
            with self.assertRaises(OSError):RuntimeLock(root)
            self.assertEqual(target.read_text(),'unchanged')
