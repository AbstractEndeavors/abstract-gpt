"""`abstract-gpt serve` is abstract-serve-core's one Serve console (the same
program as abstract-claude / hugpy-agent serve), and abstract-gpt registers GPT
among its providers."""
import os
import sys
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from abstract_gpt import cli  # noqa: E402


class ServeIsTheSharedConsole(unittest.TestCase):
    def test_serve_runs_the_shared_console_with_gpt_first(self):
        with patch("abstract_serve.serve_cli.main", return_value=0) as serve_main, \
                patch.object(sys, "argv", ["abstract-gpt", "serve", "--no-browser"]), \
                patch.dict(os.environ, {}, clear=False):
            self.assertEqual(cli.main(), 0)
            self.assertEqual(os.environ["AC_SERVE_BACKEND"], "gpt")
        serve_main.assert_called_once_with(["--host", "127.0.0.1", "--port", "9124", "--no-browser"])

    def test_registered_as_a_serve_provider_without_requiring_claude(self):
        meta = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())["project"]
        self.assertEqual(meta["entry-points"]["abstract_serve.providers"], {"gpt": "abstract_serve.providers:gpt"})
        self.assertFalse([d for d in meta["dependencies"] if d.startswith("abstract-claude")])


if __name__ == "__main__":
    unittest.main()
