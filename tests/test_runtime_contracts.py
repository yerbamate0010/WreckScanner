import os
import sys
import unittest
from unittest.mock import patch

from core.runtime import (
    PYTHON_IO_ENCODING,
    TEXT_ENCODING,
    TEXT_ERRORS,
    configure_process_encoding,
    subprocess_text_kwargs,
)


class FakeTextStream:
    def __init__(self):
        self.reconfigure_calls = []

    def reconfigure(self, **kwargs):
        self.reconfigure_calls.append(kwargs)


class RuntimeEncodingContractTests(unittest.TestCase):
    def test_configure_process_encoding_forces_safe_text_streams(self):
        stdout = FakeTextStream()
        stderr = FakeTextStream()

        with (
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
            patch.dict(os.environ, {"PYTHONIOENCODING": "latin-1"}, clear=True),
        ):
            configure_process_encoding()

            self.assertEqual(os.environ["PYTHONIOENCODING"], PYTHON_IO_ENCODING)

        expected = {"encoding": TEXT_ENCODING, "errors": TEXT_ERRORS}
        self.assertEqual(stdout.reconfigure_calls, [expected])
        self.assertEqual(stderr.reconfigure_calls, [expected])

    def test_subprocess_text_kwargs_decode_utf8_with_replacement(self):
        self.assertEqual(
            subprocess_text_kwargs(),
            {"encoding": TEXT_ENCODING, "errors": TEXT_ERRORS},
        )


if __name__ == "__main__":
    unittest.main()
