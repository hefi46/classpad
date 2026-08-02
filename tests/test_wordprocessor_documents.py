import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "wordprocessor" / "app"))

import documents  # noqa: E402


def test_create_document_writes_empty_file_immediately(tmp_path):
    path = documents.create_document(tmp_path)
    assert path.exists()
    assert path.read_text() == ""
    assert path.parent == tmp_path


def test_create_document_avoids_filename_collision(tmp_path, monkeypatch):
    # Two "New Story" clicks in the same second must not collide.
    class _FixedDateTime:
        @staticmethod
        def now():
            return _FixedDateTime()

        @staticmethod
        def strftime(fmt):
            return "20260802-120000"

    monkeypatch.setattr(documents, "datetime", _FixedDateTime)
    first = documents.create_document(tmp_path)
    second = documents.create_document(tmp_path)
    assert first != second
    assert first.exists() and second.exists()


def test_write_and_read_document_round_trip(tmp_path):
    path = documents.create_document(tmp_path)
    documents.write_document(path, "Once upon a time.")
    assert documents.read_document(path) == "Once upon a time."


def test_write_document_is_atomic_no_leftover_tmp_file(tmp_path):
    path = documents.create_document(tmp_path)
    documents.write_document(path, "hello")
    assert not path.with_suffix(".txt.tmp").exists()


def test_delete_document_removes_file(tmp_path):
    path = documents.create_document(tmp_path)
    documents.delete_document(path)
    assert not path.exists()


def test_delete_document_missing_file_does_not_raise(tmp_path):
    path = tmp_path / "does-not-exist.txt"
    documents.delete_document(path)  # should not raise


def test_list_documents_empty_dir_returns_empty_list(tmp_path):
    assert documents.list_documents(tmp_path) == []


def test_list_documents_missing_dir_returns_empty_list(tmp_path):
    assert documents.list_documents(tmp_path / "does-not-exist") == []


def test_list_documents_sorted_newest_first(tmp_path):
    old = documents.create_document(tmp_path)
    documents.write_document(old, "old story")
    time.sleep(0.01)
    new = documents.create_document(tmp_path)
    documents.write_document(new, "new story")

    docs = documents.list_documents(tmp_path)
    assert [d.path for d in docs] == [new, old]


def test_list_documents_title_is_first_nonempty_line(tmp_path):
    path = documents.create_document(tmp_path)
    documents.write_document(path, "\n\n  My Story Title  \nmore text")
    docs = documents.list_documents(tmp_path)
    assert docs[0].title == "My Story Title"


def test_list_documents_empty_document_gets_default_title(tmp_path):
    documents.create_document(tmp_path)
    docs = documents.list_documents(tmp_path)
    assert docs[0].title == documents.DEFAULT_TITLE


def test_list_documents_reflects_deletion(tmp_path):
    path = documents.create_document(tmp_path)
    assert len(documents.list_documents(tmp_path)) == 1
    documents.delete_document(path)
    assert documents.list_documents(tmp_path) == []
