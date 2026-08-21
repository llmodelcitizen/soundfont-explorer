import json
import unittest

from sfr.worker import UploadJob


class TestUploadJob(unittest.TestCase):
    def test_parse(self):
        u = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
        j = UploadJob.parse(json.dumps({"ulid": u, "midi_key": f"uploads/{u}/song.mid", "email": "a@b.c"}))
        self.assertEqual(j.public_prefix, f"u/{u}/")
        for bad in ({"ulid": "nope"}, {"ulid": u, "midi_key": "uploads/x/song.mid", "email": "a@b"},
                    {"ulid": u, "midi_key": f"uploads/{u}/song.mid", "email": "nope"}):
            with self.assertRaises(ValueError):
                UploadJob.parse(json.dumps(bad))


if __name__ == "__main__":
    unittest.main()
