#!/usr/bin/env python3
"""Safe hourly collector runner for the hotdeal board.

Runs the live collector, validates the generated JSON, and restores the
previous successful JSON if the new collection looks unsafe.

Designed for scheduled execution: success is quiet; failures write diagnostics
and exit non-zero so the scheduler can alert.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = PROJECT_ROOT / "scripts" / "collect_sample.py"
DATA_OUT = PROJECT_ROOT / "data" / "deals.json"
PUBLIC_OUT = PROJECT_ROOT / "public" / "data" / "deals.json"
LOG_OUT = PROJECT_ROOT / "data" / "collection.log"
STATUS_OUT = PROJECT_ROOT / "data" / "collection_status.json"
BACKUP_DIR = PROJECT_ROOT / "data" / ".backups"
MIN_TOTAL_DEALS = 100
EXPECTED_SOURCES = {"kakao", "11st", "coupang", "naver", "ssg", "lotteon"}
MOJIBAKE_RE = re.compile(r"[ëìíê�]")
SEVERE_DROP_RATIO = 0.2


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def backup_current() -> dict[Path, Path]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backups: dict[Path, Path] = {}
    for path in (DATA_OUT, PUBLIC_OUT):
        if path.exists():
            backup = BACKUP_DIR / f"{path.parent.name}-{path.name}.{stamp}.bak"
            shutil.copy2(path, backup)
            backups[path] = backup
    return backups


def restore(backups: dict[Path, Path]) -> None:
    for target, backup in backups.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, target)


def load_previous_payload(backups: dict[Path, Path]) -> dict | None:
    backup = backups.get(PUBLIC_OUT)
    if not backup or not backup.exists():
        return None
    try:
        return json.loads(backup.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt backup should not break fresh valid output.
        return None


def rebuild_source_summary(payload: dict) -> None:
    deals = payload.get("deals") or []
    sources = payload.get("sources") or {}
    by_source: dict[str, list[dict]] = {sid: [] for sid in sources}
    for deal in deals:
        by_source.setdefault(deal.get("source_id"), []).append(deal)

    summary = {}
    for sid, source in sources.items():
        rows = by_source.get(sid, [])
        categories: dict[str, int] = {}
        discounts = []
        for deal in rows:
            label = ((deal.get("canonical_category") or {}).get("label")) or "기타"
            categories[label] = categories.get(label, 0) + 1
            discount = deal.get("discount_rate")
            if isinstance(discount, int):
                discounts.append(discount)
        top_categories = sorted(categories.items(), key=lambda item: item[1], reverse=True)[:3]
        checked_times = [deal["checked_at"] for deal in rows if deal.get("checked_at")]
        summary[sid] = {
            "source_id": sid,
            "name": source.get("name"),
            "deal_type": source.get("deal_type"),
            "accent": source.get("accent"),
            "count": len(rows),
            "top_categories": top_categories,
            "avg_discount": round(sum(discounts) / len(discounts)) if discounts else None,
            "last_checked_at": max(checked_times) if checked_times else None,
        }
    payload["source_summary"] = summary


def preserve_previous_on_source_drop(payload: dict, previous_payload: dict | None) -> list[str]:
    if not previous_payload:
        return []

    previous_rows_by_source: dict[str, list[dict]] = {}
    for deal in previous_payload.get("deals") or []:
        previous_rows_by_source.setdefault(deal.get("source_id"), []).append(deal)

    current_rows_by_source: dict[str, list[dict]] = {}
    for deal in payload.get("deals") or []:
        current_rows_by_source.setdefault(deal.get("source_id"), []).append(deal)

    stale_sources = []
    manifest = payload.setdefault("manifest", {})
    for sid in sorted(EXPECTED_SOURCES):
        previous_count = len(previous_rows_by_source.get(sid, []))
        current_count = len(current_rows_by_source.get(sid, []))
        severe_floor = max(1, int(previous_count * SEVERE_DROP_RATIO))
        severe_drop = previous_count >= 10 and current_count < severe_floor
        zero_drop = previous_count > 0 and current_count == 0
        if not (zero_drop or severe_drop):
            continue

        stale_sources.append(sid)
        current_rows_by_source[sid] = previous_rows_by_source[sid]
        manifest[sid] = {
            "status": "stale",
            "count": previous_count,
            "error": f"kept previous data because new count {current_count} dropped from previous {previous_count}",
        }

    if stale_sources:
        merged_deals = []
        seen_ids = set()
        for sid in ["kakao", "11st", "coupang", "naver", "ssg", "lotteon"]:
            for deal in current_rows_by_source.get(sid, []):
                deal_id = deal.get("id")
                if deal_id in seen_ids:
                    continue
                seen_ids.add(deal_id)
                merged_deals.append(deal)
        payload["deals"] = merged_deals
        rebuild_source_summary(payload)
    return stale_sources


def write_payload(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
    DATA_OUT.write_text(text, encoding="utf-8")
    PUBLIC_OUT.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_OUT.write_text(text, encoding="utf-8")


def validate_payload(path: Path) -> tuple[dict, list[str]]:
    errors: list[str] = []
    payload = json.loads(path.read_text(encoding="utf-8"))
    deals = payload.get("deals") or []
    manifest = payload.get("manifest") or {}

    if len(deals) < MIN_TOTAL_DEALS:
        errors.append(f"deal count too low: {len(deals)} < {MIN_TOTAL_DEALS}")

    missing_sources = EXPECTED_SOURCES - set(manifest)
    if missing_sources:
        errors.append(f"missing manifest sources: {sorted(missing_sources)}")

    bad_sources = {
        sid: meta for sid, meta in manifest.items()
        if sid in EXPECTED_SOURCES and meta.get("status") not in {"ok", "stale"}
    }
    if bad_sources:
        errors.append(f"source errors: {bad_sources}")

    zero_sources = [sid for sid in EXPECTED_SOURCES if (manifest.get(sid) or {}).get("count", 0) <= 0]
    if zero_sources:
        errors.append(f"zero-count sources: {sorted(zero_sources)}")

    ids = [deal.get("id") for deal in deals]
    duplicate_count = len(ids) - len(set(ids))
    if duplicate_count:
        errors.append(f"duplicate deal ids: {duplicate_count}")

    mojibake_count = sum(1 for deal in deals if MOJIBAKE_RE.search(deal.get("title") or ""))
    if mojibake_count:
        errors.append(f"mojibake titles: {mojibake_count}")

    missing_prices = sum(1 for deal in deals if deal.get("deal_price") is None)
    if missing_prices > max(5, len(deals) // 20):
        errors.append(f"too many missing prices: {missing_prices}")

    return payload, errors


def write_status(payload: dict, status: str, errors: list[str] | None = None) -> None:
    deals = payload.get("deals") or []
    status_doc = {
        "checked_at": now_iso(),
        "status": status,
        "errors": errors or [],
        "generated_at": payload.get("generated_at"),
        "total_deals": len(deals),
        "manifest": payload.get("manifest") or {},
    }
    STATUS_OUT.parent.mkdir(parents=True, exist_ok=True)
    STATUS_OUT.write_text(json.dumps(status_doc, ensure_ascii=False, indent=2), encoding="utf-8")


def append_log(message: str) -> None:
    LOG_OUT.parent.mkdir(parents=True, exist_ok=True)
    with LOG_OUT.open("a", encoding="utf-8") as fh:
        fh.write(f"{now_iso()} {message}\n")


def fail(message: str, backups: dict[Path, Path], payload: dict | None = None, errors: list[str] | None = None) -> int:
    restore(backups)
    if payload is not None:
        write_status(payload, "restored_previous", errors or [message])
    append_log(f"FAIL {message}")
    print(message, file=sys.stderr)
    if errors:
        for error in errors:
            print(f"- {error}", file=sys.stderr)
    return 1


def main() -> int:
    backups = backup_current()
    proc = subprocess.run(
        [sys.executable, str(COLLECTOR)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=300,
    )
    if proc.returncode != 0:
        return fail(
            "collector process failed; restored previous deals.json",
            backups,
            errors=[proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"],
        )

    try:
        payload = json.loads(PUBLIC_OUT.read_text(encoding="utf-8"))
        stale_sources = preserve_previous_on_source_drop(payload, load_previous_payload(backups))
        if stale_sources:
            write_payload(payload)
        payload, errors = validate_payload(PUBLIC_OUT)
    except Exception as exc:  # noqa: BLE001 - scheduled runner should preserve old data on any parse failure.
        return fail(f"generated JSON validation crashed: {exc!r}; restored previous deals.json", backups)

    if errors:
        return fail("generated JSON failed validation; restored previous deals.json", backups, payload, errors)

    write_status(payload, "ok")
    total = len(payload.get("deals") or [])
    manifest = payload.get("manifest") or {}
    counts = ", ".join(f"{sid}={manifest.get(sid, {}).get('count')}" for sid in sorted(EXPECTED_SOURCES))
    stale_note = "" if not any((m or {}).get("status") == "stale" for m in manifest.values()) else " stale_sources=" + ",".join(sorted(sid for sid, m in manifest.items() if (m or {}).get("status") == "stale"))
    append_log(f"OK total={total} {counts}{stale_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
