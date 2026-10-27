"""Файл положения о соревновании: загрузка федерацией, скачивание, замена, удаление и проверки файла (competition_service)."""
import base64
import unittest

from fastapi.testclient import TestClient

from competition_service import main as competition, regulation_files
from shared.demo_roster import ROSTER_BY_SEQ

ADMIN = ROSTER_BY_SEQ[1]["fsp_id"]
PDF = b"%PDF-1.4 regulation"


def body(name="polozhenie.pdf", data=PDF):
    return {"filename": name, "content_b64": base64.b64encode(data).decode(), "actor": ADMIN}


class RegulationFileTest(unittest.TestCase):
    def setUp(self):
        competition.store.__init__()
        self.ctx = TestClient(competition.app)
        self.api = self.ctx.__enter__()
        self.cid = self.api.get("/api/v1/competitions").json()[0]["id"]
        self.url = f"/api/v1/competitions/{self.cid}/regulation-file"

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def test_no_file_by_default_and_unknown_competition_is_404(self):
        self.assertIsNone(self.api.get(self.url).json())
        self.assertEqual(self.api.get(self.url + "/content").status_code, 404)
        self.assertEqual(self.api.get("/api/v1/competitions/99999/regulation-file").status_code, 404)
        self.assertEqual(self.api.put("/api/v1/competitions/99999/regulation-file", json=body()).status_code, 404)

    def test_upload_then_download_roundtrip_and_meta_hides_content(self):
        meta = self.api.put(self.url, json=body()).json()
        self.assertEqual((meta["filename"], meta["size"], meta["uploaded_by"]), ("polozhenie.pdf", len(PDF), ADMIN))
        self.assertNotIn("content_b64", meta)
        self.assertNotIn("content_b64", self.api.get(self.url).json())
        got = self.api.get(self.url + "/content").json()
        self.assertEqual((base64.b64decode(got["content_b64"]), got["media_type"]), (PDF, "application/pdf"))

    def test_new_upload_replaces_the_old_file(self):
        self.api.put(self.url, json=body("old.pdf"))
        self.api.put(self.url, json=body("new.docx", b"PK new"))
        self.assertEqual(self.api.get(self.url).json()["filename"], "new.docx")
        self.assertEqual(len(competition.store.ext.table(regulation_files.TABLE)), 1)
        self.assertTrue(any("Заменено положение" in a["action"] for a in competition.store.audit))

    def test_files_are_per_competition(self):
        other = next(c["id"] for c in self.api.get("/api/v1/competitions").json() if c["id"] != self.cid)
        self.api.put(self.url, json=body())
        self.assertIsNone(self.api.get(f"/api/v1/competitions/{other}/regulation-file").json())

    def test_only_pdf_and_docx_within_size_limit(self):
        for name in ("virus.exe", "page.html", "noext"):
            self.assertEqual(self.api.put(self.url, json=body(name)).status_code, 422, name)
        self.assertEqual(self.api.put(self.url, json=body(data=b"")).status_code, 422)
        too_big = body(data=b"x" * (regulation_files.MAX_FILE_BYTES + 1))
        self.assertEqual(self.api.put(self.url, json=too_big).status_code, 422)
        self.assertEqual(self.api.put(self.url, json={**body(), "content_b64": "не base64!"}).status_code, 422)
        self.assertIsNone(self.api.get(self.url).json())

    def test_delete_removes_the_file_once(self):
        self.api.put(self.url, json=body())
        self.assertEqual(self.api.delete(self.url, params={"actor": ADMIN}).status_code, 200)
        self.assertIsNone(self.api.get(self.url).json())
        self.assertEqual(self.api.delete(self.url, params={"actor": ADMIN}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
