import pytest

from outlook_calendar_agent.confirm import ask_confirmation, is_exact_confirmation


@pytest.mark.parametrize("answer", ["yes", "yes\n", "  yes  ", "Yes", "YES", "yes.", "Yes!"])
def test_single_word_yes_in_any_case_is_accepted(answer):
    assert is_exact_confirmation(answer)


@pytest.mark.parametrize(
    "answer", ["y", "yes please", "yes but later", "", None, "no", "ye s", "yesyes", "ok", "sure"]
)
def test_everything_else_is_rejected(answer):
    assert not is_exact_confirmation(answer)


def test_eof_counts_as_no():
    def reader(_prompt: str) -> str:
        raise EOFError

    assert ask_confirmation("Proceed?", reader=reader) is False


def test_prompt_is_passed_to_reader():
    seen = []

    def reader(prompt: str) -> str:
        seen.append(prompt)
        return "yes"

    assert ask_confirmation(
        "Create this event? Type yes to continue (anything else cancels): ", reader=reader
    )
    assert seen == ["Create this event? Type yes to continue (anything else cancels): "]
