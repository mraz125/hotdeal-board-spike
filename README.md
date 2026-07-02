# Hotdeal Board Spike

목적: 같은 상품의 최저가 비교가 아니라, 쇼핑몰별로 지금 어떤 상품군을 핫딜/타임딜로 밀고 있는지 좌우 레인 보드로 비교한다.

## 현재 포함 소스

- 카카오 톡딜
- 쿠팡 타임딜
- 11번가 타임딜
- 네이버 프로모션
- SSG 쓱특가
- 롯데ON 쇼핑특가

## 데이터 계약

수집기는 원본 카테고리를 보존하고, 별도로 통합 카테고리를 붙인다.

```json
{
  "source_category": { "label": "원본명", "path": ["원본", "카테고리"] },
  "canonical_category": { "id": "living", "label": "생활", "confidence": 0.84, "rule": "keyword:living" }
}
```

## 실행

```bash
cd hotdeal-board-spike
python3 -m pip install -r requirements.txt
python3 scripts/hourly_collect.py
python3 -m http.server 8787
```

브라우저에서 열기:

```text
http://127.0.0.1:8787/public/
```

## 스파이크 검증 기준

- 실제 소스에서 6개 쇼핑몰 데이터를 가져온다.
- 원본 카테고리와 통합 카테고리를 함께 보존한다.
- 쇼핑몰별 좌우 레인으로 카드가 배치된다.
- 카드를 클릭하면 원본 쇼핑몰 페이지로 이동한다.
- 필터는 통합 카테고리 기준으로 동작한다.

## 갱신 정책

- 운영 수집 주기: 1시간마다
- 브라우저는 열린 상태에서도 1시간마다 `deals.json`을 다시 요청한다.
- `scripts/hourly_collect.py`는 새 수집 결과를 검증하고, 소스 실패/중복 ID/한글 깨짐/비정상 저수집이 감지되면 해당 소스의 이전 성공 데이터를 유지한다.
- 성공 실행은 조용히 `data/collection.log`와 `data/collection_status.json`만 갱신한다.
- GitHub에서는 `.github/workflows/refresh-hotdeal-board.yml`이 매 정각 데이터를 수집하고 GitHub Pages를 재배포한다.
- 가격/품절은 마지막 수집 시점 기준이며, 최종 조건은 원본 쇼핑몰에서 확인해야 한다.

## 운영 전 보완해야 할 점

- 각 쇼핑몰 약관 확인
- 실패 시 stale 표시 고도화
- 원본 카테고리 매핑 테이블 분리
- 중복 상품 제거 규칙
- 품절/가격 변동 재확인 워커
- 제휴 링크와 광고 고지 정책
