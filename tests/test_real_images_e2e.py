"""E2E 테스트: testimage 폴더의 실제 주방 사진으로 이미지 생성 파이프라인 테스트

Usage:
    python tests/test_real_images_e2e.py              # 전체 4장 테스트
    python tests/test_real_images_e2e.py 1             # 첫 번째 이미지만
"""

import sys
import time
from pathlib import Path

import httpx

API = "http://localhost:8000/api/v1"
TESTIMAGE_DIR = Path(__file__).parent.parent / "db" / "testimage"
RESULTIMAGE_DIR = Path(__file__).parent.parent / "db" / "resultimage"

IMAGES = sorted([
    f for f in TESTIMAGE_DIR.glob("*.jpg")
    if f.is_file() and f.parent == TESTIMAGE_DIR  # back/ 하위 제외
])

CATEGORIES = ["sink"] * len(IMAGES)  # 모두 주방 이미지이므로 sink


def get_test_token() -> str:
    resp = httpx.get(f"{API}/config")
    config = resp.json()

    auth_resp = httpx.post(
        f"{config['supabase_url']}/auth/v1/token?grant_type=password",
        headers={
            "apikey": config["supabase_anon_key"],
            "Content-Type": "application/json",
        },
        json={
            "email": "dadamfurniture@gmail.com",
            "password": "test1234!",
        },
        timeout=10,
    )

    if auth_resp.status_code != 200:
        print(f"Auth failed ({auth_resp.status_code}): {auth_resp.text[:200]}")
        return ""

    return auth_resp.json()["access_token"]


def run_test(image_path: Path, category: str, token: str, idx: int):
    headers = {"Authorization": f"Bearer {token}"}
    filename = image_path.name
    size_kb = image_path.stat().st_size / 1024

    print(f"\n{'='*60}")
    print(f"[{idx}] {filename} ({size_kb:.0f}KB) | category={category}")
    print(f"{'='*60}")

    image_bytes = image_path.read_bytes()

    # 1. 프로젝트 생성
    print("[1/3] 프로젝트 생성...")
    t0 = time.time()
    resp = httpx.post(
        f"{API}/projects",
        headers=headers,
        files={"image": (filename, image_bytes, "image/jpeg")},
        data={"category": category, "style": "modern"},
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"  ERROR: {resp.status_code} - {resp.text[:300]}")
        return {"image": filename, "status": "CREATE_FAIL", "error": resp.text[:200]}

    result = resp.json()
    project_id = result["data"]["project_id"]
    print(f"  Project ID: {project_id}")

    # 2. 파이프라인 실행
    print("[2/3] AI 파이프라인 실행...")
    run_resp = httpx.post(
        f"{API}/projects/{project_id}/run",
        headers=headers,
        timeout=30,
    )

    if run_resp.status_code != 200:
        print(f"  ERROR: {run_resp.status_code} - {run_resp.text[:300]}")
        return {"image": filename, "status": "RUN_FAIL", "error": run_resp.text[:200]}

    print(f"  Pipeline started")

    # 3. 결과 폴링 (최대 5분)
    print("[3/3] 결과 대기 (최대 5분)...")
    last_stage = ""

    while time.time() - t0 < 300:
        time.sleep(5)

        status_resp = httpx.get(
            f"{API}/projects/{project_id}",
            headers=headers,
            timeout=10,
        )

        if status_resp.status_code != 200:
            continue

        data = status_resp.json().get("data", {})
        proj = data.get("project", data)  # API returns data.project.status
        status = proj.get("status", "unknown")
        stage = proj.get("pipeline_stage", "")
        elapsed = int(time.time() - t0)

        if stage != last_stage:
            print(f"  [{elapsed}s] Stage: {stage} | Status: {status}")
            last_stage = stage

        if status == "completed":
            images = data.get("images", [])
            quote = data.get("quote") or {}
            total = 0
            if quote:
                items_data = quote.get("items_json", quote)
                if isinstance(items_data, dict):
                    total = items_data.get("total", 0)
                elif isinstance(items_data, str):
                    import json
                    try:
                        parsed = json.loads(items_data)
                        total = parsed.get("total", 0) if isinstance(parsed, dict) else 0
                    except Exception:
                        pass

            print(f"  PASS ({elapsed}s) | images: {len(images)}장 | 견적: {total:,}원")

            for img in images:
                print(f"    - {img.get('type', '?')}: {img.get('image_url', '')[:80]}...")

            # 결과 이미지 다운로드 → resultimage 폴더에 저장
            stem = image_path.stem
            RESULTIMAGE_DIR.mkdir(parents=True, exist_ok=True)
            for img in images:
                img_type = img.get("type", "unknown")
                img_url = img.get("image_url", "")
                if not img_url:
                    continue
                try:
                    dl = httpx.get(img_url, timeout=30, follow_redirects=True)
                    if dl.status_code == 200:
                        ext = "png" if img_type != "original" else "jpg"
                        save_path = RESULTIMAGE_DIR / f"{stem}_{img_type}.{ext}"
                        save_path.write_bytes(dl.content)
                        print(f"    -> Saved: {save_path.name} ({len(dl.content)//1024}KB)")
                    else:
                        print(f"    -> Download failed ({dl.status_code}): {img_type}")
                except Exception as e:
                    print(f"    -> Download error ({img_type}): {e}")

            return {
                "image": filename, "status": "PASS", "time": elapsed,
                "images_count": len(images), "quote_total": total,
                "project_id": project_id,
            }

        if status == "failed":
            print(f"  FAIL ({elapsed}s) | stage: {stage}")
            error_msg = data.get("error", "")
            if error_msg:
                print(f"  Error: {error_msg[:200]}")
            return {"image": filename, "status": "FAIL", "time": elapsed, "stage": stage}

    elapsed = int(time.time() - t0)
    print(f"  TIMEOUT ({elapsed}s)")
    return {"image": filename, "status": "TIMEOUT", "time": elapsed}


def main():
    if not IMAGES:
        print(f"ERROR: testimage 폴더에 이미지 없음: {TESTIMAGE_DIR}")
        return

    # 특정 이미지만 테스트
    target = int(sys.argv[1]) if len(sys.argv) > 1 else None
    test_images = [IMAGES[target - 1]] if target else IMAGES
    test_cats = [CATEGORIES[target - 1]] if target else CATEGORIES[:len(IMAGES)]

    print("=" * 60)
    print(f"  다담 이미지 생성 E2E 테스트 ({len(test_images)}장)")
    print("=" * 60)
    for i, img in enumerate(test_images):
        print(f"  {i+1}. {img.name} ({img.stat().st_size/1024:.0f}KB)")

    # 인증
    print("\n[Auth] 토큰 획득...")
    token = get_test_token()
    if not token:
        print("ERROR: 토큰 획득 실패")
        return
    print(f"  Token: {token[:20]}...")

    # 테스트 실행
    results = []
    for i, (img, cat) in enumerate(zip(test_images, test_cats)):
        r = run_test(img, cat, token, i + 1)
        results.append(r)

    # Summary
    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    passed = sum(1 for r in results if r["status"] == "PASS")
    for r in results:
        icon = "PASS" if r["status"] == "PASS" else "FAIL"
        detail = f"{r.get('time', '?')}s" if "time" in r else r.get("error", "")[:50]
        print(f"  [{icon}] {r['image'][:40]} | {detail}")
    print(f"\n  Total: {passed}/{len(results)} passed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
