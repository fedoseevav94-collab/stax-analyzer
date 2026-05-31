from app.utils.text import is_substantive_client_message


def test_attachment_filename_is_not_substantive_client_message():
    assert is_substantive_client_message("waybill-1.pdf") is False
    assert is_substantive_client_message("Фото ДТП.jpg") is False


def test_message_with_attachment_context_is_substantive():
    assert is_substantive_client_message("Посмотрите waybill-1.pdf") is True
