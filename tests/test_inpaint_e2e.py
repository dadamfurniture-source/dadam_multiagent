"""E2E 테스트: 마스크 기반 인페인팅 파이프라인

실제 API를 호출하여 프로젝트 생성 → 파이프라인 실행 → 결과 확인
테스트 이미지: PIL로 가상 주방 이미지 생성 (실제 사진 대신)
"""

import io
import json
import time

import httpx
from PIL import Image, ImageDraw, ImageFont

API = "http://localhost:8000/api/v1"


def create_test_kitchen_image() -> bytes:
    """테스트용 주방 이미지 생성 (800x600)

    벽 타일 패턴 + 바닥 + 천장이 있는 가상 주방 이미지.
    인페인팅 후 벽/바닥이 보존되는지 확인 가능.
    """
    img = Image.new("RGB", (800, 600), (240, 235, 228))  # 크림색 배경
    draw = ImageDraw.Draw(img)

    # 천장 (상단 15%)
    draw.rectangle([0, 0, 800, 90], fill=(250, 248, 245))

    # 벽면 타일 패턴 (격자)
    tile_color = (200, 195, 185)
    grout_color = (220, 215, 208)
    for y in range(90, 450, 40):
        for x in range(0, 800, 60):
            draw.rectangle([x+1, y+1, x+58, y+38], fill=tile_color)
    # 그라우트 라인
    for y in range(90, 450, 40):
        draw.line([(0, y), (800, y)], fill=grout_color, width=2)
    for x in range(0, 800, 60):
        draw.line([(x, 90), (x, 450)], fill=grout_color, width=2)

    # 바닥 (하단 25%)
    draw.rectangle([0, 450, 800, 600], fill=(180, 170, 155))
    # 바닥 패턴
    for x in range(0, 800, 100):
        draw.line([(x, 450), (x, 600)], fill=(175, 165, 150), width=1)

    # 배관 표시 (급수 - 파란점)
    draw.ellipse([300, 380, 320, 400], fill=(100, 150, 220))
    # 배기 덕트 (빨간 사각)
    draw.rectangle([550, 100, 580, 130], fill=(220, 100, 80))

    # 공사 잔해 시뮬레이션 (작은 사각형들)
    draw.rectangle([150, 420, 180, 445], fill=(160, 140, 120))
    draw.rectangle([400, 430, 420, 448], fill=(140, 130, 115))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def get_test_token() -> str:
    """Supabase에서 테스트 토큰 획득"""
    # config에서 Supabase 정보 가져오기
    resp = httpx.get(f"{API}/config")
    config = resp.json()

    supabase_url = config["supabase_url"]
    supabase_key = config["supabase_anon_key"]

    # 테스트 계정으로 로그인 시도
    auth_resp = httpx.post(
        f"{supabase_url}/auth/v1/token?grant_type=password",
        headers={
            "apikey": supabase_key,
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
        # 회원가입 시도
        signup_resp = httpx.post(
            f"{supabase_url}/auth/v1/signup",
            headers={
                "apikey": supabase_key,
                "Content-Type": "application/json",
            },
            json={
                "email": "test-inpaint@dadam.test",
                "password": "testpass123!",
            },
            timeout=10,
        )
        if signup_resp.status_code == 200:
            data = signup_resp.json()
            return data.get("access_token", "")
        print(f"Signup also failed: {signup_resp.status_code}")
        return ""

    return auth_resp.json()["access_token"]


def main():
    print("=" * 60)
    print("마스크 기반 인페인팅 E2E 테스트")
    print("=" * 60)

    # 1. 인증 토큰 획득
    print("\n[1/5] 인증 토큰 획득...")
    token = get_test_token()
    if not token:
        print("ERROR: 토큰 획득 실패. 수동으로 토큰을 입력하세요.")
        return
    print(f"  Token: {token[:20]}...")

    headers = {"Authorization": f"Bearer {token}"}

    # 2. 테스트 이미지 생성
    print("\n[2/5] 테스트 주방 이미지 생성...")
    image_bytes = create_test_kitchen_image()
    print(f"  Image size: {len(image_bytes):,} bytes")

    # 3. 프로젝트 생성
    print("\n[3/5] 프로젝트 생성 (category=sink, style=modern)...")
    resp = httpx.post(
        f"{API}/projects",
        headers=headers,
        files={"image": ("test_kitchen.jpg", image_bytes, "image/jpeg")},
        data={"category": "sink", "style": "modern"},
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"  ERROR: {resp.status_code} - {resp.text[:300]}")
        return

    result = resp.json()
    project_id = result["data"]["project_id"]
    print(f"  Project ID: {project_id}")
    print(f"  Status: {result['data'].get('status', 'created')}")

    # 4. 파이프라인 실행
    print("\n[4/5] AI 파이프라인 실행...")
    run_resp = httpx.post(
        f"{API}/projects/{project_id}/run",
        headers=headers,
        timeout=30,
    )

    if run_resp.status_code != 200:
        print(f"  ERROR: {run_resp.status_code} - {run_resp.text[:300]}")
        return

    print(f"  Pipeline started: {run_resp.json().get('message')}")

    # 5. 결과 폴링 (최대 5분)
    print("\n[5/5] 결과 대기 (최대 5분)...")
    start = time.time()
    last_stage = ""

    while time.time() - start < 300:
        time.sleep(5)

        status_resp = httpx.get(
            f"{API}/projects/{project_id}",
            headers=headers,
            timeout=10,
        )

        if status_resp.status_code != 200:
            print(f"  Poll error: {status_resp.status_code}")
            continue

        data = status_resp.json().get("data", {})
        status = data.get("status", "unknown")
        stage = data.get("pipeline_stage", "")
        elapsed = int(time.time() - start)

        if stage != last_stage:
            print(f"  [{elapsed}s] Stage: {stage} | Status: {status}")
            last_stage = stage

        if status == "completed":
            print(f"\n  ✅ 파이프라인 완료! ({elapsed}초)")

            # 생성된 이미지 목록 확인
            images = data.get("images", [])
            if images:
                print(f"\n  생성된 이미지 ({len(images)}장):")
                for img in images:
                    print(f"    - {img.get('type', '?')}: {img.get('image_url', '')[:80]}...")

            # 견적 확인
            quote = data.get("quote", {})
            if quote:
                items_data = quote.get("items_json", quote)
                if isinstance(items_data, dict):
                    total = items_data.get("total", 0)
                    print(f"\n  견적 합계: {total:,}원")

            return

        if status == "failed":
            print(f"\n  ❌ 파이프라인 실패 ({elapsed}초)")
            print(f"  Stage: {stage}")
            return

    print("\n  ⏰ 타임아웃 (5분)")


if __name__ == "__main__":
    main()
