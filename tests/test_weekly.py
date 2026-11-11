"""Weekly entries/results and starred-horse emails (crossr_core.weekly), without a database."""

import datetime as dt
import smtplib

import pytest

from crossr_core import weekly

D = dt.date


# ------------------------------------------------------------- week boundaries
@pytest.mark.parametrize("today, entries, results", [
    (D(2026, 11, 2), (D(2026, 11, 2), D(2026, 11, 9)), (D(2026, 10, 26), D(2026, 11, 2))),   # Monday
    (D(2026, 11, 8), (D(2026, 11, 2), D(2026, 11, 9)), (D(2026, 10, 26), D(2026, 11, 2))),   # Sunday
    (D(2026, 11, 3), (D(2026, 11, 2), D(2026, 11, 9)), (D(2026, 10, 26), D(2026, 11, 2))),   # missed Monday, run Tuesday
    (D(2027, 1, 1), (D(2026, 12, 28), D(2027, 1, 4)), (D(2026, 12, 21), D(2026, 12, 28))),   # across a year end
])
def test_week_windows(today, entries, results):
    w = weekly.week_windows(today)
    assert w["entries"] == entries
    assert w["results"] == results


def test_monday_races_belong_to_exactly_one_week():
    monday = D(2026, 11, 2)
    this = weekly.week_windows(monday)
    following = weekly.week_windows(monday + dt.timedelta(days=7))
    inside = lambda day, win: win[0] <= day < win[1]
    assert inside(monday, this["entries"])  # entered on Monday morning's list
    assert inside(monday, following["results"])  # its result a week later
    assert not inside(monday, this["results"])
    assert not inside(monday - dt.timedelta(days=7), this["entries"])


def test_configurable_week_start():
    assert weekly.week_start(D(2026, 11, 4), start_weekday=2) == D(2026, 11, 4)
    assert weekly.week_start(D(2026, 11, 3), start_weekday=2) == D(2026, 10, 28)


# ------------------------------------------------------------- names
def test_name_key_and_star_parsing():
    assert weekly.name_key("Curlin (USA)") == "CURLIN"
    assert weekly.name_key("Flightline") == "FLIGHTLINE"
    assert weekly.name_key("Mo Donegal's  Pal") == "MO DONEGALS PAL"
    assert weekly.parse_star("Englishman (2002)") == ("ENGLISHMAN", 2002)
    assert weekly.parse_star("Zenyatta") == ("ZENYATTA", None)


# ------------------------------------------------------------- matching
def race(horse, hid, *, sire="", sire_refno="", dam="", dam_refno="", damsire="", damsire_refno="",
         foal_year=2022, date="2026-10-25", track="BEL", rn=4, place="1"):
    return {"horse": horse, "horse_id": hid, "horse_key": weekly.name_key(horse), "foal_year": foal_year,
            "sire": sire, "sire_refno": sire_refno, "sire_key": weekly.name_key(sire),
            "dam": dam, "dam_refno": dam_refno, "dam_key": weekly.name_key(dam),
            "damsire": damsire, "damsire_refno": damsire_refno, "damsire_key": weekly.name_key(damsire),
            "race_date": date, "track": "Belmont Park", "track_code": track, "race_number": rn,
            "race_name": "Maiden Special Weight", "grade": None, "place": place,
            "distance": None, "surface": None, "post_position": None}


def star(label, *, ids=(), refnos=(), year=None, unique=True):
    return {"label": label, "key": weekly.name_key(label), "year": year, "ids": set(ids),
            "refnos": set(refnos), "unique": unique}


def test_starred_horse_and_its_relatives_land_in_the_right_sections():
    stars = [star("Curlin", ids={"1"}, refnos={"R1"}, year=2004),
             star("Beholder", ids={"2"}, refnos={"R2"}, year=2010),
             star("Tapit", ids={"3"}, refnos={"R3"}, year=2001)]
    races = [
        race("Curlin", "1", foal_year=2004),  # the starred horse itself
        race("Son Of Curlin", "10", sire="Curlin", sire_refno="R1"),
        race("Beholder Foal", "11", dam="Beholder", dam_refno="R2"),
        race("Tapit Grandson", "12", dam="Tapit Mare", dam_refno="X", damsire="Tapit", damsire_refno="R3"),
        race("Unrelated", "13", sire="Gun Runner", sire_refno="R9"),
    ]
    out = weekly.match_items(stars, races)
    assert [r["horse"] for r in out["horse"]] == ["Curlin"]
    assert [r["horse"] for r in out["stallion"]] == ["Son Of Curlin"]
    assert [r["horse"] for r in out["broodmare"]] == ["Beholder Foal"]
    (bms,) = out["bms"]
    assert bms["horse"] == "Tapit Grandson" and bms["daughter"] == "Tapit Mare" and bms["starred"] == "Tapit"


def test_namesake_progeny_are_matched_by_refno_not_name():
    # Two horses named Englishman: the 2002 stallion (R1) and a 2023 colt (R2). The user starred the stallion.
    stars = [star("Englishman (2002)", ids={"1"}, refnos={"R1"}, year=2002, unique=True)]
    races = [race("By Old Englishman", "10", sire="Englishman", sire_refno="R1", foal_year=2015),
             race("By Young Englishman", "11", sire="Englishman", sire_refno="R2", foal_year=2026)]
    out = weekly.match_items(stars, races)
    assert [r["horse"] for r in out["stallion"]] == ["By Old Englishman"]


def test_name_only_progeny_need_a_unique_star_and_a_plausible_age():
    ambiguous = [star("Englishman", ids={"1", "2"}, refnos=set(), unique=False)]
    races = [race("Name Only Foal", "10", sire="Englishman", foal_year=2015)]
    assert weekly.match_items(ambiguous, races)["stallion"] == []
    unique = [star("Englishman", ids={"1"}, year=2002)]
    assert len(weekly.match_items(unique, races)["stallion"]) == 1
    too_old = [race("Older Than Sire", "11", sire="Englishman", foal_year=2003)]
    assert weekly.match_items(unique, too_old)["stallion"] == []


def test_pensioned_and_deceased_stallions_still_match():
    # a stallion's status never filters his runners: matching is on the pedigree relationship only
    stars = [star("Mr. Prospector", ids={"5"}, refnos={"MP"}, year=1970)]
    out = weekly.match_items(stars, [race("Late Son", "20", sire="Mr Prospector", sire_refno="MP", foal_year=2000)])
    assert len(out["stallion"]) == 1


def test_one_race_is_reported_once_per_section_even_if_starred_twice():
    stars = [star("Curlin", ids={"1"}, refnos={"R1"}), star("Curlin (2004)", ids={"1"}, refnos={"R1"}, year=2004)]
    out = weekly.match_items(stars, [race("Son", "10", sire="Curlin", sire_refno="R1")])
    assert len(out["stallion"]) == 1


def test_drop_sent_removes_already_emailed_items_per_kind():
    r = race("Son", "10", sire="Curlin", sire_refno="R1")
    sections = {"stallion": [r], "horse": [], "broodmare": [], "bms": []}
    sent = {("entry", weekly.item_key(r))}
    assert weekly.drop_sent(sections, sent, "entry")["stallion"] == []
    assert weekly.drop_sent(sections, sent, "result")["stallion"] == [r]  # result is still news


def test_notification_prefs_default_to_opted_out():
    assert weekly.notification_prefs({}) == {"email": False, "entries": True, "results": True}
    assert weekly.notification_prefs({"notifications": {"email": True, "entries": False}})["entries"] is False


# ------------------------------------------------------------- email
def sections(**kw):
    out = {k: [] for k, _ in weekly.SECTIONS}
    out.update(kw)
    return out


def test_no_email_when_nothing_to_report():
    assert weekly.render_email("max", D(2026, 11, 2), sections(), sections(), "https://x") is None


def test_email_has_only_non_empty_sections_with_links_and_finish():
    r = {**race("Son Of Curlin", "10", sire="Curlin", sire_refno="R1", place="2"), "starred": "Curlin", "daughter": None}
    mail = weekly.render_email("max", D(2026, 11, 2), sections(), sections(stallion=[r]), "https://site/")
    assert mail["count"] == 1
    assert "Starred stallions and their progeny" in mail["html"]
    assert "Starred horses</h3>" not in mail["html"] and "Starred broodmares" not in mail["html"]
    assert "https://site/horses/Son%20Of%20Curlin" in mail["text"]
    assert "finished 2nd" in mail["text"]
    assert "(Curlin)" in mail["text"]


def test_email_escapes_html():
    r = {**race("<b>Bad</b>", "10"), "starred": "<b>Bad</b>", "daughter": None}
    mail = weekly.render_email("<script>", D(2026, 11, 2), sections(horse=[r]), sections(), "https://x")
    assert "<script>" not in mail["html"] and "<b>Bad</b>" not in mail["html"]


@pytest.mark.parametrize("place, text", [("1", "1st"), ("2", "2nd"), ("3", "3rd"), ("11", "11th"), ("22", "22nd"), ("DQ", "DQ"), ("", "")])
def test_place_suffix(place, text):
    assert weekly._place({"place": place}) == text


class FakeSMTP:
    calls = []
    script = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        action = FakeSMTP.script.pop(0)
        FakeSMTP.calls.append(action)
        if isinstance(action, Exception):
            raise action
        return self

    def __exit__(self, *a):
        return False

    def login(self, *a):
        pass

    def send_message(self, msg):
        FakeSMTP.sent = msg


MAIL = {"subject": "s", "text": "t", "html": "<p>h</p>"}
SETTINGS = {"host": "h", "port": 465, "user": "u", "password": "p", "from": "f@x"}


def test_temporary_smtp_failure_is_retried():
    FakeSMTP.calls, FakeSMTP.script = [], [smtplib.SMTPServerDisconnected("x"), smtplib.SMTPResponseException(421, b"busy"), "ok"]
    weekly.send_email(SETTINGS, "a@b.c", MAIL, smtp_cls=FakeSMTP, sleep=lambda s: None)
    assert len(FakeSMTP.calls) == 3
    assert FakeSMTP.sent["To"] == "a@b.c"


def test_permanent_smtp_failure_is_not_retried():
    FakeSMTP.calls, FakeSMTP.script = [], [smtplib.SMTPResponseException(535, b"bad credentials"), "ok"]
    with pytest.raises(smtplib.SMTPResponseException):
        weekly.send_email(SETTINGS, "a@b.c", MAIL, smtp_cls=FakeSMTP, sleep=lambda s: None)
    assert len(FakeSMTP.calls) == 1


def test_gives_up_after_retries():
    FakeSMTP.calls, FakeSMTP.script = [], [OSError("down")] * 3
    with pytest.raises(OSError):
        weekly.send_email(SETTINGS, "a@b.c", MAIL, smtp_cls=FakeSMTP, sleep=lambda s: None)
    assert len(FakeSMTP.calls) == 3


def test_no_smtp_settings_without_credentials(monkeypatch):
    monkeypatch.delenv("CROSSR_SMTP_USER", raising=False)
    monkeypatch.delenv("CROSSR_SMTP_PASSWORD", raising=False)
    assert weekly.smtp_settings() is None
    monkeypatch.setenv("CROSSR_SMTP_USER", "me@gmail.com")
    monkeypatch.setenv("CROSSR_SMTP_PASSWORD", "app-password")
    s = weekly.smtp_settings()
    assert s["host"] == "smtp.gmail.com" and s["port"] == 465 and s["from"] == "me@gmail.com"


# ------------------------------------------------------------- preferences
def test_account_keeps_email_settings_when_a_client_syncs_only_stars():
    from crossr_core import auth

    prefs = auth._clean_prefs({"starred": ["Curlin"], "notifications": {"email": True, "entries": False}})
    assert prefs["notifications"] == {"email": True, "entries": False, "results": True}
    merged = auth._merge_prefs(prefs, {"starred": ["Tapit"], "notifications": {"email": False}})
    assert merged["notifications"]["email"] is True  # the signed-out browser copy never overrides
    assert merged["starred"] == ["Curlin", "Tapit"]


def test_clean_display_drops_trailing_figure_after_country():
    assert weekly.clean_display("Katonah (KY) 56") == "Katonah (KY)"
    assert weekly.clean_display("Growth Trajectory (GB) 74") == "Growth Trajectory (GB)"
    assert weekly.clean_display("Seven 7") == "Seven 7"  # a number that is part of the name stays


def test_import_entries_reads_csv(tmp_path):
    import importlib.util, pathlib

    spec = importlib.util.spec_from_file_location("import_entries", pathlib.Path(__file__).parents[1] / "scripts" / "import_entries.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    f = tmp_path / "e.csv"
    f.write_text("Race_Date,Track_Code,Race_Number,Horse,Post_Position,Status\n"
                 "2026-11-02,AQU,3,Curlin's Son (KY),5,\n"
                 "2026-11-02,AQU,4,,1,\n"
                 "2026-11-03,AQU,1,Scratched One,2,Scratched\n")
    rows = mod.read_rows(f)
    assert [r["horse_key"] for r in rows] == ["CURLINS SON", "SCRATCHED ONE"]
    assert rows[0]["race_number"] == 3 and rows[0]["status"] == "entered" and rows[1]["status"] == "scratched"
