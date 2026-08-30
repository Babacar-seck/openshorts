"""The script language must reach the prompt, and an unknown one must not.

`"Spanish" if language == "es" else "English"` accepted every code and turned
all but one into English, so a French request came back as three English
scripts with no error anywhere. These tests assert on the prompt that gets
built rather than on what Gemini answers: the defect was in what we asked for,
and asserting on generated text would be both nondeterministic and billed.
"""

import json

import pytest

from saasshorts import SCRIPT_LANGUAGES, generate_scripts, language_spec


ANALYSIS = {"product_name": "Widget", "pain_points": ["slow"]}

# One valid script, enough for generate_scripts to parse and return.
CANNED_SCRIPTS = json.dumps(
    [{"title": "t", "full_narration": "n", "actor_description": "a woman"}]
)


class FakeClient:
    """Stands in for genai.Client, capturing the prompt instead of sending it."""

    prompts: list = []
    instantiated: int = 0

    def __init__(self, *args, **kwargs):
        type(self).instantiated += 1
        self.models = self

    def generate_content(self, *, model, contents, config):
        type(self).prompts.append(contents[0])
        return type("Response", (), {"text": CANNED_SCRIPTS})()


@pytest.fixture
def prompt_for(monkeypatch):
    """Build the prompt for a language and hand it back, calling no API."""
    from google import genai

    FakeClient.prompts = []
    FakeClient.instantiated = 0
    monkeypatch.setattr(genai, "Client", FakeClient)

    def build(language):
        generate_scripts(ANALYSIS, "unused-key", num_scripts=1, language=language)
        return FakeClient.prompts[-1]

    return build


class TestLanguageSpec:
    def test_every_supported_language_has_a_name(self):
        assert language_spec("en")["name"] == "English"
        assert language_spec("es")["name"] == "Spanish"
        assert language_spec("fr")["name"] == "French"

    def test_unknown_language_raises_instead_of_defaulting(self):
        # The whole point: silence here is what shipped English scripts to
        # someone who asked for French.
        with pytest.raises(ValueError) as excinfo:
            language_spec("de")
        assert "de" in str(excinfo.value)

    def test_the_error_names_what_is_supported(self):
        with pytest.raises(ValueError) as excinfo:
            language_spec("de")
        for code in SCRIPT_LANGUAGES:
            assert code in str(excinfo.value)

    def test_every_entry_carries_all_fields(self):
        # A language added with only a name would fall back to English phrasing
        # for the CTA without failing.
        for code, spec in SCRIPT_LANGUAGES.items():
            assert spec["name"], code
            assert spec["instructions"].strip(), code
            assert spec["cta"].strip(), code


class TestPromptCarriesTheLanguage:
    def test_french_asks_for_french(self, prompt_for):
        prompt = prompt_for("fr")
        assert "MUST be in FRENCH" in prompt
        assert "Write ALL text in French" in prompt

    def test_french_carries_its_own_hook_examples(self, prompt_for):
        # Not a translated block: the register is what makes a UGC script land.
        prompt = prompt_for("fr")
        assert "Franchement j'y crois pas" in prompt
        assert "tutoiement" in prompt

    def test_spanish_is_unchanged(self, prompt_for):
        prompt = prompt_for("es")
        assert "MUST be in SPANISH" in prompt
        assert "Write ALL text in Spanish" in prompt
        assert "Tío, no me puedo creer" in prompt

    def test_english_is_unchanged(self, prompt_for):
        prompt = prompt_for("en")
        assert "MUST be in ENGLISH" in prompt
        assert "Write ALL text in English" in prompt

    def test_one_language_block_at_a_time(self, prompt_for):
        # The binary emitted exactly one block; the table must not leak the
        # others into the prompt and let the model choose.
        prompt = prompt_for("fr")
        assert "MUST be in SPANISH" not in prompt
        assert "MUST be in ENGLISH" not in prompt


class TestCallToAction:
    # The assertion is on the directive that tells the model how to *phrase*
    # the CTA. "link in bio" also appears further down as English scaffolding
    # describing what segment 5 is, which is fine — the prompt is English
    # instructions throughout, and a real French run says "le lien dans la bio".
    def test_the_cta_follows_the_language(self, prompt_for):
        # This directive used to hardcode English *and* Spanish phrasings in a
        # single sentence, steering a French script toward "link in bio".
        prompt = prompt_for("fr")
        assert 'Say it as "lien dans la bio"' in prompt
        assert "enlace en la bio" not in prompt

    def test_spanish_keeps_its_own_cta(self, prompt_for):
        prompt = prompt_for("es")
        assert 'Say it as "enlace en la bio"' in prompt
        assert "lien dans la bio" not in prompt


class TestActorDescriptionStaysEnglish:
    @pytest.mark.parametrize("language", sorted(SCRIPT_LANGUAGES))
    def test_actor_description_is_required_in_english(self, prompt_for, language):
        # The image model is prompted with this field, so it must stay English
        # even when everything the viewer hears is not.
        prompt = prompt_for(language)
        assert "actor_description MUST ALWAYS be in ENGLISH" in prompt


class TestUnknownLanguageCostsNothing:
    def test_generate_scripts_raises_before_reaching_gemini(self, monkeypatch):
        from google import genai

        FakeClient.instantiated = 0
        monkeypatch.setattr(genai, "Client", FakeClient)

        with pytest.raises(ValueError):
            generate_scripts(ANALYSIS, "unused-key", language="de")

        # Raising after building the client would still bill a request.
        assert FakeClient.instantiated == 0
