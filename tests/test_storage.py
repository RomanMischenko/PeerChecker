import tempfile
from pathlib import Path
import pytest
from app.storage import Storage


@pytest.fixture
def storage():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_peers.db"
        st = Storage(db_path)
        yield st


def test_save_and_retrieve_peer(storage):
    peer_data = {
        "login": "student1",
        "tribe_id": 604,
        "tribe_name": "Northern",
        "status": "VERIFIED",
        "xp": 1500,
        "logtime": 12.5,
        "suspicion_reason_text": "Прошел проверку",
        "details": {"info": {"id": 1}},
    }

    storage.save_peer(peer_data)

    assert storage.is_known_peer("student1") is True
    assert "student1" in storage.get_known_logins()

    retrieved = storage.get_peer("student1")
    assert retrieved is not None
    assert retrieved["login"] == "student1"
    assert retrieved["status"] == "VERIFIED"
    assert retrieved["xp"] == 1500
    assert retrieved["logtime"] == 12.5
    assert retrieved["is_manual"] == 0


def test_deduplication(storage):
    peers = [
        {
            "login": "peer_a",
            "tribe_id": 604,
            "tribe_name": "Northern",
            "status": "VERIFIED",
            "xp": 100,
            "logtime": 5.0,
        },
        {
            "login": "peer_b",
            "tribe_id": 605,
            "tribe_name": "Powder",
            "status": "SUSPICIOUS",
            "xp": 0,
            "logtime": 0.0,
        },
    ]
    storage.save_peers_batch(peers)

    known = storage.get_known_logins()
    assert len(known) == 2
    assert "peer_a" in known
    assert "peer_b" in known
    assert "peer_c" not in known


def test_manual_status_update(storage):
    peer_data = {
        "login": "peer_test",
        "tribe_id": 606,
        "tribe_name": "Secret",
        "status": "SUSPICIOUS",
        "xp": 0,
        "logtime": 0.0,
    }
    storage.save_peer(peer_data)

    # Verify initial status
    peer = storage.get_peer("peer_test")
    assert peer["status"] == "SUSPICIOUS"
    assert peer["is_manual"] == 0

    # Update status manually
    success = storage.update_peer_status("peer_test", "VERIFIED", is_manual=True)
    assert success is True

    peer_updated = storage.get_peer("peer_test")
    assert peer_updated["status"] == "VERIFIED"
    assert peer_updated["is_manual"] == 1

    # Ensure re-saving automatic peer data DOES NOT override manual status
    new_auto_data = {
        "login": "peer_test",
        "tribe_id": 606,
        "tribe_name": "Secret",
        "status": "SUSPICIOUS",
        "xp": 0,
        "logtime": 0.0,
    }
    storage.save_peer(new_auto_data)

    peer_after_resave = storage.get_peer("peer_test")
    assert peer_after_resave["status"] == "VERIFIED"  # Kept manual VERIFIED status!


def test_stats_and_check_logs(storage):
    peers = [
        {"login": "p1", "tribe_id": 604, "tribe_name": "Northern", "status": "VERIFIED", "xp": 10, "logtime": 1.0},
        {"login": "p2", "tribe_id": 604, "tribe_name": "Northern", "status": "SUSPICIOUS", "xp": 0, "logtime": 0.0},
        {"login": "p3", "tribe_id": 605, "tribe_name": "Powder", "status": "VERIFIED", "xp": 50, "logtime": 3.0},
        {"login": "p4", "tribe_id": 605, "tribe_name": "Powder", "status": "SKIPPED_WAVE", "xp": 0, "logtime": 0.0},
    ]
    storage.save_peers_batch(peers)

    stats = storage.get_stats()
    assert stats["total"] == 4
    assert stats["total_verified"] == 2
    assert stats["total_suspicious"] == 1
    assert stats["total_skipped_wave"] == 1

    log_id = storage.log_check_run(2, "Test check log")
    assert log_id > 0

    last_log = storage.get_last_check_info()
    assert last_log is not None
    assert last_log["new_peers_count"] == 2
    assert last_log["status_summary"] == "Test check log"


def test_get_filtered_peers(storage):
    peers = [
        {"login": "p1", "tribe_id": 604, "tribe_name": "Northern", "status": "VERIFIED", "xp": 10, "logtime": 1.0},
        {"login": "p2", "tribe_id": 604, "tribe_name": "Northern", "status": "SUSPICIOUS", "xp": 0, "logtime": 0.0},
        {"login": "p3", "tribe_id": 605, "tribe_name": "Powder", "status": "VERIFIED", "xp": 50, "logtime": 3.0},
    ]
    storage.save_peers_batch(peers)

    verified = storage.get_filtered_peers(status="VERIFIED")
    assert len(verified) == 2
    assert {p["login"] for p in verified} == {"p1", "p3"}

    tribe_604 = storage.get_filtered_peers(tribe_id=604)
    assert len(tribe_604) == 2

    filtered_both = storage.get_filtered_peers(tribe_id=604, status="SUSPICIOUS")
    assert len(filtered_both) == 1
    assert filtered_both[0]["login"] == "p2"


def test_storage_connection_closed(storage):
    """Ensure database connection is closed after connection_scope exit."""
    with storage.connection_scope() as conn:
        assert conn is not None
        target_conn = conn
    # Attempting to execute query on closed connection raises ProgrammingError
    with pytest.raises(Exception):
        target_conn.execute("SELECT 1")


def test_bot_state_persistence(storage):
    """Verify storing and retrieving bot state, monitoring_active, and check_in_progress status."""
    assert storage.is_monitoring_active() is False
    assert storage.is_check_in_progress() is False

    storage.set_monitoring_active(True)
    assert storage.is_monitoring_active() is True

    storage.set_monitoring_active(False)
    assert storage.is_monitoring_active() is False

    storage.set_check_in_progress(True)
    assert storage.is_check_in_progress() is True

    storage.set_check_in_progress(False)
    assert storage.is_check_in_progress() is False

    storage.set_state("custom_key", "custom_val")
    assert storage.get_state("custom_key") == "custom_val"



