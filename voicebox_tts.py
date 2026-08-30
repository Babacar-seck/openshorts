"""Voicebox as the AI Shorts voiceover backend, in place of ElevenLabs.

Voicebox (github.com/jamiepine/voicebox) is a local voice studio: cloned
profiles, no per-character billing, nothing leaving the machine. This module is
the adapter that lets ``saasshorts.generate_voiceover``'s single call site use
it instead.

It lives in its own file on purpose. OpenShorts' upstream moves fast and every
line added to ``saasshorts.py`` is a future rebase conflict, so the whole
integration is this file plus one ternary at the call site.

Off unless ``VOICEBOX_URL`` is set — the ElevenLabs path stays the default, and
a machine with no Voicebox running behaves exactly as before.

    VOICEBOX_URL       http://host.docker.internal:17493
    VOICEBOX_PROFILE   voice profile name (case-insensitive) or id
    VOICEBOX_LANGUAGE  two-letter code Voicebox accepts (default "en")
    VOICEBOX_ENGINE    qwen | kokoro | chatterbox | ... (default: the profile's)
    VOICEBOX_TIMEOUT   seconds to wait for synthesis (default 600)

From inside the backend container the host is ``host.docker.internal``, not
``127.0.0.1`` — loopback there is the container itself.

This replaces *narration*, not the ElevenLabs dubbing in ``translate.py``: that
one translates and re-aligns a finished video, which Voicebox does not do.
"""

import os
import subprocess
import tempfile
from typing import Optional

import httpx

# Voicebox's own default, kept in sync with its backend/models.py
# GenerationRequest so a caller that sets nothing gets Voicebox's behaviour
# rather than ours.
DEFAULT_LANGUAGE = "en"
DEFAULT_TIMEOUT = 600.0

# One GET /profiles per process is enough: the id behind a name does not change
# under us, and a voiceover call already costs minutes of synthesis.
_profile_id_cache: dict = {}


def base_url() -> Optional[str]:
    """The configured Voicebox base URL, or None when the feature is off."""
    url = (os.getenv("VOICEBOX_URL") or "").strip().rstrip("/")
    return url or None


def is_configured() -> bool:
    """True when VOICEBOX_URL is set. Does not probe — the server may be down."""
    return base_url() is not None


def list_profiles() -> list:
    """Voice profiles known to Voicebox: [{id, name, language, ...}, ...]."""
    url = base_url()
    if not url:
        raise RuntimeError("VOICEBOX_URL is not set")

    with httpx.Client(timeout=15.0) as client:
        resp = client.get(f"{url}/profiles")
        if resp.status_code != 200:
            raise Exception(
                f"Voicebox /profiles error ({resp.status_code}): {resp.text[:300]}"
            )
        return resp.json()


def resolve_profile_id(profile: Optional[str] = None) -> str:
    """Turn a profile name or id into an id.

    Falls back to ``VOICEBOX_PROFILE``, then — only when Voicebox holds exactly
    one profile — to that one. Anything else raises with the available names
    rather than silently narrating in a voice nobody chose.
    """
    wanted = (profile or os.getenv("VOICEBOX_PROFILE") or "").strip()

    if wanted and wanted in _profile_id_cache:
        return _profile_id_cache[wanted]

    profiles = list_profiles()
    if not profiles:
        raise Exception(
            "Voicebox has no voice profiles. Clone or design one in the app first."
        )

    if not wanted:
        if len(profiles) == 1:
            return profiles[0]["id"]
        names = ", ".join(p.get("name", p["id"]) for p in profiles)
        raise Exception(
            f"VOICEBOX_PROFILE is not set and Voicebox holds several profiles: {names}"
        )

    for p in profiles:
        if p["id"] == wanted or p.get("name", "").lower() == wanted.lower():
            _profile_id_cache[wanted] = p["id"]
            return p["id"]

    names = ", ".join(p.get("name", p["id"]) for p in profiles)
    raise Exception(f"Voicebox profile '{wanted}' not found. Available: {names}")


def generate_voiceover(
    text: str,
    elevenlabs_key: Optional[str] = None,
    output_path: str = "voiceover.wav",
    voice_id: Optional[str] = None,
    language: Optional[str] = None,
) -> str:
    """Generate voiceover audio with Voicebox, writing a WAV to output_path.

    The four leading parameters mirror ``saasshorts.generate_voiceover`` on
    purpose so the swap at the call site stays a single expression.
    ``elevenlabs_key`` is ignored (nothing here talks to ElevenLabs) and
    ``voice_id`` is accepted as an override of ``VOICEBOX_PROFILE`` — an
    ElevenLabs voice id will never match a Voicebox profile, so a caller that
    passes its default through unchanged gets the configured profile instead of
    a confusing failure.

    ``language`` is the job's script language and the call site binds it with
    functools.partial, which keeps the positional signature identical to the
    ElevenLabs one. It wins over ``VOICEBOX_LANGUAGE``, which is now only the
    default for callers that have no script: an env var cannot know what
    language this particular script was written in, and the mismatch narrates
    English text with French phonetics without ever failing.

    The code is passed to Voicebox as-is rather than checked against a copy of
    its accepted list — Voicebox validates it and answers a clear 422, and a
    second list here would be one more thing to drift.
    """
    url = base_url()
    if not url:
        raise RuntimeError("VOICEBOX_URL is not set")

    # A stale ElevenLabs default id must not become a hard failure: an
    # unresolvable override falls back to the configured profile. A missing
    # configuration still raises — that one is the operator's to fix.
    try:
        profile_id = resolve_profile_id(voice_id)
    except Exception:
        if not voice_id:
            raise
        profile_id = resolve_profile_id(None)

    body = {
        "profile_id": profile_id,
        "text": text,
        "language": (language or os.getenv("VOICEBOX_LANGUAGE") or DEFAULT_LANGUAGE).strip(),
        "normalize": True,
    }
    engine = (os.getenv("VOICEBOX_ENGINE") or "").strip()
    if engine:
        body["engine"] = engine

    try:
        timeout = float(os.getenv("VOICEBOX_TIMEOUT") or DEFAULT_TIMEOUT)
    except ValueError:
        timeout = DEFAULT_TIMEOUT

    # The language is in the log on purpose: a wrong one does not fail, it
    # narrates the text with another language's phonetics, and that is only
    # noticeable by listening unless it is written down.
    print(
        f"[Voicebox] 🎙️ Generating voiceover ({len(text)} chars, "
        f"lang={body['language']}) on {url}..."
    )

    # Voicebox always answers WAV, while the caller names the file .mp3
    # (saasshorts.py builds "<slug>_voice.mp3"). Writing WAV bytes under that
    # name would upload them to fal's CDN declared as audio/mpeg, and the
    # lip-sync model would be handed a file that is not what it says it is —
    # a failure that only surfaces once a real, billed fal key is in play.
    wants_mp3 = os.path.splitext(output_path)[1].lower() == ".mp3"
    wav_path = output_path
    tmp_wav = None
    if wants_mp3:
        fd, tmp_wav = tempfile.mkstemp(suffix=".wav", dir=os.path.dirname(output_path) or None)
        os.close(fd)
        wav_path = tmp_wav

    try:
        # /generate/stream synthesises and streams the WAV back in one call — no
        # polling, and nothing added to the user's Voicebox history for what is
        # an intermediate render artifact. It chunks long text itself.
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", f"{url}/generate/stream", json=body) as resp:
                if resp.status_code != 200:
                    detail = resp.read().decode(errors="replace")[:300]
                    raise Exception(f"Voicebox TTS error ({resp.status_code}): {detail}")

                with open(wav_path, "wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)

        if os.path.getsize(wav_path) == 0:
            raise Exception("Voicebox returned an empty audio file")

        if wants_mp3:
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame",
                 "-q:a", "2", output_path],
                capture_output=True,
            )
            if proc.returncode != 0 or not os.path.exists(output_path):
                detail = proc.stderr.decode(errors="replace")[-300:]
                raise Exception(f"Voicebox WAV → MP3 conversion failed: {detail}")
    finally:
        if tmp_wav and os.path.exists(tmp_wav):
            os.remove(tmp_wav)

    print(f"[Voicebox] ✅ Voiceover: {output_path}")
    return output_path
