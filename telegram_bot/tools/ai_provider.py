"""Provider-agnostic transport to the AI vendors used by the Telegram bot.

Owns every HTTP call to a language-model vendor: JSON chat completions to turn
text into structured data, and speech-to-text for voice notes. Fail-soft
throughout: any error path returns ``None`` so each caller degrades to its own
manual flow.
"""

import base64
import json
import logging

import requests

_logger = logging.getLogger(__name__)

CHAT_TIMEOUT = 25  # seconds
TRANSCRIBE_TIMEOUT = 30  # seconds — voice files take longer to upload

# Vendors differ on two independent axes, so each is declared once here instead
# of being branched on at the call site.
#
# The chat wire collapses into two families:
#   openai      POST /chat/completions, Bearer token, response_format
#               json_object. Covers Groq, OpenAI, DeepSeek, Moonshot/Kimi and
#               Gemini through its OpenAI-compatible layer.
#   anthropic   POST /v1/messages, x-api-key header. The system prompt travels
#               in its own key and there is no response_format, so JSON is
#               requested in the prompt and any code fence stripped after.
#
# Audio does NOT follow the chat wire: Groq and OpenAI expose a Whisper-style
# multipart endpoint, Gemini takes base64 audio inline in generateContent, and
# DeepSeek, Moonshot and Anthropic cannot transcribe at all. Those report
# supports_audio False so the caller asks the user to type instead of failing
# mid-flow.
#
# Adding a vendor that speaks either wire is an entry here, not a code change.
# groq, gemini and moonshot were verified against a live key in the reference
# implementation; openai, deepseek and anthropic are wired from
# vendor-documented shapes.
#
# Optional per-vendor keys, each added because a live probe demanded it:
#   extra           literal body overrides, applied last, so they beat the
#                   caller's own values (a vendor that only accepts one
#                   temperature wins over our determinism preference)
#   min_max_tokens  floor for the token cap, for models that bill reasoning
#                   against it
#   chat_timeout    seconds to wait, for vendors slower than the shared default
#   audio_timeout   same, for transcription: a vendor that answers from the
#                   chat wire quickly can still be slow on audio
#   audio           None when the vendor cannot transcribe at all
PROVIDERS = {
    "groq": {
        "label": "Groq",
        "wire": "openai",
        "chat_url": "https://api.groq.com/openai/v1/chat/completions",
        "chat_model": "llama-3.3-70b-versatile",
        "audio": "whisper",
        "audio_url": "https://api.groq.com/openai/v1/audio/transcriptions",
        "audio_model": "whisper-large-v3-turbo",
    },
    "gemini": {
        "label": "Google Gemini",
        "wire": "openai",
        "chat_url": (
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        ),
        # Reasoning is on by default on the flagship models and billed against
        # max_tokens: flash-latest burned a whole 600-token budget thinking and
        # returned truncated JSON. The lite models spend none, so the two floors
        # below are not for this default — they protect a hand-picked bigger one.
        # "none" is rejected with HTTP 400 here, so "low" is the lowest setting.
        "chat_model": "gemini-3.5-flash-lite",
        "extra": {"reasoning_effort": "low"},
        "min_max_tokens": 2000,
        "audio": "gemini_inline",
        "audio_url": (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent"
        ),
        # flash-lite, not flash-latest: the flagship reasons before transcribing
        # and literal transcription does not need that. thinkingConfig
        # .thinkingBudget=0 is NOT the alternative: the 3.x models reject it
        # with HTTP 400, and lite does not reason by default.
        "audio_model": "gemini-flash-lite-latest",
        # This endpoint carries the audio inline and its wall clock swings far
        # wider than the Whisper ones — the same file came back in 1.3s and in
        # 62.6s on re-runs. At the shared 30s default the slow half
        # was reported to the caller as an untranscribable note.
        "audio_timeout": 90,
    },
    "openai": {
        "label": "OpenAI",
        "wire": "openai",
        "chat_url": "https://api.openai.com/v1/chat/completions",
        "chat_model": "gpt-4o-mini",
        "audio": "whisper",
        "audio_url": "https://api.openai.com/v1/audio/transcriptions",
        "audio_model": "whisper-1",
    },
    "deepseek": {
        "label": "DeepSeek",
        "wire": "openai",
        "chat_url": "https://api.deepseek.com/v1/chat/completions",
        "chat_model": "deepseek-chat",
        "audio": None,
    },
    "moonshot": {
        "label": "Moonshot (Kimi)",
        "wire": "openai",
        "chat_url": "https://api.moonshot.ai/v1/chat/completions",
        # Verified 2026-07-28 against a paid key. Kimi model ids move fast
        # (k2.6, k2.7-code, k3 all live at once and none is an alias), so the
        # per-bot model override is the escape hatch when this one retires.
        "chat_model": "kimi-k3",
        # Kimi rejects any temperature other than 1 with HTTP 400
        # ("only 1 is allowed for this model"), so the vendor wins over the
        # caller's determinism preference — there is no alternative.
        "extra": {"temperature": 1},
        # Reasoning model: at max_tokens=600 it spent the entire budget
        # thinking and returned empty content (finish_reason=length). Measured
        # need at 2000: 477 output tokens in 17s. max_tokens is a cap, not a
        # spend, so a floor costs nothing when the model stops early.
        "min_max_tokens": 2000,
        # Measured 17-28s depending on model; the 25s shared default cut it off.
        "chat_timeout": 60,
        "audio": None,
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "wire": "anthropic",
        "chat_url": "https://api.anthropic.com/v1/messages",
        # Two releases newer than the claude-sonnet-4-5 this defaulted to
        # (4.5 → 4.6 → 5). That id still works — it is active, not
        # deprecated — so this is a default-quality bump, not a break.
        # Model ids here carry no date suffix; do not add one.
        "chat_model": "claude-sonnet-5",
        "audio": None,
    },
}

ANTHROPIC_VERSION = "2023-06-01"


def provider_selection():
    """Build the ``Selection`` values for the per-bot provider field.

    :return: list of ``(code, label)`` tuples in catalog order
    :rtype: list
    """
    return [(code, spec["label"]) for code, spec in PROVIDERS.items()]


def _strip_json_fence(text):
    """Remove a markdown code fence wrapping a JSON payload.

    Vendors without a native JSON mode (Anthropic) often answer with
    ```json ... ``` despite being told not to.

    :param str text: raw assistant content
    :return: content without the surrounding fence
    :rtype: str
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped.split("\n", 1)[-1]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3]
    return body.strip()


class AiProvider:
    """Transport bound to one vendor, one API key and one model."""

    def __init__(self, provider, api_key, model=None):
        """Resolve the vendor spec and the effective model.

        :param str provider: key of ``PROVIDERS`` (empty / unknown = unusable)
        :param str api_key: vendor API key
        :param str model: optional model override; falls back to the vendor
            default, which every catalog vendor ships
        """
        self._name = provider or ""
        self._spec = PROVIDERS.get(self._name) or {}
        self._api_key = (api_key or "").strip()
        self._model = (model or "").strip() or self._spec.get("chat_model") or ""

    @property
    def label(self):
        """Human-readable vendor name, or the raw code when unknown."""
        return self._spec.get("label") or self._name or "unset"

    @property
    def model(self):
        """Effective chat model: the override, or the vendor default."""
        return self._model

    @property
    def configured(self):
        """``True`` when vendor, key and model are all resolved."""
        return bool(self._spec and self._api_key and self._model)

    @property
    def supports_audio(self):
        """``True`` when this vendor can transcribe voice notes."""
        return bool(self._spec.get("audio"))

    @property
    def _audio_timeout(self):
        """Seconds to wait for a transcription, per vendor."""
        return self._spec.get("audio_timeout") or TRANSCRIBE_TIMEOUT

    @property
    def _chat_timeout(self):
        """Seconds to wait for a completion, per vendor."""
        return self._spec.get("chat_timeout") or CHAT_TIMEOUT

    def _token_budget(self, max_tokens):
        """Raise the caller's token cap to this vendor's floor.

        Reasoning models bill their thinking against ``max_tokens``, so a cap
        tuned for a non-reasoning model returns empty or truncated content. The
        cap is not a spend, so lifting it is free when the model stops early.

        :param int max_tokens: cap requested by the caller
        :return: cap actually sent to the vendor
        :rtype: int
        """
        return max(max_tokens, self._spec.get("min_max_tokens") or 0)

    def chat_json(self, system, user, max_tokens, temperature):
        """Request a JSON object from the model, fail-soft on every error.

        :param str system: system prompt (instructions and output schema)
        :param str user: user content to act on
        :param int max_tokens: completion cap
        :param float temperature: sampling temperature
        :return: raw assistant content with any code fence stripped, or None
        :rtype: str | None
        """
        if not self.configured:
            return None
        if self._spec["wire"] == "anthropic":
            return self._chat_anthropic(system, user, max_tokens, temperature)
        return self._chat_openai(system, user, max_tokens, temperature)

    def _chat_openai(self, system, user, max_tokens, temperature):
        """Call an OpenAI-compatible ``/chat/completions`` endpoint.

        :param str system: system prompt
        :param str user: user content
        :param int max_tokens: completion cap
        :param float temperature: sampling temperature
        :return: assistant content, or None
        :rtype: str | None
        """
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": self._token_budget(max_tokens),
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        body.update(self._spec.get("extra") or {})
        payload = self._post_json(
            self._spec["chat_url"],
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            body,
            timeout=self._chat_timeout,
        )
        if payload is None:
            return None
        try:
            choice = payload["choices"][0]
        except (KeyError, IndexError, TypeError):
            _logger.exception("%s response malformed", self.label)
            return None
        content = (choice.get("message") or {}).get("content")
        finish = choice.get("finish_reason")
        # A reasoning model can spend the whole max_tokens budget thinking and
        # come back with finish_reason=length and empty or truncated content.
        # Logging the reason is what distinguishes that from a vendor outage.
        if not content or finish == "length":
            _logger.warning(
                "%s returned no usable content (finish_reason=%s, model=%s); "
                "raise max_tokens or lower the reasoning effort",
                self.label,
                finish,
                self._model,
            )
            return None
        return _strip_json_fence(content)

    def _chat_anthropic(self, system, user, max_tokens, temperature):
        """Call Anthropic's ``/v1/messages`` endpoint.

        :param str system: system prompt (own key, not a message)
        :param str user: user content
        :param int max_tokens: completion cap
        :param float temperature: sampling temperature
        :return: assistant content, or None
        :rtype: str | None
        """
        body = {
            "model": self._model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": self._token_budget(max_tokens),
            "temperature": temperature,
        }
        body.update(self._spec.get("extra") or {})
        payload = self._post_json(
            self._spec["chat_url"],
            {
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            },
            body,
            timeout=self._chat_timeout,
        )
        if payload is None:
            return None
        try:
            blocks = payload["content"]
        except (KeyError, TypeError):
            _logger.exception("%s response malformed", self.label)
            return None
        text = "".join(
            block.get("text") or ""
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        # Same failure mode as the OpenAI wire, under a different name: the
        # budget runs out mid-answer and the JSON arrives truncated.
        stop = payload.get("stop_reason")
        if not text or stop == "max_tokens":
            _logger.warning(
                "%s returned no usable content (stop_reason=%s, model=%s); "
                "raise max_tokens or lower the reasoning effort",
                self.label,
                stop,
                self._model,
            )
            return None
        return _strip_json_fence(text)

    def transcribe(self, audio_bytes, filename, language="es", prompt=None):
        """Transcribe an audio file to text, fail-soft on every error.

        :param bytes audio_bytes: raw audio file content
        :param str filename: upload file name (vendors sniff the extension)
        :param str language: ISO language hint
        :param str prompt: optional domain-vocabulary hint
        :return: transcript text, or None
        :rtype: str | None
        """
        if not self.configured or not self.supports_audio or not audio_bytes:
            return None
        if self._spec["audio"] == "gemini_inline":
            return self._transcribe_gemini(audio_bytes, filename, language, prompt)
        return self._transcribe_whisper(audio_bytes, filename, language, prompt)

    def _transcribe_whisper(self, audio_bytes, filename, language, prompt):
        """Transcribe via a Whisper-style multipart endpoint.

        :param bytes audio_bytes: raw audio file content
        :param str filename: upload file name
        :param str language: ISO language hint
        :param str prompt: optional domain-vocabulary hint
        :return: transcript text, or None
        :rtype: str | None
        """
        data = {
            "model": self._spec["audio_model"],
            "language": language,
            "response_format": "text",
        }
        if prompt:
            data["prompt"] = prompt
        try:
            response = requests.post(
                self._spec["audio_url"],
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"file": (filename, audio_bytes, _audio_mimetype(filename))},
                data=data,
                timeout=self._audio_timeout,
            )
        except requests.RequestException:
            _logger.exception("%s transcription failed (network)", self.label)
            return None
        if response.status_code != 200:
            _logger.warning(
                "%s transcription failed (HTTP %s): %s",
                self.label,
                response.status_code,
                response.text[:300],
            )
            return None
        return (response.text or "").strip() or None

    def _transcribe_gemini(self, audio_bytes, filename, language, prompt):
        """Transcribe via Gemini ``generateContent`` with inline base64 audio.

        Gemini has no Whisper-style endpoint, so the audio rides inside the
        prompt as ``inline_data`` and the instruction asks for a bare
        transcript.

        :param bytes audio_bytes: raw audio file content
        :param str filename: upload file name (used to pick the mime type)
        :param str language: ISO language hint
        :param str prompt: optional domain-vocabulary hint
        :return: transcript text, or None
        :rtype: str | None
        """
        instruction = (
            f"Transcribe literalmente el audio en idioma '{language}'. "
            "Responde SOLO con la transcripción, sin comillas ni comentarios."
        )
        if prompt:
            instruction += f" Contexto de vocabulario: {prompt}"
        model = self._spec["audio_model"]
        body = {
            "contents": [
                {
                    "parts": [
                        {"text": instruction},
                        {
                            "inline_data": {
                                "mime_type": _audio_mimetype(filename),
                                "data": base64.b64encode(audio_bytes).decode(),
                            }
                        },
                    ]
                }
            ]
        }
        payload = self._post_json(
            self._spec["audio_url"].format(model=model),
            {"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
            body,
            timeout=self._audio_timeout,
        )
        if payload is None:
            return None
        try:
            parts = payload["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError):
            _logger.warning(
                "%s transcription returned no candidates: %s",
                self.label,
                json.dumps(payload)[:300],
            )
            return None
        # Thought parts carry a `text` key like answer parts, so a plain
        # `.get("text")` join would splice the model's reasoning into the
        # transcript. They are opt-in — this request never sets
        # thinkingConfig.includeThoughts — so the guard is here to keep that
        # true if the body ever asks for them, not to fix a live leak.
        text = "".join(
            part.get("text") or ""
            for part in parts
            if isinstance(part, dict) and not part.get("thought")
        )
        return text.strip() or None

    def _post_json(self, url, headers, body, timeout=CHAT_TIMEOUT):
        """POST a JSON body and decode the JSON response, fail-soft.

        :param str url: target endpoint
        :param dict headers: request headers
        :param dict body: JSON-serializable request body
        :param int timeout: seconds before giving up
        :return: decoded response payload, or None on any failure
        :rtype: dict | None
        """
        try:
            response = requests.post(
                url, headers=headers, data=json.dumps(body), timeout=timeout
            )
        except requests.RequestException:
            _logger.exception("%s call failed (network)", self.label)
            return None
        if response.status_code != 200:
            _logger.warning(
                "%s call failed (HTTP %s): %s",
                self.label,
                response.status_code,
                response.text[:300],
            )
            return None
        try:
            return response.json()
        except ValueError:
            _logger.exception("%s returned non-JSON body", self.label)
            return None


def _audio_mimetype(filename):
    """Map an audio file name to the mime type vendors expect.

    :param str filename: upload file name
    :return: mime type string, defaulting to ``audio/ogg``
    :rtype: str
    """
    lowered = (filename or "").lower()
    for suffix, mimetype in (
        (".mp3", "audio/mpeg"),
        (".m4a", "audio/mp4"),
        (".mp4", "audio/mp4"),
        (".wav", "audio/wav"),
        (".flac", "audio/flac"),
        (".webm", "audio/webm"),
    ):
        if lowered.endswith(suffix):
            return mimetype
    return "audio/ogg"
