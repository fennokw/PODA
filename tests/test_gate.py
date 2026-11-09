import pytest

from poda_app.agent import gate

CAP_NONE = {"filesystem": {"general_user_file_read": False, "general_user_file_create": False, "general_user_file_edit": False},
            "code": {"can_execute_user_code": False}, "email": {"can_send": False, "can_modify_mailbox": False, "can_read_without_credentials_in_request": False},
            "calendar": {"chat_can_execute_writes": False, "can_write_calendar": False, "can_read_calendar": False}}


@pytest.mark.parametrize("text", ["What can you do with files?", "What could you do with files on my Desktop?", "Can you create a file?", "Which files can you read?"])
def test_informational_questions_do_not_trigger_block(text):
    assert gate.requested_action_intents(text) == set()
    assert gate.unsupported_action_response(text, CAP_NONE) is None
    assert gate.is_capability_query(text)


@pytest.mark.parametrize("text,intent", [("Create `PODA Test 1.py` on the Desktop", "file_create"), ("Please write a file called notes.txt in ~/Documents", "file_create"),
                                         ("Edit the config.json file in my project folder", "file_edit"), ("Run the tests in this repo", "code_execute"),
                                         ("Add this to my calendar for Friday", "calendar_write"), ("Read my inbox and summarize", "email_read")])
def test_side_effects_are_detected_and_blocked_without_grants(text, intent):
    assert intent in gate.requested_action_intents(text)
    msg = gate.unsupported_action_response(text, CAP_NONE)
    assert msg and "Nothing was created" in msg


def test_file_create_allowed_when_granted():
    cap = {**CAP_NONE, "filesystem": {"general_user_file_read": True, "general_user_file_create": True, "general_user_file_edit": True}}
    assert gate.unsupported_action_response("Create test.py on my Desktop", cap) is None


def test_hints():
    assert gate.extract_filename_hint("Create `PODA Test 1.py` on the Desktop") == "PODA Test 1.py"
    assert gate.extract_path_hint("Create test.py on the Desktop") == "~/Desktop"
