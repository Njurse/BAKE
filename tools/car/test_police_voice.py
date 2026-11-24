"""LUMP_CAR_VOICE: handling.policeVoice -> one byte per model slot."""
import struct
import unittest

import bankjson
import exportd2


def _cars(**voices):
    return {name: {"handling": ({"policeVoice": v} if v is not None else {})}
            for name, v in voices.items()}


class PoliceVoiceTest(unittest.TestCase):
    def test_absent_writes_nothing(self):
        self.assertIsNone(exportd2.build_voice_body([(1, "a")], _cars(a=None)))

    def test_own_is_absent(self):
        self.assertIsNone(exportd2.build_voice_body([(1, "a")], _cars(a="OWN")))

    def test_cities_and_host(self):
        order = [(1, "a"), (3, "b"), (9, "c")]
        body = exportd2.build_voice_body(order, _cars(a="havana", b="RIO", c="HOST"))
        self.assertEqual(len(body), 16)
        self.assertEqual(body[1], bankjson.POLICE_VOICE_CITIES["HAVANA"])
        self.assertEqual(body[3], bankjson.POLICE_VOICE_CITIES["RIO"])
        self.assertEqual(body[9], bankjson.POLICE_VOICE_HOST)
        self.assertEqual(exportd2._voice_label(body[3]), "RIO")

    def test_bad_value_fails(self):
        with self.assertRaises(bankjson.BankError):
            exportd2.build_voice_body([(1, "a")], _cars(a="MIAMI"))

    def test_segment_in_bank(self):
        body = exportd2.build_voice_body([(2, "a")], _cars(a="VEGAS"))
        blob = exportd2.assemble_bank(b"m", b"", b"", [], voice_body=body)
        self.assertIn(struct.pack("<ii", exportd2.LUMP_CAR_VOICE, len(body)), blob)


if __name__ == "__main__":
    unittest.main()
