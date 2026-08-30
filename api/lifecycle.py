"""API endpoints for local artifact lifecycle visibility and cleanup."""
from flask import Blueprint, jsonify, request, send_file

from data.workspace import workspace_manager
from .state import require_session_ownership

from data.memory_store import (
    list_memory_trash,
    reclaim_expired_memory_trash,
    restore_memory_trash,
)

from infrastructure.artifact_lifecycle import (
    artifact_cleanup_preview,
    lifecycle_audit,
    lifecycle_report,
    load_lifecycle_settings,
    list_artifact_trash,
    list_session_trash,
    list_upload_trash,
    prune_missing_registered,
    reclaim_expired_artifact_trash,
    reclaim_expired_session_trash,
    reclaim_expired_upload_trash,
    recycle_registered_artifact,
    recycle_unregistered_artifact,
    registered_artifact_reference_preview,
    restore_artifact_trash,
    save_lifecycle_settings,
    uploads_storage_preview,
    recycle_upload_file,
    restore_upload_trash,
    workspace_storage_preview,
    restore_session_trash,
    list_registered_artifacts,
    record_artifact_download,
    resolve_registered_artifact_path,
)

bp = Blueprint("lifecycle", __name__)


@bp.before_request
def _check_auth():
    _require_cloud_auth()


def _require_cloud_auth():
    """Require an authenticated user in cloud-managed deployments.

    In desktop mode (no cloud env) this is a no-op so local users can still
    manage their own storage without a login step.
    """
    from .auth import is_cloud_managed, current_user
    if not is_cloud_managed():
        return  # desktop — no auth required
    user = current_user()
    if not user:
        from flask import abort
        abort(401)


def _retention_days() -> int:
    raw = (request.json or {}).get("retention_days", 30)
    try:
        days = int(raw)
    except (TypeError, ValueError):
        raise ValueError("保留天数必须是非负整数")
    if not 0 <= days <= 3650:
        raise ValueError("保留天数必须在 0 到 3650 之间")
    return days


@bp.get("/api/lifecycle/audit")
def get_lifecycle_audit():
    raw_limit = request.args.get("limit", "50")
    try:
        limit = int(raw_limit)
    except ValueError:
        return jsonify({"ok": False, "error": "limit 必须是整数"}), 400
    return jsonify({"ok": True, "items": lifecycle_audit(limit)})

@bp.get("/api/lifecycle/report")
def get_report():
    return jsonify({"ok": True, "report": lifecycle_report()})


@bp.get("/api/lifecycle/settings")
def get_lifecycle_settings():
    return jsonify({"ok": True, "settings": load_lifecycle_settings()})


@bp.put("/api/lifecycle/settings")
def update_lifecycle_settings():
    try:
        settings = save_lifecycle_settings(request.json or {})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "settings": settings})


@bp.get("/api/lifecycle/workspaces/preview")
def get_workspace_storage_preview():
    return jsonify({"ok": True, "preview": workspace_storage_preview(workspace_manager.list_known())})

@bp.get("/api/lifecycle/artifacts/preview")
def get_artifact_cleanup_preview():
    return jsonify({"ok": True, "preview": artifact_cleanup_preview()})


@bp.get("/api/lifecycle/artifacts")
def get_registered_artifacts():
    sid = str(request.args.get("session_id") or "")[:160]
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        return jsonify({"ok": False, "error": "limit 必须是整数"}), 400
    artifacts = _enrich_registered_artifacts(list_registered_artifacts(session_id=sid, limit=limit))
    return jsonify({"ok": True, "artifacts": artifacts})


def _artifact_governance_audit(artifact, session_id: str = ""):
    """Return only human Claim decisions belonging to this artifact/session."""
    claim_ids = {str(value) for value in (artifact.get("claim_ids") or []) if value}
    task_ids = {str(value) for value in (artifact.get("task_ids") or []) if value}
    if artifact.get("task_id"):
        task_ids.add(str(artifact["task_id"]))
    if not claim_ids and not task_ids:
        return []
    result = []
    for event in lifecycle_audit(200):
        if event.get("event") != "claim_decision":
            continue
        if session_id and str(event.get("session_id") or "") != str(session_id):
            continue
        if str(event.get("claim_id") or "") not in claim_ids and str(event.get("task_id") or "") not in task_ids:
            continue
        result.append({key: event.get(key, "") for key in (
            "at", "claim_id", "task_id", "previous_decision", "decision", "reason",
        )})
    return result


def _enrich_registered_artifacts(artifacts, *, session_id: str = ""):
    """Attach safe ledger lineage to already session-filtered artifacts."""
    # Enrich only explicitly recorded Claim/Evidence IDs; local paths remain hidden.
    try:
        from api.pfs import _ledger
        ledger = _ledger()
        for artifact in artifacts:
            claims = []
            evidence_by_id = {}
            for claim_id in (artifact.get("claim_ids") or []):
                try:
                    detail = ledger.claim_detail(str(claim_id))
                except Exception:
                    continue
                claim = detail.get("claim")
                if isinstance(claim, dict):
                    claims.append(claim)
                for evidence in detail.get("evidence") or []:
                    if isinstance(evidence, dict) and evidence.get("evidence_id"):
                        evidence_by_id[str(evidence["evidence_id"])] = evidence
            for evidence_id in (artifact.get("evidence_ids") or []):
                evidence = ledger.get_evidence(str(evidence_id))
                if evidence is not None:
                    evidence_by_id[str(evidence_id)] = evidence.to_dict()
            artifact["lineage"] = {
                "claims": claims,
                "evidence": list(evidence_by_id.values()),
                "governance_audit": _artifact_governance_audit(artifact, session_id),
                "available": bool(claims or evidence_by_id),
            }
    except Exception:
        for artifact in artifacts:
            artifact["lineage"] = {"claims": [], "evidence": [], "governance_audit": [], "available": False}
    return artifacts


@bp.get("/api/session/<sid>/lifecycle/artifacts")
@require_session_ownership
def get_session_registered_artifacts(sid: str):
    """List only active artifacts owned by the caller's session."""
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        return jsonify({"ok": False, "error": "limit 必须是整数"}), 400
    # Keep the history list cheap; full lineage is fetched by the detail route.
    artifacts = list_registered_artifacts(session_id=str(sid)[:160], limit=limit)
    for item in artifacts:
        item["download_url"] = f"/api/session/{sid}/lifecycle/artifacts/{item['id']}/download"
        item["detail_url"] = f"/api/session/{sid}/lifecycle/artifacts/{item['id']}"
    return jsonify({"ok": True, "artifacts": artifacts})


@bp.get("/api/session/<sid>/lifecycle/artifacts/<artifact_id>")
@require_session_ownership
def get_session_registered_artifact(sid: str, artifact_id: str):
    """Read one active artifact and its lineage within the owning session."""
    artifacts = list_registered_artifacts(session_id=str(sid)[:160], limit=200)
    matches = [item for item in artifacts if str(item.get("id") or "") == str(artifact_id)]
    if not matches:
        return jsonify({"ok": False, "error": "产物不存在或不属于此会话", "code": "artifact_not_found"}), 404
    artifact = _enrich_registered_artifacts(matches, session_id=str(sid)[:160])[0]
    artifact["download_url"] = f"/api/session/{sid}/lifecycle/artifacts/{artifact['id']}/download"
    artifact["detail_url"] = f"/api/session/{sid}/lifecycle/artifacts/{artifact['id']}"
    return jsonify({"ok": True, "artifact": artifact})


@bp.get("/api/session/<sid>/lifecycle/artifacts/<artifact_id>/download")
@require_session_ownership
def download_session_registered_artifact(sid: str, artifact_id: str):
    """Download an active registered artifact only from its owning session."""
    items = list_registered_artifacts(session_id=str(sid)[:160], limit=200)
    matches = [item for item in items if str(item.get("id") or "") == str(artifact_id)]
    if not matches:
        return jsonify({"ok": False, "error": "产物不存在或不属于此会话", "code": "artifact_not_found"}), 404
    resolved = resolve_registered_artifact_path(str(artifact_id), session_id=str(sid)[:160])
    if resolved is None:
        return jsonify({"ok": False, "error": "产物文件已不存在", "code": "artifact_missing"}), 404
    _, target = resolved
    record_artifact_download(str(artifact_id))
    return send_file(target, as_attachment=True, download_name=target.name)


@bp.get("/api/lifecycle/uploads/preview")
def get_uploads_storage_preview():
    return jsonify({"ok": True, "preview": uploads_storage_preview()})


@bp.post("/api/lifecycle/uploads/recycle")
def recycle_upload():
    payload = request.json or {}
    try:
        summary = recycle_upload_file(
            str(payload.get("category") or ""),
            str(payload.get("relative_path") or ""),
        )
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "上传文件不存在或已被处理"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": summary})


@bp.get("/api/lifecycle/upload-trash")
def get_upload_trash():
    return jsonify({"ok": True, "items": list_upload_trash()})


@bp.post("/api/lifecycle/upload-trash/reclaim")
def reclaim_upload_trash():
    try:
        days = _retention_days()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": reclaim_expired_upload_trash(retention_days=days)})


@bp.post("/api/lifecycle/upload-trash/<trash_id>/restore")
def restore_upload(trash_id: str):
    try:
        summary = restore_upload_trash(trash_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "上传回收站项目不存在"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": summary})


@bp.get("/api/lifecycle/artifacts/references/preview")
def get_artifact_reference_preview():
    return jsonify({"ok": True, "preview": registered_artifact_reference_preview()})


@bp.post("/api/lifecycle/artifacts/prune-missing")
def prune_missing_artifacts():
    """Drop active registry entries whose files no longer exist on disk."""
    return jsonify({"ok": True, "summary": prune_missing_registered()})


@bp.post("/api/lifecycle/artifacts/unregistered/recycle")
def recycle_legacy_artifact():
    payload = request.json or {}
    try:
        summary = recycle_unregistered_artifact(
            str(payload.get("type") or ""),
            str(payload.get("relative_path") or ""),
        )
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "历史产物不存在或已被处理"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": summary})


@bp.post("/api/lifecycle/artifacts/registered/recycle")
def recycle_registered_artifact_endpoint():
    payload = request.json or {}
    try:
        summary = recycle_registered_artifact(str(payload.get("artifact_id") or ""))
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "已登记产物不存在或已被处理"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": summary})


@bp.get("/api/lifecycle/artifact-trash")
def get_artifact_trash():
    return jsonify({"ok": True, "items": list_artifact_trash()})


@bp.post("/api/lifecycle/artifact-trash/reclaim")
def reclaim_artifact_trash():
    try:
        days = _retention_days()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": reclaim_expired_artifact_trash(retention_days=days)})


@bp.post("/api/lifecycle/artifact-trash/<trash_id>/restore")
def restore_artifact(trash_id: str):
    try:
        summary = restore_artifact_trash(trash_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "产物回收站项目不存在"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": summary})


@bp.get("/api/lifecycle/session-trash")
def get_session_trash():
    return jsonify({"ok": True, "items": list_session_trash()})


@bp.post("/api/lifecycle/session-trash/reclaim")
def reclaim_session_trash():
    try:
        days = _retention_days()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": reclaim_expired_session_trash(retention_days=days)})


@bp.post("/api/lifecycle/session-trash/<trash_id>/restore")
def restore_session(trash_id: str):
    try:
        summary = restore_session_trash(trash_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "回收站项目不存在"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": summary})


@bp.get("/api/lifecycle/memory-trash")
def get_memory_trash():
    return jsonify({"ok": True, "items": list_memory_trash()})


@bp.post("/api/lifecycle/memory-trash/reclaim")
def reclaim_memory_trash():
    try:
        days = _retention_days()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": reclaim_expired_memory_trash(retention_days=days)})


@bp.post("/api/lifecycle/memory-trash/<trash_id>/restore")
def restore_memory(trash_id: str):
    try:
        summary = restore_memory_trash(trash_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "记忆回收站项目不存在"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "summary": summary})
