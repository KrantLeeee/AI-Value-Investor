from pathlib import Path

from src.screening import jobs


def test_create_and_load_job(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "get_output_dir", lambda name: tmp_path / name)

    job = jobs.create_job({"universe": "watchlist", "limit": 1})
    loaded = jobs.load_job(job["job_id"])

    assert loaded is not None
    assert loaded["job_id"] == job["job_id"]
    assert loaded["status"] == "queued"
    assert Path(tmp_path / "valuation_jobs" / f"{job['job_id']}.json").exists()


def test_latest_job(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "get_output_dir", lambda name: tmp_path / name)

    first = jobs.create_job({"universe": "watchlist", "limit": 1})
    second = jobs.create_job({"universe": "watchlist", "limit": 2})
    latest = jobs.latest_job()

    assert latest is not None
    assert latest["job_id"] in {first["job_id"], second["job_id"]}


def test_has_running_job(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "get_output_dir", lambda name: tmp_path / name)

    assert not jobs.has_running_job()


def test_is_stale_for_old_queued_job():
    job = {
        "status": "queued",
        "updated_at": "2000-01-01T00:00:00",
    }

    assert jobs.is_stale(job)
    job = jobs.create_job({"universe": "watchlist", "limit": 1})

    assert jobs.has_running_job()

    job["status"] = "completed"
    jobs.save_job(job)

    assert not jobs.has_running_job()
