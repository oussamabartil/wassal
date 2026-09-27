"""
Tests unitaires Wassal : parser, résolveur de repères, API.
Wassal unit tests. Run: pytest -v

Aucun test ne charge le modèle MoulSot : la transcription est simulée,
donc la suite tourne en quelques secondes sans GPU ni torch.
"""

import io
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import api as api_module  # noqa: E402
from src.api import create_app, normalize_phone, ApiError  # noqa: E402
from src.landmarks import LandmarkResolver  # noqa: E402
from src.normalize import detect_language, normalize_text  # noqa: E402
from src.parser import parse_darija_command  # noqa: E402
from src import transcribe as transcribe_module  # noqa: E402
from src.transcribe import transcribe_darija_audio  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def resolver():
    return LandmarkResolver()


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "LANDMARKS_STORE": str(tmp_path / "landmarks.json"),
        "API_TOKEN": "",
    })


@pytest.fixture
def client(app):
    return app.test_client()


def post_command(client, **payload):
    return client.post("/wassal/command", json=payload)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_french_accents_and_punctuation(self):
        assert normalize_text("Derrière la Mosquée !") == "derriere la mosquee"

    def test_arabic_folding(self):
        # ة -> ه, أ -> ا, harakat supprimés
        assert normalize_text("القارَة") == normalize_text("القاره")
        assert normalize_text("أتاي") == "اتاي"

    def test_empty(self):
        assert normalize_text("") == ""

    @pytest.mark.parametrize("text,expected", [
        ("بغيت تاكسي للقارة", "darija"),
        ("jib lia khobz o 7lib", "darija_latin"),
        ("je veux un taxi pour la gare", "french"),
        ("bghit taxi l la gare de casa", "mixed"),
        ("بغيت taxi l gare", "mixed"),
    ])
    def test_detect_language(self, text, expected):
        assert detect_language(text) == expected


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class TestParser:
    def test_example_taxi_arabic(self):
        r = parse_darija_command("بغيت تاكسي للقارة")
        assert (r["service_type"], r["subtype"]) == ("ride", "taxi")
        assert r["urgency"] == "normal"
        assert r["confidence"] >= 0.9

    def test_example_groceries_go_to_market(self):
        # Pain + lait = courses -> Yassir Market (pas Yassir Food, réservé aux restaurants).
        r = parse_darija_command("jib lia khobz o 7lib")
        assert (r["service_type"], r["subtype"]) == ("delivery", "market")
        assert r["yassir_product"] == "Yassir Market"
        assert r["items"] == ["bread", "milk"]
        assert r["urgency"] == "normal"
        assert r["confidence"] >= 0.85

    def test_example_package_urgent(self):
        r = parse_darija_command("waslni package ldjamaa fasa")
        assert (r["service_type"], r["subtype"]) == ("delivery", "package")
        assert r["urgency"] == "urgent"
        assert r["confidence"] >= 0.8

    @pytest.mark.parametrize("text", [
        "bghit taxi", "waslni b taxi", "taksi 3afak", "بغيت درايفر", "je veux un taxi",
        "bghit nmchi lagar", "هزني من الدار",
    ])
    def test_taxi_variants(self, text):
        assert parse_darija_command(text)["subtype"] == "taxi"

    @pytest.mark.parametrize("text", [
        "بغيت طلبية ديال الماكلة", "bghit pizza", "bghit tajine mn resto", "جيعان بغيت ماكلة",
        "jib lia pizza o coca",
    ])
    def test_food_variants(self, text):
        r = parse_darija_command(text)
        assert r["subtype"] == "food"
        assert r["yassir_product"] == "Yassir Food"

    @pytest.mark.parametrize("text", [
        "jib lia kolchi mn hanout", "جيب ليا خبز و حليب", "chri lia atay o sokar", "jib lia lma",
        "bghit courses mn marjane",
    ])
    def test_market_variants(self, text):
        r = parse_darija_command(text)
        assert r["subtype"] == "market"
        assert r["yassir_product"] == "Yassir Market"

    def test_drink_is_kept_with_meal(self):
        r = parse_darija_command("jib lia pizza o coca")
        assert r["items"] == ["pizza", "soda"]

    def test_products(self):
        assert parse_darija_command("bghit taxi")["yassir_product"] == "Yassir Go"
        # Colis : pas dans l'offre Yassir Maroc.
        assert parse_darija_command("sift colis")["yassir_product"] is None
        assert parse_darija_command("salam")["yassir_product"] is None

    @pytest.mark.parametrize("text", [
        "sift had colis l khouya", "عندي طرد بغيت نصيفطو", "waslni had l package",
        "bghit nsift chi 7aja",
    ])
    def test_package_variants(self, text):
        assert parse_darija_command(text)["subtype"] == "package"

    def test_waslni_alone_is_a_ride(self):
        # "waslni" = "emmène-moi" : sans objet, c'est une course.
        assert parse_darija_command("waslni l gare")["subtype"] == "taxi"

    @pytest.mark.parametrize("text,urgency", [
        ("bghit taxi fasa", "urgent"),
        ("taxi dlak", "urgent"),
        ("bghit taxi daba", "urgent"),
        ("jib lia khobz basr", "quick"),
        ("جيب ليا خبز بسرعة", "quick"),
        ("bghit taxi", "normal"),
    ])
    def test_urgency(self, text, urgency):
        assert parse_darija_command(text)["urgency"] == urgency

    def test_item_quantities(self):
        r = parse_darija_command("jib lia joj khobz w 3 7lib")
        details = {i["name"]: i["quantity"] for i in r["item_details"]}
        assert details == {"bread": 2, "milk": 3}

    def test_unknown_command(self):
        r = parse_darija_command("salam labas 3lik")
        assert r["service_type"] == "unknown"
        assert r["subtype"] is None
        assert r["confidence"] == 0.0

    def test_ambiguous_lowers_confidence(self):
        clear = parse_darija_command("sift colis")["confidence"]
        mixed = parse_darija_command("waslni colis")["confidence"]
        assert mixed < clear

    @pytest.mark.parametrize("bad", ["", "   "])
    def test_empty_text_raises(self, bad):
        with pytest.raises(ValueError):
            parse_darija_command(bad)

    def test_non_string_raises(self):
        with pytest.raises(TypeError):
            parse_darija_command(None)  # type: ignore[arg-type]

    def test_too_long_raises(self):
        with pytest.raises(ValueError):
            parse_darija_command("taxi " * 200)


# ---------------------------------------------------------------------------
# Landmarks
# ---------------------------------------------------------------------------

class TestLandmarks:
    def test_arabic_gare_with_prefix(self, resolver):
        r = resolver.resolve("بغيت تاكسي للقارة", "casablanca")
        assert r["place_name"] == "Gare Casa-Voyageurs"
        assert r["confidence"] >= 0.9

    def test_behind_the_mosque(self, resolver):
        r = resolver.resolve("derrière la mosquée", "casablanca")
        assert r["place_name"] == "Mosquée Hassan II"
        assert r["relation"] == "behind"
        assert {"lat", "lng", "place_name", "confidence"} <= r.keys()

    def test_jamaa_depends_on_city(self, resolver):
        assert resolver.resolve("waslni package ldjamaa", "fes")["landmark_id"] == "fes_medina"
        assert resolver.resolve("waslni l jamaa", "marrakech")["landmark_id"] == "rak_koutoubia"

    def test_longest_alias_wins(self, resolver):
        # "jemaa el fna" ne doit pas être pris pour "jamaa" (Koutoubia).
        assert resolver.resolve("bghit nmchi l jemaa el fna", "marrakech")["landmark_id"] == "rak_jemaa_el_fna"

    @pytest.mark.parametrize("city", ["Casa", "الدار البيضاء", "CASABLANCA"])
    def test_city_aliases(self, resolver, city):
        assert resolver.normalize_city(city) == "casablanca"

    def test_city_detected_from_text(self, resolver):
        r = resolver.resolve("gare de marrakech")
        assert r["city"] == "marrakech"

    def test_generic_alias_without_city_is_ambiguous(self, resolver):
        r = resolver.resolve("la gare")
        assert r is not None
        assert r["confidence"] < 0.7

    def test_pickup_and_destination(self, resolver):
        route = resolver.resolve_route("mn lgare l jamaa", "casablanca")
        assert route["pickup"]["landmark_id"] == "casa_gare_voyageurs"
        assert route["destination"]["landmark_id"] == "casa_mosquee_hassan2"

    def test_no_match(self, resolver):
        assert resolver.resolve("chi blassa ma kaynach", "casablanca") is None
        assert resolver.resolve("", "casablanca") is None

    def test_crowdsourcing_add_and_resolve(self, tmp_path):
        store = tmp_path / "lm.json"
        resolver = LandmarkResolver(store_path=str(store))
        added = resolver.add_landmark("Café Atlas", "casa", 33.5900, -7.6100, aliases=["9ahwa atlas"])
        assert added["source"] == "crowdsourced" and not added["verified"]

        r = resolver.resolve("waslni l 9ahwa atlas", "casablanca")
        assert r["place_name"] == "Café Atlas"
        assert 0.5 <= r["confidence"] <= 0.8

        # Persistance : un nouveau résolveur relit le fichier.
        reloaded = LandmarkResolver(store_path=str(store))
        assert reloaded.resolve("9ahwa atlas", "casablanca")["place_name"] == "Café Atlas"

    def test_crowdsourcing_upvote_raises_confidence(self, resolver):
        resolver.add_landmark("Hanout Si Ahmed", "fes", 34.03, -5.0)
        first = resolver.resolve("hanout si ahmed", "fes")["confidence"]
        resolver.add_landmark("Hanout Si Ahmed", "fes", 34.03, -5.0)
        assert resolver.resolve("hanout si ahmed", "fes")["confidence"] > first

    @pytest.mark.parametrize("kwargs,message", [
        ({"name": "X place", "city": "paris", "lat": 33.5, "lng": -7.6}, "unknown city"),
        ({"name": "X place", "city": "casablanca", "lat": 48.8, "lng": 2.3}, "outside Morocco"),
        ({"name": "X place", "city": "casablanca", "lat": 31.63, "lng": -7.98}, "km from"),
        ({"name": "X place", "city": "casablanca", "lat": "abc", "lng": -7.6}, "numbers"),
        ({"name": "", "city": "casablanca", "lat": 33.5, "lng": -7.6}, "name"),
    ])
    def test_crowdsourcing_validation(self, resolver, kwargs, message):
        with pytest.raises(ValueError, match=message):
            resolver.add_landmark(**kwargs)

    def test_corrupted_store_does_not_crash(self, tmp_path):
        store = tmp_path / "broken.json"
        store.write_text("{not json", encoding="utf-8")
        assert len(LandmarkResolver(store_path=str(store))) > 0


# ---------------------------------------------------------------------------
# Transcription (sans modèle / without the model)
# ---------------------------------------------------------------------------

class TestTranscribe:
    def test_missing_file(self):
        r = transcribe_darija_audio("does_not_exist.wav")
        assert r["status"] == "error" and r["error_code"] == "FILE_NOT_FOUND"

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "note.txt"
        f.write_text("hello")
        assert transcribe_darija_audio(str(f))["error_code"] == "UNSUPPORTED_FORMAT"

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.wav"
        f.write_bytes(b"")
        assert transcribe_darija_audio(str(f))["error_code"] == "EMPTY_AUDIO"

    def test_extract_text_handles_list_of_results(self):
        # qwen_asr.transcribe() renvoie une liste d'ASRTranscription.
        class Result:
            def __init__(self, text):
                self.text = text

        assert transcribe_module._extract_text([Result(" بغيت تاكسي ")]) == "بغيت تاكسي"
        assert transcribe_module._extract_text([]) == ""

    def test_m4a_is_decoded_and_sent_as_samples(self, tmp_path, monkeypatch):
        # Régression : libsndfile ne lit pas le m4a (AAC) -> décodage PyAV.
        av = pytest.importorskip("av")
        np = pytest.importorskip("numpy")
        pytest.importorskip("soundfile")

        path = tmp_path / "cmd.m4a"
        rate = 44100
        tone = (0.3 * np.sin(2 * np.pi * 440 * np.arange(rate) / rate)).astype(np.float32)
        with av.open(str(path), "w", format="mp4") as out:
            stream = out.add_stream("aac", rate=rate, layout="mono")
            for i in range(0, len(tone), 1024):
                frame = av.AudioFrame.from_ndarray(tone[None, i:i + 1024], format="fltp", layout="mono")
                frame.sample_rate = rate
                for packet in stream.encode(frame):
                    out.mux(packet)
            for packet in stream.encode(None):
                out.mux(packet)

        received = {}

        class FakeModel:
            def transcribe(self, audio, language=None):
                received["audio"], received["language"] = audio, language

                class Result:
                    text = "بغيت تاكسي للقارة"
                return [Result()]

        monkeypatch.setattr(transcribe_module, "_model", FakeModel())
        result = transcribe_darija_audio(str(path))

        assert result["status"] == "success", result
        assert result["transcription"] == "بغيت تاكسي للقارة"
        assert 0.9 < result["duration_s"] < 1.2
        samples, sample_rate = received["audio"]
        assert sample_rate == 16000 and samples.ndim == 1
        assert received["language"] == "Arabic"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class TestPhone:
    @pytest.mark.parametrize("raw", ["0612345678", "06 12 34 56 78", "+212612345678", "00212 6-12-34-56-78"])
    def test_valid(self, raw):
        assert normalize_phone(raw) == "+212612345678"

    @pytest.mark.parametrize("raw", ["12345", "0812345678", "+33612345678"])
    def test_invalid(self, raw):
        with pytest.raises(ApiError):
            normalize_phone(raw)

    def test_empty_is_none(self):
        assert normalize_phone("") is None and normalize_phone(None) is None


class TestApi:
    def test_health(self, client):
        res = client.get("/wassal/test")
        assert res.status_code == 200
        body = res.get_json()
        assert body["success"] is True
        assert body["data"]["status"] == "ok"
        assert "casablanca" in body["data"]["cities"]
        assert res.headers.get("X-Request-ID")

    def test_frontend_served(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert b"Wassal" in res.data

    def test_taxi_command_ready(self, client):
        res = post_command(client, darija_text="بغيت تاكسي للقارة", city="casablanca", phone="0612345678")
        assert res.status_code == 200
        data = res.get_json()["data"]
        assert data["service_type"] == "ride"
        assert data["destination"]["place"] == "Gare Casa-Voyageurs"
        assert data["ready_for_yassir"] is True
        assert data["yassir_request"]["dropoff"]["label"] == "Gare Casa-Voyageurs"
        assert data["yassir_request"]["customer"]["phone"] == "+212612345678"
        # L'arabe reste lisible dans le JSON (pas de \uXXXX).
        assert "بغيت" in res.get_data(as_text=True)

    def test_market_command_ready_without_destination(self, client):
        data = post_command(client, darija_text="jib lia khobz o 7lib", city="casablanca").get_json()["data"]
        assert data["subtype"] == "market"
        assert data["yassir_request"]["product"] == "Yassir Market"
        assert data["items"] == ["bread", "milk"]
        assert data["ready_for_yassir"] is True
        assert data["yassir_request"]["dropoff"] == {"type": "current_location"}

    def test_package_command(self, client):
        data = post_command(client, darija_text="waslni package ldjamaa fasa", city="fes").get_json()["data"]
        assert data["subtype"] == "package"
        assert data["urgency"] == "urgent"
        assert data["destination"]["city"] == "fes"
        assert data["ready_for_yassir"] is True
        assert any("not listed in Yassir's Morocco offer" in w for w in data["warnings"])

    def test_taxi_without_destination_not_ready(self, client):
        data = post_command(client, darija_text="bghit taxi", city="casablanca").get_json()["data"]
        assert data["ready_for_yassir"] is False
        assert "destination" in data["missing_fields"]
        assert data["yassir_request"] is None

    def test_unknown_service_not_ready(self, client):
        data = post_command(client, darija_text="salam labas", city="casablanca").get_json()["data"]
        assert data["service_type"] == "unknown"
        assert data["ready_for_yassir"] is False

    @pytest.mark.parametrize("payload,code", [
        ({}, "MISSING_TEXT"),
        ({"darija_text": "   "}, "MISSING_TEXT"),
        ({"darija_text": 42}, "MISSING_TEXT"),
        ({"darija_text": "taxi " * 200}, "TEXT_TOO_LONG"),
        ({"darija_text": "bghit taxi", "city": "paris"}, "INVALID_CITY"),
        ({"darija_text": "bghit taxi", "phone": "123"}, "INVALID_PHONE"),
    ])
    def test_validation_errors(self, client, payload, code):
        res = client.post("/wassal/command", json=payload)
        assert res.status_code == 400
        body = res.get_json()
        assert body["success"] is False
        assert body["error"]["code"] == code

    def test_invalid_json(self, client):
        res = client.post("/wassal/command", data="{oops", content_type="application/json")
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "INVALID_JSON"

    def test_wrong_content_type(self, client):
        res = client.post("/wassal/command", data="darija_text=taxi", content_type="text/plain")
        assert res.status_code == 415

    def test_unknown_route_is_json(self, client):
        res = client.get("/nope")
        assert res.status_code == 404
        assert res.get_json()["success"] is False

    def test_method_not_allowed(self, client):
        assert client.get("/wassal/command").status_code == 405

    def test_audio_command_uses_transcription(self, client, monkeypatch):
        def fake_transcribe(path):
            assert os.path.exists(path)
            return {"status": "success", "transcription": "بغيت تاكسي للقارة", "language": "darija",
                    "duration_s": 2.1, "model": "fake"}

        monkeypatch.setattr(api_module, "transcribe_darija_audio", fake_transcribe)
        res = client.post(
            "/wassal/command",
            data={"audio": (io.BytesIO(b"RIFF....WAVE"), "cmd.wav"), "city": "casablanca"},
            content_type="multipart/form-data",
        )
        assert res.status_code == 200
        data = res.get_json()["data"]
        assert data["transcription"]["transcription"] == "بغيت تاكسي للقارة"
        assert data["ready_for_yassir"] is True

    def test_audio_model_unavailable_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(api_module, "transcribe_darija_audio", lambda p: {
            "status": "error", "error": "model missing", "error_code": "MODEL_UNAVAILABLE"})
        res = client.post(
            "/wassal/command",
            data={"audio": (io.BytesIO(b"xxxx"), "cmd.wav")},
            content_type="multipart/form-data",
        )
        assert res.status_code == 503

    def test_audio_bad_extension(self, client):
        res = client.post(
            "/wassal/command",
            data={"audio": (io.BytesIO(b"xxxx"), "cmd.exe")},
            content_type="multipart/form-data",
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "UNSUPPORTED_FORMAT"

    def test_landmarks_list_and_crowdsource(self, client, app):
        res = client.get("/wassal/landmarks?city=fes")
        assert res.status_code == 200
        assert all(lm["city"] == "fes" for lm in res.get_json()["data"]["landmarks"])

        res = client.post("/wassal/landmarks", json={
            "name": "Pharmacie Nour", "city": "casablanca", "lat": 33.59, "lng": -7.62,
            "aliases": ["farmasyan nour"],
        })
        assert res.status_code == 201

        data = post_command(client, darija_text="waslni l farmasyan nour b taxi",
                            city="casablanca").get_json()["data"]
        assert data["destination"]["place_name"] == "Pharmacie Nour"

        store = Path(app.config["LANDMARKS_STORE"])
        assert json.loads(store.read_text(encoding="utf-8"))[0]["name"] == "Pharmacie Nour"

    def test_crowdsource_validation_error(self, client):
        res = client.post("/wassal/landmarks", json={"name": "X", "city": "casablanca"})
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "MISSING_FIELDS"

    def test_crowdsource_requires_token_when_configured(self, tmp_path):
        app = create_app({"TESTING": True, "LANDMARKS_STORE": str(tmp_path / "l.json"), "API_TOKEN": "s3cret"})
        client = app.test_client()
        payload = {"name": "Café Test", "city": "casablanca", "lat": 33.59, "lng": -7.61}
        assert client.post("/wassal/landmarks", json=payload).status_code == 401
        assert client.post("/wassal/landmarks", json=payload, headers={"X-API-Key": "s3cret"}).status_code == 201
