from pathlib import Path
import unittest

from r2_local_fs.paths import key_to_relative_path, relative_path_to_key


class PathMappingTests(unittest.TestCase):
    def test_key_to_relative_path(self) -> None:
        self.assertEqual(
            key_to_relative_path("blog/2026/may/foo.json"),
            Path("blog/2026/may/foo.json"),
        )

    def test_relative_path_to_key(self) -> None:
        self.assertEqual(
            relative_path_to_key(Path("images/logo file.png")),
            "images/logo file.png",
        )

    def test_rejects_parent_segments(self) -> None:
        with self.assertRaises(ValueError):
            key_to_relative_path("../secret.txt")
        with self.assertRaises(ValueError):
            relative_path_to_key(Path("..") / "secret.txt")


if __name__ == "__main__":
    unittest.main()
