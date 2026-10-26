"""Unit tests for the pure engines: FSP ID, protocol log parser, rating engine.  Run: py -m unittest -v"""
import unittest

from competition_service.log_parser import parse_log, rank_manual
from competition_service.rating import RatingSettings, compute_rating
from shared.fsp_id import is_valid_fsp_id, make_fsp_id

A, B, C = make_fsp_id(2026, 1), make_fsp_id(2026, 2), make_fsp_id(2026, 3)


def row(fid, cid, level, place, status="OK", name=None):
    return {"fsp_id": fid, "full_name": name or fid, "competition_id": cid, "competition_code": f"C{cid}",
            "title": f"Соревнование {cid}", "level": level, "date": f"2026-0{cid}-01", "place": place, "status": status}


class FspIdTest(unittest.TestCase):
    def test_roundtrip_and_check_digit(self):
        fid = make_fsp_id(2026, 42)
        self.assertRegex(fid, r"^FSP-56-26-00042-\d$")
        self.assertTrue(is_valid_fsp_id(fid))
        broken = fid[:-1] + str((int(fid[-1]) + 1) % 10)
        self.assertFalse(is_valid_fsp_id(broken))

    def test_other_region(self):
        self.assertTrue(is_valid_fsp_id(make_fsp_id(2025, 7, region="77")))


class LogParserTest(unittest.TestCase):
    def test_icpc_ranking_penalty_and_ties(self):
        log = f"""#! FSP-LOG/1 mode=icpc penalty=20
00:00:00 JOIN {C}
00:10:00 RUN {A} A WA
00:20:00 RUN {A} A OK
00:30:00 RUN {A} B OK
00:15:00 RUN {B} A OK
00:55:00 RUN {B} B CE
00:55:00 RUN {B} B OK
"""
        p = parse_log(log)
        by = {e["fsp_id"]: e for e in p.entries}
        self.assertEqual(by[A]["penalty"], 20 + 20 + 30)   # AC@20 + 1 WA*20 + AC@30
        self.assertEqual(by[B]["penalty"], 15 + 55)        # CE is not penalised
        self.assertEqual((by[A]["place"], by[B]["place"]), (1, 1))  # same solved & penalty → shared place
        self.assertEqual(by[C]["place"], 3)                # joined, solved nothing, still ranked
        self.assertEqual(p.warnings, [])

    def test_dsq_and_invalid_ids(self):
        log = f"""#! FSP-LOG/1 mode=score
00:00:00 SCORE {A} 10.5
00:00:00 SCORE {A} 20
00:00:00 SCORE {B} 40
00:30:00 DSQ {B} "Нарушение регламента"
00:00:00 SCORE FSP-56-26-00001-0 99
garbage line
"""
        p = parse_log(log)
        self.assertEqual([e["fsp_id"] for e in p.entries], [A, B])
        self.assertEqual(p.entries[0]["score"], 30.5)
        self.assertEqual(p.entries[0]["place"], 1)
        self.assertEqual((p.entries[1]["status"], p.entries[1]["place"]), ("DSQ", None))
        self.assertEqual(len(p.warnings), 2)


class ManualResultsTest(unittest.TestCase):
    def test_icpc_totals_ranked_with_shared_places_and_dsq_last(self):
        D = make_fsp_id(2026, 4)
        entries = rank_manual([
            {"fsp_id": A, "solved": 5, "penalty": 410},
            {"fsp_id": B, "solved": 6, "penalty": 700},
            {"fsp_id": C, "solved": 5, "penalty": 410},
            {"fsp_id": D, "solved": 7, "penalty": 100, "dsq": True},
        ], "icpc")
        self.assertEqual([(e["fsp_id"], e["place"]) for e in entries], [(B, 1), (A, 2), (C, 2), (D, None)])
        self.assertEqual((entries[-1]["status"], entries[-1]["dsq_reason"]), ("DSQ", "Дисквалификация"))

    def test_score_mode_same_shape_as_log(self):
        entries = rank_manual([{"fsp_id": A, "score": 71.5}, {"fsp_id": B, "score": "88.25"}], "score")
        self.assertEqual([(e["fsp_id"], e["place"], e["score"]) for e in entries], [(B, 1, 88.25), (A, 2, 71.5)])
        self.assertEqual(set(entries[0]), set(parse_log(f"00:00:00 SCORE {A} 1").entries[0]))


class RatingTest(unittest.TestCase):
    def test_stability_coefficient_applied_with_two_podiums(self):
        res = compute_rating([row(A, 1, "REGIONAL", 1), row(A, 2, "INTERREG", 2), row(A, 3, "REGIONAL", 5)], RatingSettings())
        a = res["rows"][0]
        self.assertEqual(a["base_total"], 40 + 45 + 0)
        self.assertTrue(a["coefficient_applied"])
        self.assertEqual(a["final"], 102.0)
        self.assertEqual(a["bonus"], 17.0)

    def test_single_podium_no_coefficient(self):
        a = compute_rating([row(A, 1, "CHAMP_RU", 1), row(A, 2, "REGIONAL", 4)], RatingSettings())["rows"][0]
        self.assertFalse(a["coefficient_applied"])
        self.assertEqual(a["final"], 100.0)

    def test_dsq_scores_zero_and_is_not_a_podium(self):
        a = compute_rating([row(A, 1, "REGIONAL", 1), row(A, 2, "REGIONAL", None, "DSQ")], RatingSettings())["rows"][0]
        self.assertEqual((a["base_total"], a["podiums"], a["coefficient_applied"]), (40, 1, False))

    def test_sanctioned_athletes_excluded(self):
        res = compute_rating([row(A, 1, "REGIONAL", 1), row(B, 1, "REGIONAL", 2)], RatingSettings(), excluded={A: "Дисквалификация"})
        self.assertEqual([r["fsp_id"] for r in res["rows"]], [B])
        self.assertEqual(res["rows"][0]["position"], 1)
        self.assertEqual(res["excluded"][0]["excluded_reason"], "Дисквалификация")

    def test_other_regions_not_rated_and_ties_share_position(self):
        ext = make_fsp_id(2025, 300, region="77")
        res = compute_rating([row(ext, 1, "CHAMP_RU", 1), row(A, 1, "CHAMP_RU", 2, name="Б"), row(B, 2, "CHAMP_RU", 2, name="А")], RatingSettings())
        self.assertNotIn(ext, [r["fsp_id"] for r in res["rows"]])
        self.assertEqual([r["position"] for r in res["rows"]], [1, 1])

    def test_custom_settings(self):
        s = RatingSettings(points={"REGIONAL": [10, 5, 1]}, coefficient=1.5, min_podiums=1)
        self.assertEqual(compute_rating([row(A, 1, "REGIONAL", 2)], s)["rows"][0]["final"], 7.5)


if __name__ == "__main__":
    unittest.main()
