"""
Transcription audio Darija avec MoulSot v0.3.
Darija speech-to-text with MoulSot v0.3 (HuggingFace, Apache 2.0).

MoulSot v0.3 utilise l'architecture Qwen3-ASR ("Qwen3ASRForConditionalGeneration"),
qui n'est pas (encore) reconnue par transformers.pipeline(). Le loader officiel,
documenté sur la fiche du modèle (huggingface.co/atlasia/moulsot.v0.3), est le
paquet `qwen_asr` de l'auteur du modèle. C'est celui-ci qu'on utilise ici.

MoulSot v0.3 uses the Qwen3-ASR architecture, which is not (yet) recognized by
transformers.pipeline(). The model card's official loader is the model author's
own `qwen_asr` package, used here instead.

Le modèle est chargé paresseusement (au premier appel) et mis en cache :
l'API texte fonctionne même si qwen_asr n'est pas installé.

The model is loaded lazily on first use and cached, so the text-only API
works even when qwen_asr is not installed.
"""

import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

try:
    from .normalize import detect_language
except ImportError:  # pragma: no cover - direct script execution
    from normalize import detect_language

logger = logging.getLogger(__name__)

# "01Yassine/moulsot.v0.3" (nom donné dans le brief) redirige vers ce dépôt
# canonique sur HuggingFace. "01Yassine/..." redirects here.
DEFAULT_MODEL = "atlasia/moulsot.v0.3"
TARGET_SAMPLE_RATE = 16_000  # Taux attendu par Qwen3-ASR / rate expected by the model
SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".webm", ".aac", ".opus", ".mp4"}
MAX_FILE_BYTES = 25 * 1024 * 1024

# Langue passée à model.transcribe(). Le modèle est spécialisé Darija (ary)
# mais son vocabulaire de langues suit celui de Qwen3-ASR ; "Arabic" couvre
# le darija et le code-switching arabe standard/français observés dans les
# benchmarks de la fiche modèle.
DEFAULT_LANGUAGE = "Arabic"


class TranscriptionError(Exception):
    """Erreur de transcription / transcription failure."""


class ModelUnavailableError(TranscriptionError):
    """Le modèle ou ses dépendances ne peuvent pas être chargés."""


_model: Any = None
_model_lock = threading.Lock()


def _model_name() -> str:
    return os.getenv("MOULSOT_MODEL", DEFAULT_MODEL)


def _language() -> str:
    return os.getenv("ASR_LANGUAGE", DEFAULT_LANGUAGE)


def _max_audio_seconds() -> float:
    try:
        return float(os.getenv("MAX_AUDIO_SECONDS", "60"))
    except ValueError:
        return 60.0


def _resolve_device() -> str:
    """
    DEVICE=auto|cpu|cuda|mps -> device_map passé à Qwen3ASRModel.from_pretrained.
    "auto" choisit le GPU si disponible / picks GPU when available.

    torch n'est pas dans nos dépendances directes mais est installé de façon
    transitive par qwen-asr ; l'import est donc protégé au cas où.
    torch is a transitive dependency of qwen-asr, not a direct one, so the
    import here is guarded just in case.
    """
    requested = os.getenv("DEVICE", "auto").strip().lower()
    if requested != "auto":
        return requested
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def is_model_loaded() -> bool:
    """Indique si le modèle est déjà en mémoire / whether the model is loaded."""
    return _model is not None


def get_asr_model() -> Any:
    """
    Charge (une seule fois) le modèle MoulSot via qwen_asr.
    Load the MoulSot model through qwen_asr once (thread-safe) and return it.

    Raises:
        ModelUnavailableError: dépendance manquante ou chargement impossible.
    """
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from qwen_asr import Qwen3ASRModel
        except ImportError as exc:
            raise ModelUnavailableError(
                "qwen-asr not installed: pip install -r requirements.txt"
            ) from exc

        model_name = _model_name()
        device = _resolve_device()
        dtype = "bfloat16" if device.startswith("cuda") else "float32"
        logger.info("Loading ASR model %s on %s (%s) ...", model_name, device, dtype)
        started = time.perf_counter()
        try:
            _model = Qwen3ASRModel.from_pretrained(
                model_name,
                dtype=dtype,
                device_map=device,
                token=os.getenv("HF_TOKEN") or None,
            )
        except Exception as exc:  # réseau, modèle introuvable, mémoire...
            raise ModelUnavailableError(f"could not load model '{model_name}': {exc}") from exc
        logger.info("ASR model loaded in %.1fs", time.perf_counter() - started)
        return _model


def _decode_with_pyav(audio_path: str) -> "Any":
    """
    Décode n'importe quel format (m4a/AAC, webm/Opus, mp3...) en 16 kHz mono float32.
    Decode any container/codec to 16 kHz mono float32 with PyAV, which bundles
    its own ffmpeg libraries (no system ffmpeg needed).
    """
    try:
        import av
        import numpy as np
    except ImportError as exc:
        raise ModelUnavailableError("PyAV not installed: pip install -r requirements.txt") from exc

    chunks = []
    try:
        with av.open(audio_path) as container:
            if not container.streams.audio:
                raise TranscriptionError("file contains no audio stream")
            resampler = av.AudioResampler(format="flt", layout="mono", rate=TARGET_SAMPLE_RATE)
            for frame in container.decode(audio=0):
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray().reshape(-1))
            for out in resampler.resample(None):  # vide le tampon / flush
                chunks.append(out.to_ndarray().reshape(-1))
    except TranscriptionError:
        raise
    except Exception as exc:  # fichier corrompu / corrupted file
        raise TranscriptionError(f"could not decode audio: {exc}") from exc
    if not chunks:
        raise TranscriptionError("could not decode any audio samples")
    return np.concatenate(chunks).astype(np.float32)


def load_audio(audio_path: str) -> Tuple["Any", int]:
    """
    Charge l'audio en mono float32, quel que soit le format.
    Load audio as mono float32 samples, whatever the format.

    On décode nous-mêmes au lieu de passer le chemin à qwen_asr : celui-ci
    utilise librosa.load, qui ne lit pas le m4a/webm sans ffmpeg installé.
    We decode ourselves instead of passing the path to qwen_asr, whose
    librosa.load cannot read m4a/webm without a system ffmpeg.

    - wav, flac, ogg, mp3 : libsndfile (soundfile)
    - m4a (AAC), webm (Opus)... : PyAV, rééchantillonné en 16 kHz

    Returns:
        (samples, sample_rate) ; qwen_asr rééchantillonne lui-même si besoin.
    """
    try:
        import soundfile as sf
    except ImportError as exc:
        raise ModelUnavailableError("soundfile not installed: pip install -r requirements.txt") from exc

    try:
        data, sr = sf.read(audio_path, dtype="float32", always_2d=True)
        if data.shape[0] > 0:
            return data.mean(axis=1), int(sr)
    except Exception:  # format non géré par libsndfile -> PyAV
        logger.debug("libsndfile cannot read %s, decoding with PyAV", audio_path)

    return _decode_with_pyav(audio_path), TARGET_SAMPLE_RATE


def _extract_text(output: Any) -> str:
    """qwen_asr renvoie une liste d'ASRTranscription (une par audio)."""
    results = output if isinstance(output, (list, tuple)) else [output]
    return "".join(getattr(r, "text", None) or "" for r in results).strip()


def transcribe_darija_audio(audio_path: str) -> Dict[str, Any]:
    """
    Transcrit un fichier audio Darija en texte.
    Transcribe a local Darija audio file (supports Darija/French/Arabic
    code-switching, as handled by MoulSot).

    Args:
        audio_path: chemin local (.wav, .mp3, .flac, .ogg, .m4a, .webm).

    Returns:
        Succès : {"status": "success", "transcription": str, "language": str,
                  "duration_s": float, "model": str, "processing_ms": int}
        Échec  : {"status": "error", "transcription": "", "language": None,
                  "error": str, "error_code": str}

    Never raises: every failure is reported in the returned dict.
    """
    started = time.perf_counter()

    def _error(code: str, message: str) -> Dict[str, Any]:
        logger.warning("Transcription failed (%s): %s", code, message)
        return {"status": "error", "transcription": "", "language": None, "error": message, "error_code": code}

    # --- Validation du fichier / file validation ---
    if not audio_path or not isinstance(audio_path, str):
        return _error("INVALID_PATH", "audio_path must be a non-empty string")
    if not os.path.isfile(audio_path):
        return _error("FILE_NOT_FOUND", f"audio file not found: {audio_path}")
    ext = os.path.splitext(audio_path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return _error("UNSUPPORTED_FORMAT", f"unsupported format '{ext}', use {sorted(SUPPORTED_EXTENSIONS)}")
    size = os.path.getsize(audio_path)
    if size == 0:
        return _error("EMPTY_AUDIO", "audio file is empty")
    if size > MAX_FILE_BYTES:
        return _error("FILE_TOO_LARGE", f"audio file exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB")

    try:
        samples, sample_rate = load_audio(audio_path)
        duration = len(samples) / sample_rate
        if duration < 0.3:
            return _error("EMPTY_AUDIO", "audio is too short (< 0.3s)")
        if duration > _max_audio_seconds():
            return _error("AUDIO_TOO_LONG", f"audio exceeds {_max_audio_seconds():.0f}s")

        model = get_asr_model()
        output = model.transcribe(audio=(samples, sample_rate), language=_language())
        text = _extract_text(output)
    except ModelUnavailableError as exc:
        return _error("MODEL_UNAVAILABLE", str(exc))
    except TranscriptionError as exc:
        return _error("DECODE_ERROR", str(exc))
    except Exception as exc:  # erreur d'inférence inattendue / unexpected inference error
        logger.exception("Unexpected ASR error")
        return _error("INFERENCE_ERROR", f"inference failed: {exc}")

    if not text:
        return _error("NO_SPEECH", "no speech detected")

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info("Transcribed %.1fs of audio in %dms: %r", duration, elapsed_ms, text)
    return {
        "status": "success",
        "transcription": text,
        "language": detect_language(text),
        "duration_s": round(duration, 2),
        "model": _model_name(),
        "processing_ms": elapsed_ms,
    }


def warmup() -> Optional[str]:
    """Précharge le modèle au démarrage / preload model. Returns an error or None."""
    try:
        get_asr_model()
        return None
    except ModelUnavailableError as exc:
        logger.warning("ASR warmup failed: %s", exc)
        return str(exc)


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(sys.argv) != 2:
        print("Usage: python src/transcribe.py <audio_file>")
        sys.exit(1)
    print(json.dumps(transcribe_darija_audio(sys.argv[1]), ensure_ascii=False, indent=2))
