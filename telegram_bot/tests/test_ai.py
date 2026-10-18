"""Tests for the AI vendor transport and the bot's AI service (no network)."""

import json
from unittest.mock import Mock, patch

import requests

from odoo.exceptions import AccessError
from odoo.tests import Form, new_test_user
from odoo.tests.common import TransactionCase

from odoo.addons.telegram_bot.tools import (
    PROVIDERS,
    AiProvider,
    provider_selection,
)


class TestAiProvider(TransactionCase):
    """Unit tests for the vendor transport layer (no network)."""

    def test_selection_matches_catalog(self):
        """The Selection helper is generated from the catalog."""
        self.assertEqual(set(dict(provider_selection())), set(PROVIDERS))

    def test_unknown_provider_is_not_configured(self):
        """An unknown vendor code never reports itself as usable."""
        provider = AiProvider("nope", "some-key")
        self.assertFalse(provider.configured)
        self.assertFalse(provider.supports_audio)
        self.assertIsNone(provider.chat_json("sys", "user", 10, 0.1))

    def test_missing_key_is_not_configured(self):
        """A known vendor without a key is unusable."""
        self.assertFalse(AiProvider("groq", "").configured)

    def test_vendor_default_model_applies(self):
        """An empty model override falls back to the vendor default."""
        self.assertEqual(
            AiProvider("groq", "key").model, PROVIDERS["groq"]["chat_model"]
        )

    def test_model_override_wins(self):
        """An explicit model override replaces the vendor default."""
        self.assertEqual(AiProvider("groq", "key", "other-model").model, "other-model")

    def test_every_vendor_ships_a_default_model(self):
        """A key alone is enough to use any catalog vendor."""
        for code in PROVIDERS:
            self.assertTrue(
                AiProvider(code, "key").configured,
                f"Vendor {code} must ship a default model.",
            )

    def test_token_budget_floor_applies(self):
        """Reasoning vendors lift the caller's token cap to their floor."""
        self.assertEqual(AiProvider("moonshot", "key")._token_budget(600), 2000)
        self.assertEqual(AiProvider("moonshot", "key")._token_budget(4000), 4000)
        self.assertEqual(AiProvider("groq", "key")._token_budget(600), 2000)
        self.assertEqual(AiProvider("openai", "key")._token_budget(600), 600)

    @staticmethod
    def _sent_body(provider_code, max_tokens=600, temperature=0.1):
        """Return the JSON body a chat_json call actually puts on the wire.

        :param str provider_code: key of PROVIDERS
        :param int max_tokens: cap the caller asks for
        :param float temperature: temperature the caller asks for
        :return: decoded request body
        :rtype: dict
        """
        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "content": [{"type": "text", "text": "{}"}],
            }
            AiProvider(provider_code, "key").chat_json(
                "sys", "user", max_tokens, temperature
            )
        return json.loads(mock_post.call_args.kwargs["data"])

    def test_vendor_extra_overrides_caller_in_sent_body(self):
        """A vendor-mandated body value replaces the caller's on the wire."""
        self.assertEqual(
            self._sent_body("moonshot", temperature=0.1)["temperature"],
            1,
            "Kimi rejects any other temperature, so the vendor must win over "
            "the 0.1 the caller asked for.",
        )
        self.assertEqual(
            self._sent_body("openai", temperature=0.1)["temperature"],
            0.1,
            "A vendor without an override must not alter the caller's value.",
        )

    def test_token_floor_reaches_the_sent_body(self):
        """The reasoning floor lifts the cap on the wire, not just in _token_budget."""
        self.assertEqual(
            self._sent_body("moonshot", max_tokens=600)["max_tokens"],
            2000,
            "Without the floor the model spends the budget thinking and "
            "returns empty content.",
        )
        self.assertEqual(
            self._sent_body("openai", max_tokens=600)["max_tokens"],
            600,
            "A vendor without a floor must keep the caller's cap.",
        )

    def test_audio_capability_per_vendor(self):
        """supports_audio reflects the vendor, not the chat wire."""
        self.assertTrue(AiProvider("groq", "key").supports_audio)
        self.assertTrue(AiProvider("gemini", "key").supports_audio)
        self.assertFalse(AiProvider("anthropic", "key").supports_audio)
        self.assertFalse(AiProvider("deepseek", "key").supports_audio)

    def test_transcribe_excludes_thinking_parts(self):
        """Thought parts stay out of the transcript if a body ever requests them."""
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Debo transcribir literalmente…", "thought": True},
                            {"text": "Compra de tres llantas, doce mil pesos."},
                        ]
                    }
                }
            ]
        }
        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = payload
            transcript = AiProvider("gemini", "key").transcribe(b"audio", "voice.oga")
        self.assertEqual(transcript, "Compra de tres llantas, doce mil pesos.")

    def test_audio_timeout_per_vendor(self):
        """A vendor slower on audio than on chat gets its own wait."""
        self.assertEqual(AiProvider("gemini", "key")._audio_timeout, 90)
        self.assertEqual(
            AiProvider("groq", "key")._audio_timeout,
            30,
            "A vendor without an override keeps the shared default.",
        )

    def test_transcribe_refused_without_audio_support(self):
        """A vendor that cannot transcribe returns None without any HTTP call."""
        with patch("requests.post") as mock_post:
            result = AiProvider("anthropic", "key").transcribe(b"audio", "voice.ogg")
        self.assertIsNone(result)
        mock_post.assert_not_called()


def _response(status=200, payload=None, text=""):
    """Build a fake ``requests`` response.

    :param int status: HTTP status code
    :param dict payload: decoded JSON body
    :param str text: raw text body
    :rtype: unittest.mock.Mock
    """
    return Mock(status_code=status, text=text, json=Mock(return_value=payload))


class TestBotAi(TransactionCase):
    """The bot's AI service, as the flow modules call it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bot = cls.env["telegram.bot"].create(
            {"name": "Vykin Bot", "ai_provider": "groq", "ai_api_key": "ai-key"}
        )

    def _post(self, *responses):
        """Patch ``requests.post`` to answer with ``responses`` in order."""
        patcher = patch("requests.post", side_effect=list(responses))
        mock_post = patcher.start()
        self.addCleanup(patcher.stop)
        return mock_post

    def test_form_warns_when_vendor_cannot_transcribe(self):
        with Form(self.env["telegram.bot"]) as form:
            form.name = "Bot"
            self.assertFalse(form.ai_no_audio, "No vendor: nothing to warn about.")
            for code in ("anthropic", "deepseek", "moonshot"):
                form.ai_provider = code
                self.assertTrue(form.ai_no_audio, code)
            for code in ("groq", "openai", "gemini"):
                form.ai_provider = code
                self.assertFalse(form.ai_no_audio, code)

    def test_no_key_returns_no_result_without_calling(self):
        mock_post = self._post()
        for bot in (
            self.bot.copy({"ai_api_key": False}),
            self.bot.copy({"ai_provider": False, "ai_api_key": "ai-key"}),
        ):
            self.assertIsNone(bot._ai_transcribe(b"audio", "voice.oga"))
            self.assertIsNone(bot._ai_chat_json("sys", "texto"))
        mock_post.assert_not_called()

    def test_vendor_failure_returns_no_result(self):
        failures = [
            requests.ConnectionError("down"),
            _response(500, text="server error"),
            _response(200, payload={"unexpected": True}),
            _response(
                200,
                payload={
                    "choices": [
                        {
                            "message": {"content": '{"title": "Llan'},
                            "finish_reason": "length",
                        }
                    ]
                },
            ),
            _response(
                200,
                payload={"choices": [{"message": {"content": "no es JSON"}}]},
            ),
        ]
        self._post(*failures)
        with self.assertLogs("odoo.addons.telegram_bot", level="WARNING"):
            for __ in failures:
                self.assertIsNone(self.bot._ai_chat_json("sys", "texto"))
        self._post(requests.Timeout("slow"), _response(401, text="bad key"))
        with self.assertLogs("odoo.addons.telegram_bot", level="WARNING"):
            self.assertIsNone(self.bot._ai_transcribe(b"audio", "voice.oga"))
            self.assertIsNone(self.bot._ai_transcribe(b"audio", "voice.oga"))

    def test_transcribe_whisper(self):
        mock_post = self._post(_response(200, text="Revisar la llanta.\n"))
        self.assertEqual(
            self.bot._ai_transcribe(b"audio", "voice.oga"), "Revisar la llanta."
        )
        self.assertEqual(
            mock_post.call_args.args[0],
            "https://api.groq.com/openai/v1/audio/transcriptions",
        )
        self.assertEqual(
            mock_post.call_args.kwargs["headers"], {"Authorization": "Bearer ai-key"}
        )

    def test_transcribe_gemini(self):
        self.bot.ai_provider = "gemini"
        mock_post = self._post(
            _response(
                200,
                payload={
                    "candidates": [{"content": {"parts": [{"text": "Hola equipo."}]}}]
                },
            )
        )
        self.assertEqual(self.bot._ai_transcribe(b"audio", "voice.oga"), "Hola equipo.")
        self.assertIn(":generateContent", mock_post.call_args.args[0])

    def test_chat_json_openai_wire(self):
        mock_post = self._post(
            _response(
                200,
                payload={
                    "choices": [
                        {
                            "message": {"content": '{"title": "Revisar llanta"}'},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        )
        self.assertEqual(
            self.bot._ai_chat_json("sys", "texto"), {"title": "Revisar llanta"}
        )
        body = json.loads(mock_post.call_args.kwargs["data"])
        self.assertEqual(body["response_format"], {"type": "json_object"})

    def test_chat_json_anthropic_wire_strips_fence(self):
        self.bot.write({"ai_provider": "anthropic", "ai_model": "claude-custom"})
        mock_post = self._post(
            _response(
                200,
                payload={
                    "content": [
                        {"type": "text", "text": '```json\n{"title": "Revisar"}\n```'}
                    ],
                    "stop_reason": "end_turn",
                },
            )
        )
        self.assertEqual(self.bot._ai_chat_json("sys", "texto"), {"title": "Revisar"})
        self.assertEqual(mock_post.call_args.kwargs["headers"]["x-api-key"], "ai-key")
        body = json.loads(mock_post.call_args.kwargs["data"])
        self.assertEqual(body["model"], "claude-custom")
        self.assertEqual(body["system"], "sys")

    def test_api_key_restricted_to_administrators(self):
        user = new_test_user(self.env, login="employee", groups="base.group_user")
        bot = self.bot.with_user(user)
        self.assertEqual(bot.ai_provider, "groq")
        with self.assertRaises(AccessError):
            bot.read(["ai_api_key"])
        # The service still works for that user: the bot reads its key itself.
        self._post(_response(200, text="Hola."))
        self.assertEqual(bot._ai_transcribe(b"audio", "voice.oga"), "Hola.")
