"""Pose analysis API — clean stateless design.

Single source of truth: GCS progress file.
DB is only updated when progress shows "done".

Flow:
1. POST /analyze → delete old data, create DB record, send request to GPU, return
2. GPU processes video, writes progress to GCS every 200 frames
3. GET /progress → read GCS progress file, sync DB if done
4. DELETE /analyze → write "cancelled" to GCS, GPU checks and stops
5. GET /pose-data → stream pose JSON from GCS

No background threads. No race conditions. No stuck jobs.
"""

import os
import json
import threading
import requests
from datetime import datetime, timezone, timedelta
import google.auth.transport.requests
import google.oauth2.id_token
from flask import Blueprint, request, jsonify, Response
from core.adapters.repository import get_repo
from core.adapters.analyses import AnalysesAdapter
from core.adapters.videos import VideosAdapter
from core.adapters.storage import BUCKET_NAME, _get_client as _gcs
from core.api._helpers import uid

analysis_bp = Blueprint("analysis", __name__)

POSE_SERVICE_URL = os.environ.get("POSE_SERVICE_URL", "http://localhost:8080")


def _auth_headers():
    if POSE_SERVICE_URL.startswith("http://localhost"):
        return {}
    try:
        auth_req = google.auth.transport.requests.Request()
        token = google.oauth2.id_token.fetch_id_token(auth_req, POSE_SERVICE_URL)
        return {"Authorization": f"Bearer {token}"}
    except Exception:
        # Fallback for local dev — use gcloud CLI
        import subprocess, shutil
        gcloud_path = shutil.which("gcloud") or "gcloud"
        token = subprocess.check_output(
            [gcloud_path, "auth", "print-identity-token"], text=True, shell=True
        ).strip()
        return {"Authorization": f"Bearer {token}"}


def _read_progress(video_id):
    """Read progress from GCS. Returns dict or None."""
    try:
        # Use fresh client call to avoid stale reads
        bucket = _gcs().bucket(BUCKET_NAME)
        blob = bucket.blob(f"progress/{video_id}.json")
        blob.reload()  # Force fetch latest metadata
        return json.loads(blob.download_as_bytes(if_generation_match=blob.generation))
    except Exception:
        pass
    return None


def _delete_progress(video_id):
    """Delete progress file from GCS."""
    try:
        blob = _gcs().bucket(BUCKET_NAME).blob(f"progress/{video_id}.json")
        if blob.exists():
            blob.delete()
    except Exception:
        pass


def _delete_pose_file(video_id):
    """Delete pose data file from GCS."""
    try:
        blob = _gcs().bucket(BUCKET_NAME).blob(f"poses/{video_id}.json")
        if blob.exists():
            blob.delete()
    except Exception:
        pass


def _sync_db_if_done(video_id):
    """If GCS says done, make sure DB matches."""
    prog = _read_progress(video_id)
    if not prog or prog.get("status") != "done":
        return
    repo = get_repo()
    aa = AnalysesAdapter(repo)
    analysis = aa.get_by_video(video_id)
    if analysis and analysis["status"] != "done":
        summary = {
            "pose_gcs_path": prog.get("pose_gcs_path", f"poses/{video_id}.json"),
            "pose_size_kb": prog.get("pose_size_kb", 0),
        }
        aa.set_summary(analysis["id"], summary)
        update_data = {"has_pose": True}
        try:
            from core.classification import classify_body_focus
            blob = _gcs().bucket(BUCKET_NAME).blob(summary["pose_gcs_path"])
            pose_data = json.loads(blob.download_as_bytes())
            update_data["body_focus"] = classify_body_focus(pose_data.get("frames", []))
            # role_labels already saved from person_roles at request time
            # Auto-compute features for move matching
            try:
                from core.pose_features import compute_and_store_features
                if compute_and_store_features(video_id, pose_data):
                    update_data["has_features"] = True
            except Exception:
                pass
        except Exception:
            pass
        repo.update("bachata.videos", video_id, update_data)


# ── Main Endpoints ──────────────────────────────────────────────────────────


@analysis_bp.route("/api/videos/<int:video_id>/detect", methods=["POST"])
def api_detect_first_frame(video_id):
    """Quick detection of persons in the first frame — for labeling before analysis."""
    repo = get_repo()
    video = VideosAdapter(repo).get(video_id)
    if not video:
        return jsonify({"error": "video not found"}), 404
    if not video.get("filename"):
        return jsonify({"error": "video has no file"}), 400

    try:
        headers = _auth_headers()
        resp = requests.post(
            f"{POSE_SERVICE_URL}/detect-first-frame",
            json={"gcs_bucket": BUCKET_NAME, "gcs_blob": video["filename"]},
            headers=headers,
            timeout=120,
        )
        if resp.status_code != 200:
            return jsonify({"error": f"GPU returned {resp.status_code}: {resp.text[:200]}"}), 502
        return jsonify(resp.json()), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@analysis_bp.route("/api/videos/<int:video_id>/analyze", methods=["POST"])
def api_run_analysis(video_id):
    repo = get_repo()
    video = VideosAdapter(repo).get(video_id)
    if not video:
        return jsonify({"error": "video not found"}), 404
    if not video.get("filename"):
        return jsonify({"error": "video has no file"}), 400

    # Cancel any existing run before starting fresh
    try:
        _gcs().bucket(BUCKET_NAME).blob(f"cancel/{video_id}").upload_from_string("1")
    except Exception:
        pass

    # Clean slate: delete old progress, pose file, cancel flag, and DB records
    _delete_progress(video_id)
    _delete_pose_file(video_id)
    try:
        _gcs().bucket(BUCKET_NAME).blob(f"cancel/{video_id}").delete()
    except Exception:
        pass

    aa = AnalysesAdapter(repo)
    for old in aa.select(video_id=video_id):
        aa.delete(old["id"])

    # Create fresh DB record
    body = request.get_json(silent=True) or {}
    sample_every_n = body.get("sample_every_n", 2)
    resolution = body.get("resolution", 640)
    person_roles = body.get("person_roles")  # {person_index: "lead"|"follow"}
    max_persons = body.get("max_persons")    # cap persons per frame

    analysis = aa.create(video_id=video_id, model="yolo26x-pose")
    aa.set_status(analysis["id"], "running")

    # Save role labels to video if provided
    if person_roles:
        repo.update("bachata.videos", video_id, {"role_labels": json.dumps(person_roles)})

    # Send to GPU — fire and forget via thread
    gpu_payload = {
        "gcs_bucket": BUCKET_NAME,
        "gcs_blob": video["filename"],
        "video_id": video_id,
        "sample_every_n": sample_every_n,
        "resolution": resolution,
    }
    if person_roles:
        gpu_payload["person_roles"] = person_roles
    if max_persons:
        gpu_payload["max_persons"] = max_persons

    def _send():
        try:
            requests.post(
                f"{POSE_SERVICE_URL}/analyze",
                json=gpu_payload,
                headers=_auth_headers(),
                timeout=3600,
            )
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()

    return jsonify({"status": "started", "analysis_id": analysis["id"]}), 202


@analysis_bp.route("/api/videos/<int:video_id>/analyze", methods=["DELETE"])
def api_cancel_analysis(video_id):
    # Write separate cancel flag file — GPU checks this every 200 frames
    try:
        blob = _gcs().bucket(BUCKET_NAME).blob(f"cancel/{video_id}")
        blob.upload_from_string("1", content_type="text/plain")
    except Exception:
        pass

    # Clean up: delete DB record, pose file
    repo = get_repo()
    aa = AnalysesAdapter(repo)
    for a in aa.select(video_id=video_id):
        if a["status"] != "done":
            aa.delete(a["id"])
    _delete_pose_file(video_id)
    repo.update("bachata.videos", video_id, {"has_pose": False})

    return jsonify({"status": "cancelled"}), 200


@analysis_bp.route("/api/videos/<int:video_id>/progress", methods=["GET"])
def api_get_progress(video_id):
    # GCS is the single source of truth for progress
    prog = _read_progress(video_id)

    if prog:
        # Sync DB when GPU finishes
        if prog["status"] == "done":
            _sync_db_if_done(video_id)
        return jsonify(prog)

    # No GCS progress — check DB
    repo = get_repo()
    analysis = AnalysesAdapter(repo).get_by_video(video_id)
    if analysis:
        if analysis["status"] == "done":
            return jsonify({"status": "done", "percent": 100, "step": "Complete"})
        if analysis["status"] == "running":
            # DB says running but no GCS progress — GPU hasn't started yet or died
            # Check if it's been too long (30 min timeout)
            created = analysis.get("created_at")
            if created and (datetime.now(timezone.utc) - created) > timedelta(minutes=30):
                AnalysesAdapter(repo).set_status(analysis["id"], "error")
                return jsonify({"status": "error", "percent": 0, "step": "Timed out — GPU never responded"})
            return jsonify({"status": "running", "percent": 0, "step": "Waiting for GPU..."})
        if analysis["status"] == "error":
            return jsonify({"status": "error", "percent": 0, "step": "Error"})

    return jsonify({"status": "idle", "percent": 0})


@analysis_bp.route("/api/videos/<int:video_id>/pose-data", methods=["GET"])
def api_get_pose_data(video_id):
    """Stream pose data from GCS."""
    # Sync DB first
    _sync_db_if_done(video_id)

    repo = get_repo()
    analysis = AnalysesAdapter(repo).get_by_video(video_id)
    if not analysis or analysis["status"] != "done":
        return jsonify({"error": "no pose data"}), 404

    summary = analysis.get("summary")
    if isinstance(summary, str):
        summary = json.loads(summary)
    path = summary.get("pose_gcs_path") if summary else None
    if not path:
        return jsonify({"error": "no pose file"}), 404

    blob = _gcs().bucket(BUCKET_NAME).blob(path)
    if not blob.exists():
        return jsonify({"error": "pose file missing from storage"}), 404

    def stream():
        with blob.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk

    return Response(stream(), content_type="application/json")



# ── Role Labels ───────────────────────────────────────────────────────────


@analysis_bp.route("/api/videos/<int:video_id>/role-labels", methods=["GET"])
def api_get_role_labels(video_id):
    repo = get_repo()
    video = VideosAdapter(repo).get(video_id)
    if not video:
        return jsonify({"error": "video not found"}), 404
    rl = video.get("role_labels")
    if isinstance(rl, str):
        try:
            rl = json.loads(rl)
        except Exception:
            rl = None
    return jsonify({"labels": rl})


# ── Admin ────────────────────────────────────────────────────────────────────


@analysis_bp.route("/api/admin/jobs", methods=["GET"])
def api_list_jobs():
    repo = get_repo()
    analyses = AnalysesAdapter(repo).select(order_by="id DESC")
    result = []
    for a in analyses:
        video = VideosAdapter(repo).get(a["video_id"])
        for k in ("created_at", "updated_at"):
            if a.get(k):
                a[k] = str(a[k])
        if a.get("summary") and isinstance(a["summary"], str):
            a["summary"] = json.loads(a["summary"])

        # Get live progress from GCS
        gcs_progress = _read_progress(a["video_id"])

        result.append({
            "id": a["id"],
            "video_id": a["video_id"],
            "video_title": video["title"] if video else "?",
            "status": gcs_progress["status"] if gcs_progress else a["status"],
            "model": a.get("model"),
            "created_at": a.get("created_at"),
            "summary": a.get("summary"),
            "gcs_progress": gcs_progress,
        })
    return jsonify(result)


@analysis_bp.route("/api/admin/jobs/<int:analysis_id>", methods=["DELETE"])
def api_delete_job(analysis_id):
    repo = get_repo()
    aa = AnalysesAdapter(repo)
    analysis = aa.get(analysis_id)
    if not analysis:
        return jsonify({"error": "not found"}), 404

    video_id = analysis["video_id"]
    _delete_progress(video_id)
    _delete_pose_file(video_id)
    aa.delete(analysis_id)

    return jsonify({"status": "deleted", "id": analysis_id}), 200


@analysis_bp.route("/api/admin/jobs/cleanup", methods=["POST"])
def api_cleanup_jobs():
    repo = get_repo()
    aa = AnalysesAdapter(repo)
    stuck = aa.select(status="running")
    cleaned = 0
    for a in stuck:
        prog = _read_progress(a["video_id"])
        if prog and prog.get("status") == "running":
            # Check age — if older than 30 min, it's dead
            created = a.get("created_at")
            if created and (datetime.now(timezone.utc) - created) < timedelta(minutes=30):
                continue  # Actually still running
        _delete_progress(a["video_id"])
        aa.set_status(a["id"], "error")
        cleaned += 1

    return jsonify({"cleaned": cleaned})


# ── Feature Extraction & Move Matching ─────────────────────────────────────


@analysis_bp.route("/api/videos/<int:video_id>/compute-features", methods=["POST"])
def api_compute_features(video_id):
    """Compute pose feature vectors for move matching."""
    repo = get_repo()
    video = VideosAdapter(repo).get(video_id)
    if not video:
        return jsonify({"error": "video not found"}), 404
    if not video.get("has_pose"):
        return jsonify({"error": "no pose data — run analysis first"}), 400

    analysis = AnalysesAdapter(repo).get_by_video(video_id)
    if not analysis or analysis["status"] != "done":
        return jsonify({"error": "analysis not complete"}), 400

    summary = analysis.get("summary")
    if isinstance(summary, str):
        summary = json.loads(summary)
    path = summary.get("pose_gcs_path") if summary else f"poses/{video_id}.json"

    try:
        blob = _gcs().bucket(BUCKET_NAME).blob(path)
        pose_data = json.loads(blob.download_as_bytes())
    except Exception as e:
        return jsonify({"error": f"failed to load pose data: {e}"}), 500

    from core.pose_features import compute_and_store_features
    if compute_and_store_features(video_id, pose_data):
        repo.update("bachata.videos", video_id, {"has_features": True})
        return jsonify({"status": "done"}), 200
    else:
        return jsonify({"error": "no pose frames to extract features from"}), 400


@analysis_bp.route("/api/videos/<int:video_id>/find-similar", methods=["POST"])
def api_find_similar(video_id):
    """Find similar move segments across other videos."""
    repo = get_repo()
    video = VideosAdapter(repo).get(video_id)
    if not video:
        return jsonify({"error": "video not found"}), 404

    body = request.get_json(silent=True) or {}
    start_sec = body.get("start_sec")
    end_sec = body.get("end_sec")
    if start_sec is None or end_sec is None:
        return jsonify({"error": "start_sec and end_sec required"}), 400
    if end_sec - start_sec < 0.5:
        return jsonify({"error": "selection too short (min 0.5s)"}), 400

    person_id = body.get("person_id", 0)
    top_k = min(body.get("top_k", 5), 10)

    # Load query video features
    from core.pose_features import load_features_from_gcs
    query_data = load_features_from_gcs(video_id)
    if query_data is None:
        return jsonify({"error": "no features — compute features first"}), 400

    # Find candidate videos (all with features, excluding query video)
    all_videos = VideosAdapter(repo).select(has_features=True)
    candidate_ids = [v["id"] for v in all_videos if v["id"] != video_id]

    if not candidate_ids:
        return jsonify({"matches": [], "message": "no other videos with features"}), 200

    from core.pose_matching import find_matches
    matches = find_matches(
        query_features=query_data["features"],
        query_timestamps=query_data["timestamps"],
        start_sec=start_sec,
        end_sec=end_sec,
        candidate_video_ids=candidate_ids,
        person_id=person_id,
        top_k=top_k,
    )

    # Enrich results with video metadata
    for m in matches:
        v = VideosAdapter(repo).get(m["video_id"])
        if v:
            m["title"] = v.get("title", "")
            m["thumbnail_url"] = v.get("thumbnail_url", "")
            m["gcs_url"] = v.get("gcs_url", "")

    return jsonify({"matches": matches}), 200


@analysis_bp.route("/api/admin/compute-all-features", methods=["POST"])
def api_compute_all_features():
    """Backfill: compute features for all analyzed videos missing them."""
    repo = get_repo()
    videos = VideosAdapter(repo).select(has_pose=True)
    computed = 0
    errors = 0
    for v in videos:
        if v.get("has_features"):
            continue
        vid = v["id"]
        analysis = AnalysesAdapter(repo).get_by_video(vid)
        if not analysis or analysis["status"] != "done":
            continue
        summary = analysis.get("summary")
        if isinstance(summary, str):
            summary = json.loads(summary)
        path = summary.get("pose_gcs_path") if summary else f"poses/{vid}.json"
        try:
            blob = _gcs().bucket(BUCKET_NAME).blob(path)
            pose_data = json.loads(blob.download_as_bytes())
            from core.pose_features import compute_and_store_features
            if compute_and_store_features(vid, pose_data):
                repo.update("bachata.videos", vid, {"has_features": True})
                computed += 1
            else:
                errors += 1
        except Exception:
            errors += 1

    return jsonify({"computed": computed, "errors": errors})
