"""Tests for the AI cleanup pass and, mostly, for the guard around it.

No Ollama required: every check here is fed a simulated model reply. The guard
is the whole reason this feature is safe to leave switched on, so it is tested
far harder than the HTTP call is.

Two failure modes matter and they pull in opposite directions. A guard that
lets a fabrication through puts words in Kevin's document that he never said.
A guard that rejects good cleanups is worse -- he turns the feature off and
never learns why. Hence the false-positive suite at the bottom.
"""

from __future__ import annotations

import pytest

from vocal_advantage.cleanup import (
    check_model_output,
    unwrap_model_output,
)


def _ok(said, output):
    accepted, failed = check_model_output(said, unwrap_model_output(output))
    return accepted, failed


# -- unwrapping ------------------------------------------------------------


def test_a_think_block_is_stripped():
    wrapped = "<think>The user wants me to clean this up.</think>We should ship it."
    assert unwrap_model_output(wrapped) == "We should ship it."


def test_a_multiline_think_block_is_stripped():
    wrapped = "<think>\nfirst I will\nthen I will\n</think>\n\nWe should ship it."
    assert unwrap_model_output(wrapped) == "We should ship it."


def test_surrounding_quotes_are_stripped():
    assert unwrap_model_output('"We should ship it."') == "We should ship it."


def test_a_markdown_fence_is_stripped():
    assert unwrap_model_output("```\nWe should ship it.\n```") == "We should ship it."


def test_a_labelled_markdown_fence_is_stripped():
    assert unwrap_model_output("```text\nWe should ship it.\n```") == "We should ship it."


# -- accepted --------------------------------------------------------------


def test_a_good_cleanup_is_accepted():
    accepted, failed = _ok(
        "um so I I think we should uh ship it on friday",
        "So I think we should ship it on Friday.",
    )
    assert accepted, failed


def test_a_good_cleanup_in_quotes_is_accepted():
    accepted, failed = _ok(
        "um so I I think we should uh ship it on friday",
        '"So I think we should ship it on Friday."',
    )
    assert accepted, failed


def test_a_good_cleanup_in_a_code_fence_is_accepted():
    accepted, failed = _ok(
        "um so I I think we should uh ship it on friday",
        "```\nSo I think we should ship it on Friday.\n```",
    )
    assert accepted, failed


def test_a_good_cleanup_behind_a_think_block_is_accepted():
    accepted, failed = _ok(
        "um so I I think we should uh ship it on friday",
        "<think>Remove the fillers.</think>So I think we should ship it on Friday.",
    )
    assert accepted, failed


# -- rejected --------------------------------------------------------------


def test_answering_the_question_instead_of_cleaning_it_is_rejected():
    """The one that length checks cannot catch: same length, smuggles 'Paris'."""
    accepted, failed = _ok(
        "um what's the capital of France",
        "The capital of France is Paris.",
    )
    assert not accepted
    assert failed in {"invented_words", "question_lost"}, failed


def test_a_paraphrase_at_similar_length_is_rejected():
    accepted, failed = _ok(
        "the server kept timing out so I could not finish the deployment",
        "The deployment failed because the machine was unresponsive.",
    )
    assert not accepted, failed


def test_a_summary_is_rejected():
    accepted, failed = _ok(
        "so the thing is I could not get it working because the API kept timing "
        "out and then the build failed as well",
        "It did not work.",
    )
    assert not accepted
    assert failed == "shrank", failed


def test_a_chatter_preamble_is_rejected():
    accepted, failed = _ok(
        "um so I think we should ship it",
        "Sure! Here is the cleaned text: I think we should ship it.",
    )
    assert not accepted
    assert failed == "chatter", failed


def test_a_refusal_is_rejected():
    accepted, failed = _ok(
        "um so I think we should ship it on friday",
        "I'm sorry, I cannot help with that request.",
    )
    assert not accepted, failed


def test_empty_output_is_rejected():
    accepted, failed = _ok("um so I think we should ship it", "")
    assert not accepted
    assert failed == "empty", failed


def test_a_think_block_with_nothing_after_it_is_rejected():
    accepted, failed = _ok(
        "um so I think we should ship it",
        "<think>I should clean this up.</think>",
    )
    assert not accepted
    assert failed == "empty", failed


def test_a_translation_is_rejected():
    accepted, failed = _ok(
        "so I think we should ship it on Friday",
        "Creo que deberiamos enviarlo el viernes.",
    )
    assert not accepted, failed


def test_output_that_grew_wildly_is_rejected():
    accepted, failed = _ok(
        "ship it friday",
        "We should ship the product on Friday because the team has finished "
        "testing and the release notes are ready for the customer announcement.",
    )
    assert not accepted
    assert failed == "grew", failed


def test_a_question_that_loses_its_question_mark_is_rejected():
    accepted, failed = _ok(
        "um can you send me the file when you get a sec",
        "You can send me the file when you get a sec.",
    )
    assert not accepted
    assert failed == "question_lost", failed


# -- false positives: all of these MUST be accepted ------------------------
#
# Kevin: "Guards that reject good output are worse than no guards, because
# I'll turn the feature off and never know why."

GOOD_CLEANUPS = [
    (
        "um so I I think we should uh ship it on friday",
        "So I think we should ship it on Friday.",
    ),
    (
        "meet me tuesday no wednesday",
        "Meet me Wednesday.",
    ),
    (
        "im not sure if the the build passed",
        "I'm not sure if the build passed.",
    ),
    (
        "dont forget to push the branch",
        "Don't forget to push the branch.",
    ),
    (
        "there were like forty two failures in the suite",
        "There were 42 failures in the suite.",
    ),
    (
        "can you send me the file when you get a sec thanks",
        "Can you send me the file when you get a sec? Thanks.",
    ),
    (
        "okay perfect I think that this is working a little bit better",
        "Okay, perfect. I think this is working a little bit better.",
    ),
    (
        "so the deploy went out and then the alerts fired and I had to roll it "
        "back and then I spent the rest of the morning working out why the "
        "migration had not run",
        "The deploy went out and then the alerts fired, so I had to roll it "
        "back.\n\nI spent the rest of the morning working out why the migration "
        "had not run.",
    ),
    (
        "whats the status on the pull request",
        "What's the status on the pull request?",
    ),
]


@pytest.mark.parametrize(("said", "cleaned"), GOOD_CLEANUPS)
def test_realistic_good_cleanups_are_all_accepted(said, cleaned):
    accepted, failed = _ok(said, cleaned)
    assert accepted, f"rejected by {failed}: {cleaned!r}"


def test_an_abandoned_restart_collapse_is_rejected_and_that_is_deliberate():
    """The one place Kevin's spec contradicts itself, recorded rather than hidden.

    He asked for "I was gonna, well, what I mean is we should wait" to collapse
    to "We should wait." -- and for output shorter than 45% of the input, or
    keeping under 60% of his content words, to be discarded. That collapse is
    both. No word-counting guard can tell it apart from summarising, because
    they are the same operation.

    The guard wins: this falls back to the rules-only text. Losing the collapse
    costs a tidier sentence. Losing the guard costs words he never said.
    """
    accepted, failed = _ok(
        "i was gonna well what I mean is we should wait", "We should wait."
    )
    assert not accepted
    assert failed in {"shrank", "words_lost"}, failed


# -- the call itself, with a fake transport --------------------------------


import json as _json  # noqa: E402

from vocal_advantage.cleanup import ai_clean  # noqa: E402

SAID = "um so I I think we should uh ship it on friday"
# What the rules layer alone produces: fillers and the stutter gone, but no
# invented capital or full stop -- it only ever removes and recapitalises.
RULES_ONLY = "So I think we should ship it on friday"


def _reply(content):
    return lambda url, payload, timeout: {"message": {"content": content}}


def _boom(exc):
    def post(url, payload, timeout):
        raise exc
    return post


def test_a_good_model_reply_is_used(tmp_path):
    out = ai_clean(SAID, post=_reply("So I think we should ship it on Friday."),
                   log_path=tmp_path / "log.jsonl")
    assert out == "So I think we should ship it on Friday."


def test_ollama_being_down_falls_back_silently(tmp_path):
    out = ai_clean(SAID, post=_boom(ConnectionRefusedError("no server")),
                   log_path=tmp_path / "log.jsonl")
    assert out == RULES_ONLY


def test_a_timeout_falls_back_silently(tmp_path):
    out = ai_clean(SAID, post=_boom(TimeoutError("too slow")),
                   log_path=tmp_path / "log.jsonl")
    assert out == RULES_ONLY


def test_a_model_that_answers_instead_of_cleaning_falls_back(tmp_path):
    out = ai_clean("um whats the capital of france",
                   post=_reply("The capital of France is Paris."),
                   log_path=tmp_path / "log.jsonl")
    assert "Paris" not in out


def test_a_malformed_reply_falls_back(tmp_path):
    out = ai_clean(SAID, post=lambda u, p, t: {"unexpected": "shape"},
                   log_path=tmp_path / "log.jsonl")
    assert out == RULES_ONLY


def test_every_dictation_is_logged_as_one_json_line(tmp_path):
    log = tmp_path / "log.jsonl"
    ai_clean(SAID, post=_reply("So I think we should ship it on Friday."), log_path=log)
    ai_clean(SAID, post=_boom(TimeoutError()), log_path=log)

    lines = log.read_text().strip().splitlines()
    assert len(lines) == 2
    first, second = (_json.loads(line) for line in lines)
    assert first["which_was_used"] == "model"
    assert first["failed_check"] is None
    assert second["which_was_used"] == "rules"
    assert second["failed_check"]
    for entry in (first, second):
        assert entry["rules_only_output"] == RULES_ONLY
        assert isinstance(entry["elapsed_ms"], (int, float))
        assert entry["timestamp"]


def test_a_broken_log_path_never_breaks_dictation(tmp_path):
    """Logging is a diagnostic. It must never cost Kevin his sentence."""
    unwritable = tmp_path / "a-file"
    unwritable.write_text("not a directory")
    out = ai_clean(SAID, post=_reply("So I think we should ship it on Friday."),
                   log_path=unwritable / "nested" / "log.jsonl")
    assert out == "So I think we should ship it on Friday."


def test_num_predict_is_bounded_by_the_input_length(tmp_path):
    seen = {}

    def post(url, payload, timeout):
        seen.update(payload)
        return {"message": {"content": RULES_ONLY}}

    ai_clean(SAID, post=post, log_path=tmp_path / "log.jsonl")
    assert seen["options"]["num_predict"] == max(64, len(SAID.split()) * 3 + 48)
    assert seen["options"]["temperature"] == 0
    assert seen["options"]["top_p"] == 0.1
    assert seen["think"] is False
    assert seen["stream"] is False


def test_the_few_shot_examples_are_sent_as_real_turns(tmp_path):
    seen = {}

    def post(url, payload, timeout):
        seen.update(payload)
        return {"message": {"content": RULES_ONLY}}

    ai_clean(SAID, post=post, log_path=tmp_path / "log.jsonl")
    roles = [m["role"] for m in seen["messages"]]
    assert roles[0] == "system"
    assert roles[1:-1] == ["user", "assistant"] * 3
    assert roles[-1] == "user"
    assert seen["messages"][-1]["content"] == SAID


# -- each guard tested in isolation ----------------------------------------
#
# Added after mutation testing: deleting the invented-words check and deleting
# the words-lost check both left the whole suite green. Every case above was
# being caught by some other rule first, so neither was guarding anything.


def test_invented_words_are_caught_without_help_from_the_question_rule():
    """'tell me' is not a question opener, so only the invented-word rule can
    catch the model answering instead of cleaning."""
    accepted, failed = _ok(
        "so tell me the capital of france",
        "The capital of France is Paris.",
    )
    assert not accepted
    assert failed == "invented_words", failed


def test_a_repetition_loop_is_caught_by_the_words_lost_rule():
    """Small models loop. Nothing is invented and the length is plausible, so
    only the survival rule sees it."""
    accepted, failed = _ok(
        "the migration never ran on the production database and that broke the "
        "checkout flow",
        "The migration never ran. The migration never ran. The migration never ran.",
    )
    assert not accepted
    assert failed == "words_lost", failed


def test_a_long_dictation_tolerates_one_repaired_word():
    """The invented-word allowance is proportional: a typo fix in a long
    sentence must not cost the whole cleanup."""
    said = (
        "so we shipped the migration on thursday and the checkout flow broke "
        "for about twenty minutes before the rollback finished and nobody "
        "noticed except the oncall engineer"
    )
    cleaned = (
        "We shipped the migration on Thursday and the checkout flow broke for "
        "about 20 minutes before the rollback finished. Nobody noticed except "
        "the on-call engineer."
    )
    accepted, failed = _ok(said, cleaned)
    assert accepted, failed


# -- warm-up ---------------------------------------------------------------


def test_warming_up_asks_for_one_token():
    """Loading a 2 GB model takes ~10s. Paying that at startup rather than on
    Kevin's first sentence is the difference between working and timing out."""
    seen = {}

    def post(url, payload, timeout):
        seen.update(payload)
        return {"message": {"content": "ok"}}

    from vocal_advantage.cleanup import warm_up_model
    warm_up_model(post=post)
    assert seen["options"]["num_predict"] == 1
    assert seen["model"]


def test_warming_up_never_raises_when_ollama_is_missing():
    from vocal_advantage.cleanup import warm_up_model

    def post(url, payload, timeout):
        raise ConnectionRefusedError("nothing there")

    assert warm_up_model(post=post) is False
