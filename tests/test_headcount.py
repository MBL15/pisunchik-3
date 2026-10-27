"""Отчёт о численности занимающихся: динамика по годам, разрезы на отчётную дату, фильтры."""
import unittest
from datetime import date, datetime

from fastapi.testclient import TestClient

from shared.fsp_id import make_fsp_id
from sportsman_service import main as sportsman
from sportsman_service.headcount import build_report
from sportsman_service.models import PersonRecord, RankRecord

TODAY = date(2026, 10, 27)
NOW = datetime(2026, 1, 1)


def person(seq, birth, start_year, disciplines, gender="М", roles=("athlete",), municipality="г. Оренбург",
           organization="Без организации", rank=None):
    return PersonRecord(fsp_id=make_fsp_id(2025, seq), seq=seq, last_name=f"Фамилия{seq}", first_name="Имя", gender=gender,
                        birth_date=date.fromisoformat(birth), municipality=municipality, organization=organization,
                        roles=list(roles), disciplines=disciplines, start_year=start_year, rank=rank,
                        pd_consent_at=NOW, created_at=NOW)


A = person(1, "2008-05-21", 2024, ["ALG"])
B = person(2, "2012-03-01", 2025, ["ALG", "SEC"], gender="Ж", municipality="г. Орск",
           rank=RankRecord(code="2", assigned_at=date(2025, 6, 1), expires_at=date(2027, 6, 1), order_no="1"))
C = person(3, "2000-01-10", 2026, ["ROB"], municipality="г. Орск")
TRAINER = person(4, "1980-01-01", 2020, ["ALG"], roles=("trainer",))


def rows(report, key):
    cut = next(c for c in report["cuts"] if c["key"] == key)
    return {r["label"]: r["counts"] for r in cut["rows"]}


class HeadcountReportTest(unittest.TestCase):
    def setUp(self):
        self.report = build_report([A, B, C, TRAINER], TODAY, years=3)

    def test_totals_dynamics_and_new_members_per_year(self):
        r = self.report
        self.assertEqual((r["years"], r["as_of"][-1]), ([2024, 2025, 2026], "2026-10-27"))
        self.assertEqual(r["as_of"][0], "2024-12-31")
        self.assertEqual(r["total"], {"counts": [1, 2, 3], "delta": 2, "delta_pct": 200.0, "joined": [1, 1, 1]})  # тренер не в счёт

    def test_discipline_cut_counts_every_discipline_of_an_athlete(self):
        self.assertEqual(rows(self.report, "discipline"),
                         {"Алгоритмика": [1, 2, 2], "Кибербезопасность": [0, 1, 1], "Робототехника": [0, 0, 1]})

    def test_age_is_taken_on_the_reporting_date(self):
        age = rows(self.report, "age")
        self.assertEqual(age["14–17 лет"], [1, 1, 1])  # 2024: Абрамов 16; 2025: он же 17; 2026: Б. исполнилось 14, А. уже 18
        self.assertEqual(age["10–13 лет"], [0, 1, 0])
        self.assertEqual(age["18–21 год"], [0, 0, 1])
        self.assertEqual(age["22 года и старше"], [0, 0, 1])

    def test_rank_counts_only_ranks_already_assigned_and_valid(self):
        ranks = rows(self.report, "rank")
        self.assertEqual(ranks["II разряд"], [0, 1, 1])
        self.assertEqual(ranks["без разряда"], [1, 1, 2])
        expired = build_report([B], date(2027, 7, 1), years=2)  # срок разряда вышел 2027-06-01
        self.assertEqual(rows(expired, "rank")["без разряда"], [0, 1])

    def test_filters_and_ordering_of_rows(self):
        orsk = build_report([A, B, C], TODAY, municipality="г. Орск")
        self.assertEqual(orsk["total"]["counts"], [0, 1, 2])
        self.assertEqual(list(rows(self.report, "municipality")), ["г. Орск", "г. Оренбург"])  # по убыванию на конец периода
        alg = build_report([A, B, C], TODAY, discipline="ALG")
        self.assertEqual(alg["total"]["counts"], [1, 2, 2])
        self.assertEqual(alg["total"]["delta_pct"], 100.0)

    def test_empty_baseline_gives_no_percentage(self):
        only_new = build_report([C], TODAY, years=2)
        self.assertEqual((only_new["total"]["counts"], only_new["total"]["delta_pct"]), ([0, 1], None))


class HeadcountApiTest(unittest.TestCase):
    def setUp(self):
        sportsman.store.__init__()
        self.ctx = TestClient(sportsman.app)
        self.api = self.ctx.__enter__()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def test_seeded_registry_shows_growth_over_three_year_ends(self):
        r = self.api.get("/api/v1/reports/headcount").json()
        counts = r["total"]["counts"]
        self.assertEqual(len(r["years"]), 3)
        self.assertEqual(counts, sorted(counts))
        self.assertTrue(counts[0] < counts[-1])
        self.assertEqual(counts[-1], self.api.get("/api/v1/stats").json()["athletes"])

    def test_validation(self):
        self.assertEqual(self.api.get("/api/v1/reports/headcount", params={"years": 1}).status_code, 422)
        self.assertEqual(self.api.get("/api/v1/reports/headcount", params={"discipline": "XXX"}).status_code, 422)
        self.assertEqual(self.api.get("/api/v1/reports/headcount", params={"municipality": "Нигде"}).status_code, 422)
        ok = self.api.get("/api/v1/reports/headcount", params={"years": 2, "discipline": "ALG"}).json()
        self.assertEqual(len(ok["years"]), 2)


if __name__ == "__main__":
    unittest.main()
