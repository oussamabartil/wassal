"""
API Flask Wassal - وصّل.
Wassal Flask API: Darija command (text or audio) -> structured Yassir request.

Routes:
    GET  /                    -> interface web (frontend/index.html)
    GET  /wassal/test         -> health check
    POST /wassal/command      -> traite une commande (JSON ou multipart audio)
    GET  /wassal/landmarks    -> liste des repères (?city=casablanca)
    POST /wassal/landmarks    -> ajout crowdsourcé d'un repère

Lancer / run:
    python src/api.py
"""

import logging
import os
import random
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Permet `python src/api.py` : on importe via le package `src` pour éviter
# tout conflit avec l'ancien module standard `parser` (Python <= 3.9).
# Allow running as a script while importing through the `src` package.
ROOT_DIR = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv  # noqa: E402
from flask import Flask, g, jsonify, request, send_from_directory  # noqa: E402
from flask_cors import CORS  # noqa: E402
from werkzeug.exceptions import HTTPException  # noqa: E402
from werkzeug.utils import secure_filename  # noqa: E402

from src import __version__  # noqa: E402
from src.landmarks import MOROCCO_BBOX, LandmarkResolver, haversine_km  # noqa: E402
from src.parser import MAX_TEXT_LENGTH, parse_darija_command  # noqa: E402
from src.transcribe import (  # noqa: E402
    DEFAULT_MODEL, SUPPORTED_EXTENSIONS, asr_available, is_model_loaded, model_status,
    start_background_warmup,
    transcribe_darija_audio,
)

load_dotenv(ROOT_DIR / ".env")

logger = logging.getLogger("wassal.api")

FRONTEND_DIR = ROOT_DIR / "frontend"

# Téléphone marocain : 05/06/07 + 8 chiffres, avec +212 / 00212 optionnel.
_PHONE_RE = re.compile(r"^(?:\+212|00212|0)([5-7]\d{8})$")

# Codes d'erreur HTTP de transcription / transcription error -> HTTP status.
_TRANSCRIPTION_STATUS = {"MODEL_UNAVAILABLE": 503, "INFERENCE_ERROR": 500}

# Sous-type -> onglet de l'app Yassir à ouvrir automatiquement après la commande vocale.
# "package" n'a pas d'écran cible : hors de l'offre Yassir Maroc (Go, Food, Market).
SUBTYPE_TO_SCREEN = {"taxi": "go", "food": "food", "market": "market", "package": None}

# Chauffeurs/livreurs factices pour la simulation /wassal/dispatch (aucun appel réel à Yassir).
_MOCK_COURIERS = [
    {"name": "Youssef B.", "vehicle": "Dacia Logan blanche", "plate": "12345-A-6"},
    {"name": "Karim T.", "vehicle": "Renault Clio grise", "plate": "45210-B-3"},
    {"name": "Hamza E.", "vehicle": "Peugeot 208 bleue", "plate": "78542-A-9"},
    {"name": "Salma R.", "vehicle": "Dacia Sandero rouge", "plate": "36201-D-5"},
]


class ApiError(Exception):
    """Erreur métier renvoyée proprement au client / client-facing API error."""

    def __init__(self, message: str, status: int = 400, code: str = "INVALID_INPUT",
                 details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details or {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_phone(phone: Optional[str]) -> Optional[str]:
    """
    Valide et normalise un numéro marocain au format E.164 (+2126XXXXXXXX).
    Validate a Moroccan phone number; None/empty -> None.

    Raises:
        ApiError: numéro invalide / invalid number.
    """
    if phone is None or (isinstance(phone, str) and not phone.strip()):
        return None
    if not isinstance(phone, str):
        raise ApiError("phone must be a string", code="INVALID_PHONE")
    compact = re.sub(r"[\s.\-()]", "", phone)
    m = _PHONE_RE.match(compact)
    if not m:
        raise ApiError("invalid Moroccan phone number (ex: 0612345678 or +212612345678)", code="INVALID_PHONE")
    return f"+212{m.group(1)}"


def mask_phone(phone: Optional[str]) -> str:
    """Masque le numéro dans les logs / mask phone numbers in logs."""
    if not phone:
        return "-"
    return phone[:6] + "****" + phone[-2:]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        logger.warning("Invalid %s, using default %s", name, default)
        return default


def _current_location(user_location: Optional[Tuple[float, float]]) -> Dict[str, Any]:
    """Position GPS de l'utilisateur (envoyée par l'app) / user's GPS position."""
    if user_location:
        return {"type": "current_location", "lat": user_location[0], "lng": user_location[1]}
    return {"type": "current_location"}


def parse_user_location(lat: Any, lng: Any) -> Optional[Tuple[float, float]]:
    """
    Valide la position GPS de l'utilisateur (les deux ou aucune, au Maroc).
    Validate the user's GPS position: both or neither, inside Morocco.

    Raises:
        ApiError: position incomplète, non numérique ou hors du Maroc.
    """
    if lat in (None, "") and lng in (None, ""):
        return None
    if lat in (None, "") or lng in (None, ""):
        raise ApiError("'user_lat' and 'user_lng' must be sent together", code="INVALID_LOCATION")
    try:
        lat_f, lng_f = float(lat), float(lng)
    except (TypeError, ValueError):
        raise ApiError("'user_lat'/'user_lng' must be numbers", code="INVALID_LOCATION") from None
    if not (MOROCCO_BBOX["lat_min"] <= lat_f <= MOROCCO_BBOX["lat_max"]
            and MOROCCO_BBOX["lng_min"] <= lng_f <= MOROCCO_BBOX["lng_max"]):
        raise ApiError("user location is outside Morocco", code="INVALID_LOCATION")
    return lat_f, lng_f


def _location(point: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "landmark", "lat": point["lat"], "lng": point["lng"], "label": point["place_name"]}


def build_yassir_request(result: Dict[str, Any], phone: Optional[str],
                         user_location: Optional[Tuple[float, float]] = None) -> Dict[str, Any]:
    """
    Construit la requête prête à envoyer à Yassir.
    Build the draft request payload for Yassir. The exact schema must be
    aligned with Yassir's partner API; this is the integration contract
    Wassal proposes.
    """
    pickup = _location(result["pickup"]) if result.get("pickup") else _current_location(user_location)
    dropoff = _location(result["destination"]) if result.get("destination") else _current_location(user_location)
    payload: Dict[str, Any] = {
        "service": result["service_type"],
        "category": result["subtype"],
        "product": result["yassir_product"],
        "pickup": pickup,
        "dropoff": dropoff,
        "priority": result["urgency"],
        "customer": {"phone": phone},
        "source": "wassal_voice",
    }
    if result["subtype"] in ("food", "market"):
        payload["items"] = [{"name": i["name"], "quantity": i["quantity"]} for i in result["item_details"]]
    return payload


def estimate_eta_minutes(yassir_request: Dict[str, Any]) -> int:
    """
    ETA factice pour la simulation de dispatch : à partir de la distance
    pickup -> dropoff si connue (25 km/h de moyenne en ville + 3 min de base),
    sinon une fourchette plausible. Pas une estimation réelle Yassir.
    """
    pickup, dropoff = yassir_request.get("pickup") or {}, yassir_request.get("dropoff") or {}
    if all(k in pickup for k in ("lat", "lng")) and all(k in dropoff for k in ("lat", "lng")):
        km = haversine_km(pickup["lat"], pickup["lng"], dropoff["lat"], dropoff["lng"])
        return max(4, min(35, round(3 + km / 25 * 60)))
    return random.randint(6, 18)


def process_command(text: str, city: Optional[str], phone: Optional[str],
                    resolver: LandmarkResolver, min_confidence: float,
                    user_location: Optional[Tuple[float, float]] = None) -> Dict[str, Any]:
    """
    Pipeline complet : parsing + résolution d'adresse + décision "prêt pour Yassir".
    Full pipeline: intent parsing + landmark resolution + readiness decision.
    """
    parsed = parse_darija_command(text)
    city = city or resolver.city_from_location(user_location)
    route = resolver.resolve_route(text, city, user_location)
    destination, pickup = route["destination"], route["pickup"]
    unresolved = route["unresolved"]

    missing: List[str] = []
    warnings: List[str] = []
    subtype = parsed["subtype"]

    if parsed["service_type"] == "unknown":
        missing.append("service_type")
    # "la mosquée" compris mais pas localisable sans la position de l'utilisateur.
    needs_location = [u for u in unresolved if u["reason"] == "user_location_required"]
    if needs_location:
        missing.append("user_location")
        for u in needs_location:
            warnings.append(f"'{u['matched_text']}' ({u['category_label']}): send the user's position "
                            f"to find the nearest one")
    if subtype in ("taxi", "package") and not destination and not needs_location:
        missing.append("destination")
    if subtype in ("food", "market") and not parsed["items"] and not destination:
        missing.append("items")

    # Confiance globale = intention x adresse (si une adresse est utilisée).
    confidence = parsed["confidence"]
    breakdown = {"intent": parsed["confidence"]}
    for label, point in (("destination", destination), ("pickup", pickup)):
        if point:
            breakdown[label] = point["confidence"]
            confidence *= point["confidence"]
            if point["confidence"] < 0.7:
                warnings.append(f"{label} is uncertain ('{point['place_name']}'), please confirm")
    confidence = round(confidence, 2)

    if subtype == "package":
        warnings.append("package delivery is not listed in Yassir's Morocco offer (Go, Food, Market)")
    if not city and (destination or pickup):
        warnings.append("no city provided; landmark resolved from text or best guess")
    if confidence < min_confidence and not missing:
        warnings.append(f"confidence {confidence} below threshold {min_confidence}")

    ready = not missing and confidence >= min_confidence
    result: Dict[str, Any] = {
        "service_type": parsed["service_type"],
        "subtype": subtype,
        "target_screen": SUBTYPE_TO_SCREEN.get(subtype),
        "yassir_product": parsed["yassir_product"],
        "destination": destination,
        "pickup": pickup,
        "items": parsed["items"],
        "item_details": parsed["item_details"],
        "urgency": parsed["urgency"],
        "confidence": confidence,
        "confidence_breakdown": breakdown,
        "ready_for_yassir": ready,
        "missing_fields": missing,
        "warnings": warnings,
        "language": parsed["language"],
        "matched_keywords": parsed["matched_keywords"],
        "negation_detected": parsed["negation_detected"],
        "input_text": text,
        "city": city,
    }
    result["user_location"] = (
        {"lat": user_location[0], "lng": user_location[1]} if user_location else None
    )
    result["unresolved_places"] = unresolved
    result["yassir_request"] = build_yassir_request(result, phone, user_location) if ready else None
    return result


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app(config: Optional[Dict[str, Any]] = None) -> Flask:
    """
    Crée l'application Flask / create the Flask app.

    Args:
        config: surcharge de configuration (utile pour les tests), ex.
                {"LANDMARKS_STORE": "/tmp/x.json", "TESTING": True}.
    """
    app = Flask(__name__, static_folder=None)
    app.config.update(
        MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "25")) * 1024 * 1024,
        LANDMARKS_STORE=os.getenv("LANDMARKS_STORE", str(ROOT_DIR / "data" / "crowdsourced_landmarks.json")),
        # Vrais lieux importés d'OpenStreetMap (scripts/import_osm.py). "" = désactivé.
        OSM_DATA_DIR=os.getenv("OSM_DATA_DIR", str(ROOT_DIR / "data" / "osm")),
        MIN_CONFIDENCE=_env_float("MIN_CONFIDENCE", 0.6),
        API_TOKEN=os.getenv("WASSAL_API_TOKEN", ""),
    )
    if config:
        app.config.update(config)
    # Chemin relatif = relatif à la racine du projet, pas au dossier courant.
    store = Path(app.config["LANDMARKS_STORE"])
    app.config["LANDMARKS_STORE"] = str(store if store.is_absolute() else ROOT_DIR / store)
    app.json.ensure_ascii = False  # Flask 3 : garder l'arabe lisible dans le JSON

    origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
    CORS(app, resources={r"/wassal/*": {"origins": origins}})

    osm_dir = app.config["OSM_DATA_DIR"]
    if osm_dir and not Path(osm_dir).is_absolute():
        osm_dir = str(ROOT_DIR / osm_dir)
    resolver = LandmarkResolver(store_path=app.config["LANDMARKS_STORE"], osm_dir=osm_dir or None)

    # Sous gunicorn, le bloc __main__ ne s'exécute pas : on configure les logs ici.
    if not logging.getLogger().handlers:
        _configure_logging()
    # PRELOAD_MODEL=true : charge MoulSot en arrière-plan dès le démarrage.
    if os.getenv("PRELOAD_MODEL", "false").lower() == "true" and not app.config.get("TESTING"):
        start_background_warmup()
    app.extensions["wassal_resolver"] = resolver

    # ---------------------------------------------------------- réponses JSON

    def success(data: Dict[str, Any], status: int = 200):
        body = {
            "success": True,
            "request_id": g.get("request_id"),
            "processing_ms": int((time.perf_counter() - g.get("started", time.perf_counter())) * 1000),
            "data": data,
        }
        return jsonify(body), status

    def failure(status: int, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        body = {
            "success": False,
            "request_id": g.get("request_id"),
            "error": {"code": code, "message": message, "details": details or {}},
        }
        return jsonify(body), status

    def require_token() -> None:
        """Si WASSAL_API_TOKEN est défini, les POST exigent l'en-tête X-API-Key."""
        token = app.config["API_TOKEN"]
        if token and request.headers.get("X-API-Key") != token:
            raise ApiError("missing or invalid X-API-Key", status=401, code="UNAUTHORIZED")

    # ------------------------------------------------------------- hooks

    @app.before_request
    def _start_request() -> None:
        g.started = time.perf_counter()
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]

    @app.after_request
    def _finish_request(response):
        response.headers["X-Request-ID"] = g.get("request_id", "")
        elapsed = (time.perf_counter() - g.get("started", time.perf_counter())) * 1000
        logger.info("%s %s -> %s (%.0fms) [%s]", request.method, request.path,
                    response.status_code, elapsed, g.get("request_id"))
        return response

    # ------------------------------------------------------ gestion erreurs

    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        return failure(exc.status, exc.code, exc.message, exc.details)

    @app.errorhandler(413)
    def _too_large(_exc):
        mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return failure(413, "PAYLOAD_TOO_LARGE", f"request exceeds {mb} MB")

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):
        code = (exc.name or "HTTP_ERROR").upper().replace(" ", "_")
        return failure(exc.code or 500, code, exc.description or exc.name)

    @app.errorhandler(Exception)
    def _unexpected(exc: Exception):
        logger.exception("Unhandled error [%s]", g.get("request_id"))
        return failure(500, "INTERNAL_ERROR", "unexpected server error")

    # ----------------------------------------------------------------- routes

    @app.get("/")
    def index():
        """Sert l'interface web / serve the web UI."""
        if not (FRONTEND_DIR / "index.html").exists():
            raise ApiError("frontend not found", status=404, code="NOT_FOUND")
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.get("/wassal/test")
    def health():
        """Health check : état de l'API, du modèle et de la base de repères."""
        return success({
            "status": "ok",
            "service": "wassal",
            "version": __version__,
            "asr_model": os.getenv("MOULSOT_MODEL", DEFAULT_MODEL),
            "asr_available": asr_available(),
            "asr_model_loaded": is_model_loaded(),
            "asr_model_status": model_status(),
            "landmarks": resolver.stats(),
            "osm": {city: {k: meta.get(k) for k in ("osm_timestamp", "loaded", "license")}
                    for city, meta in resolver.osm_meta.items()},
            "cities": resolver.cities,
            "min_confidence": app.config["MIN_CONFIDENCE"],
        })

    def _read_command_input() -> Tuple[str, Optional[str], Optional[str], Optional[Dict[str, Any]],
                                       Optional[Tuple[float, float]]]:
        """
        Lit la commande en JSON ou multipart (avec fichier audio).
        Read the command from JSON, or multipart/form-data with an `audio` file.
        Returns (text, city, phone, transcription_result).
        """
        transcription: Optional[Dict[str, Any]] = None
        if request.mimetype == "multipart/form-data":
            fields: Dict[str, Any] = request.form.to_dict()
            audio = request.files.get("audio")
            if audio and audio.filename:
                transcription = _transcribe_upload(audio)
                fields["darija_text"] = transcription["transcription"]
        elif request.is_json:
            fields = request.get_json(silent=True)
            if not isinstance(fields, dict):
                raise ApiError("body must be a valid JSON object", code="INVALID_JSON")
        else:
            raise ApiError("use Content-Type application/json or multipart/form-data",
                           status=415, code="UNSUPPORTED_MEDIA_TYPE")

        text = fields.get("darija_text")
        if not isinstance(text, str) or not text.strip():
            raise ApiError("'darija_text' is required (or upload an 'audio' file)", code="MISSING_TEXT")
        text = text.strip()
        if len(text) > MAX_TEXT_LENGTH:
            raise ApiError(f"'darija_text' exceeds {MAX_TEXT_LENGTH} characters", code="TEXT_TOO_LONG")

        raw_city = fields.get("city")
        city = None
        if raw_city not in (None, ""):
            if not isinstance(raw_city, str):
                raise ApiError("'city' must be a string", code="INVALID_CITY")
            city = resolver.normalize_city(raw_city)
            if city is None:
                raise ApiError(f"unknown city '{raw_city}'", code="INVALID_CITY",
                               details={"supported_cities": resolver.cities})

        phone = normalize_phone(fields.get("phone"))
        user_location = parse_user_location(fields.get("user_lat"), fields.get("user_lng"))
        return text, city, phone, transcription, user_location

    def _transcribe_upload(audio) -> Dict[str, Any]:
        """Sauve l'upload dans un fichier temporaire puis transcrit / save + transcribe."""
        filename = secure_filename(audio.filename) or "audio.wav"
        ext = os.path.splitext(filename)[1].lower() or ".wav"
        if ext not in SUPPORTED_EXTENSIONS:
            raise ApiError(f"unsupported audio format '{ext}'", code="UNSUPPORTED_FORMAT",
                           details={"supported": sorted(SUPPORTED_EXTENSIONS)})
        fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="wassal_")
        os.close(fd)
        try:
            audio.save(tmp_path)
            result = transcribe_darija_audio(tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                logger.warning("Could not delete temp audio %s", tmp_path)
        if result["status"] != "success":
            code = result.get("error_code", "TRANSCRIPTION_FAILED")
            raise ApiError(result.get("error", "transcription failed"),
                           status=_TRANSCRIPTION_STATUS.get(code, 422), code=code)
        return result

    @app.post("/wassal/command")
    def command():
        """
        Traite une commande Darija.
        JSON: {"darija_text": "بغيت تاكسي للقارة", "city": "casablanca", "phone": "0612345678"}
        ou multipart: audio=<fichier>, city=..., phone=...
        """
        text, city, phone, transcription, user_location = _read_command_input()
        logger.info("Command [%s] city=%s phone=%s text=%r",
                    g.request_id, city, mask_phone(phone), text)
        try:
            data = process_command(text, city, phone, resolver, app.config["MIN_CONFIDENCE"], user_location)
        except (TypeError, ValueError) as exc:
            raise ApiError(str(exc), code="INVALID_TEXT") from exc
        if transcription:
            data["transcription"] = {
                k: transcription.get(k) for k in ("transcription", "language", "duration_s", "model")
            }
        return success(data)

    @app.post("/wassal/dispatch")
    def dispatch():
        """
        SIMULATION d'envoi à Yassir : aucun appel réel n'est fait, l'API
        partenaire n'étant pas encore branchée (voir README "Limites connues").
        Ferme la boucle de démonstration en renvoyant un ordre, un chauffeur/
        livreur factice et une ETA, à partir du `yassir_request` de
        `POST /wassal/command`.

        Body: {"yassir_request": {...}}
        """
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ApiError("body must be a valid JSON object", code="INVALID_JSON")
        yassir_request = body.get("yassir_request")
        if not isinstance(yassir_request, dict):
            raise ApiError("'yassir_request' is required (send the object returned by /wassal/command)",
                           code="MISSING_FIELDS")
        missing = [f for f in ("service", "product", "pickup", "dropoff") if not yassir_request.get(f)]
        if missing:
            raise ApiError(f"yassir_request is incomplete, missing: {', '.join(missing)}",
                           code="INCOMPLETE_REQUEST")

        courier = random.choice(_MOCK_COURIERS)
        order_id = f"WSL-{uuid.uuid4().hex[:8].upper()}"
        courier_role = "driver" if yassir_request["service"] == "ride" else "courier"
        logger.info("Dispatch (simulated) [%s] order=%s product=%s",
                    g.request_id, order_id, yassir_request.get("product"))
        return success({
            "simulated": True,
            "note": "Simulation Wassal : aucun appel réel à l'API Yassir (pas encore accessible). "
                    "À brancher sur l'API partenaire une fois la spec connue.",
            "order_id": order_id,
            "status": "dispatched",
            "product": yassir_request.get("product"),
            "eta_minutes": estimate_eta_minutes(yassir_request),
            courier_role: {"name": courier["name"], "vehicle": courier["vehicle"], "plate": courier["plate"]},
            "pickup": yassir_request.get("pickup"),
            "dropoff": yassir_request.get("dropoff"),
        }, status=201)

    @app.get("/wassal/landmarks")
    def list_landmarks():
        """Liste les repères connus / list known landmarks (?city=)."""
        city = request.args.get("city")
        if city and resolver.normalize_city(city) is None:
            raise ApiError(f"unknown city '{city}'", code="INVALID_CITY",
                           details={"supported_cities": resolver.cities})
        try:
            limit = min(max(int(request.args.get("limit", 200)), 1), 1000)
        except ValueError:
            raise ApiError("'limit' must be an integer", code="INVALID_LIMIT") from None
        items = resolver.list_landmarks(city, category=request.args.get("category"),
                                        source=request.args.get("source"), limit=limit)
        return success({"count": len(items), "landmarks": items})

    @app.post("/wassal/landmarks")
    def add_landmark():
        """
        Crowdsourcing : propose un nouveau repère.
        {"name": "Café Atlas", "city": "casablanca", "lat": 33.59, "lng": -7.61, "aliases": ["9hwa atlas"]}
        """
        require_token()
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ApiError("body must be a valid JSON object", code="INVALID_JSON")
        missing = [k for k in ("name", "city", "lat", "lng") if body.get(k) in (None, "")]
        if missing:
            raise ApiError(f"missing fields: {', '.join(missing)}", code="MISSING_FIELDS")
        aliases = body.get("aliases") or []
        if not isinstance(aliases, list):
            raise ApiError("'aliases' must be a list of strings", code="INVALID_ALIASES")
        try:
            landmark = resolver.add_landmark(
                name=str(body["name"]), city=str(body["city"]), lat=body["lat"], lng=body["lng"],
                aliases=aliases, category=str(body.get("category") or "other"),
            )
        except ValueError as exc:
            raise ApiError(str(exc), code="INVALID_LANDMARK") from exc
        except OSError as exc:
            logger.error("Could not persist landmark: %s", exc)
            raise ApiError("could not save landmark", status=500, code="STORAGE_ERROR") from exc
        return success({"landmark": landmark}, status=201)

    return app


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


if __name__ == "__main__":
    _configure_logging()
    port = int(os.getenv("PORT", "5000"))
    host = os.getenv("HOST", "127.0.0.1")
    debug = os.getenv("FLASK_ENV", "production").lower() == "development"

    logger.info("Wassal API v%s on http://%s:%d (debug=%s)", __version__, host, port, debug)
    create_app().run(host=host, port=port, debug=debug)
