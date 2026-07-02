#!/usr/bin/env python3
"""Collect live promotion data for the hotdeal board prototype.

This is intentionally a spike, not production code. It fetches only public,
non-login endpoints we already probed and writes a normalized board JSON.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "deals.json"
PUBLIC_OUT = ROOT / "public" / "data" / "deals.json"
KST = ZoneInfo("Asia/Seoul")
NOW = datetime.now(timezone.utc)

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 HotdealBoardSpike/0.1",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

SOURCES = {
    "kakao": {
        "name": "카카오 톡딜",
        "deal_type": "톡딜",
        "accent": "#f7d900",
        "human_url": "https://store.kakao.com/home/best",
    },
    "coupang": {
        "name": "쿠팡 타임딜",
        "deal_type": "타임딜",
        "accent": "#2b67f6",
        "human_url": "https://shop.coupang.com/timedeal?locale=ko_KR&platform=p",
    },
    "11st": {
        "name": "11번가 타임딜",
        "deal_type": "타임딜",
        "accent": "#ef3340",
        "human_url": "https://www.11st.co.kr/",
    },
    "naver": {
        "name": "네이버 프로모션",
        "deal_type": "프로모션",
        "accent": "#03c75a",
        "human_url": "https://shopping.naver.com/",
    },
    "ssg": {
        "name": "SSG 쓱특가",
        "deal_type": "쓱특가",
        "accent": "#d71920",
        "human_url": "https://www.ssg.com/page/pc/SpecialPrice/happybuy.ssg",
    },
    "lotteon": {
        "name": "롯데ON 쇼핑특가",
        "deal_type": "쇼핑특가",
        "accent": "#e0002a",
        "human_url": "https://www.lotteon.com/p/display/main/lotteon",
    },
}

CANONICAL = {
    "food": "식품",
    "living": "생활",
    "fashion": "의류/패션",
    "beauty": "뷰티",
    "digital": "디지털/가전",
    "baby": "육아/키즈",
    "sports": "스포츠/레저",
    "travel": "여행/e쿠폰",
    "home": "가구/인테리어",
    "books": "도서/문구",
    "other": "기타",
}

CATEGORY_KEYWORDS = {
    "travel": ["숙박", "호텔", "리조트", "객실", "오션월드", "입장권", "여행", "항공", "e쿠폰", "외식권", "시즌권", "서울랜드", "패키지"],
    "digital": ["노트북", "모니터", "충전", "케이블", "가전", "디지털", "이어폰", "아이폰", "갤럭시", "게임", "SSD", "TV", "스마트폰"],
    "beauty": ["뷰티", "화장품", "클렌징", "선크림", "앰플", "스킨", "로션", "향수", "샴푸", "바디", "디올", "프라다 뷰티", "맥스클리닉"],
    "fashion": ["의류", "패션", "신발", "운동화", "스니커즈", "슬리퍼", "가방", "티셔츠", "반팔", "니트", "잠옷", "파자마", "드로즈", "속옷", "아디다스", "나이키", "크록스"],
    "baby": ["기저귀", "분유", "유아", "아기", "키즈", "완구", "장난감", "아동", "젖병"],
    "sports": ["골프", "캠핑", "등산", "낚시", "헬스", "스포츠", "자전거", "수영", "스키", "구명조끼"],
    "home": ["가구", "침구", "커튼", "카페트", "수납", "인테리어", "매트리스", "홈데코", "침실"],
    "food": ["식품", "김치", "닭", "닭갈비", "냉면", "커피", "과자", "빵", "옥수수", "사골", "도가니탕", "오징어", "진미", "음료", "생수", "홍삼", "건강식품", "삼계탕", "스타벅스", "라유"],
    "living": ["생활", "생필품", "세제", "물티슈", "휴지", "주방", "욕실", "청소", "밀폐용기", "용기", "생리대", "반려", "마스크", "수건"],
    "books": ["도서", "책", "문구", "사무", "노트", "필기"],
}

SOURCE_CATEGORY_MAP = {
    "FOOD": "food",
    "식품": "food",
    "LIFE": "living",
    "생활": "living",
    "DIGITAL": "digital",
    "디지털": "digital",
    "BEAUTY": "beauty",
    "뷰티": "beauty",
    "FASHION": "fashion",
    "패션": "fashion",
    "TRAVEL": "travel",
    "여행": "travel",
    "리빙": "living",
    "생필품": "living",
}


def fix_mojibake(value: Any) -> str:
    text = "" if value is None else str(value)
    if any(marker in text for marker in ("ë", "ì", "í", "ê")):
        try:
            text = text.encode("latin1").decode("utf-8")
        except Exception:
            pass
    return text


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", fix_mojibake(value)).strip()


def price_to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    nums = re.sub(r"[^0-9]", "", str(value))
    return int(nums) if nums else None


def iso_from_ms(ms: Any) -> Optional[str]:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


def iso_from_yyyymmddhhmmss(value: Any) -> Optional[str]:
    s = clean_text(value)
    if not re.fullmatch(r"\d{14}", s):
        return None
    dt = datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    return dt.isoformat()


def ensure_http(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://www.coupang.com" + url
    return url


def categorize(title: str, source_category: str = "", source_path: Optional[List[str]] = None) -> Dict[str, Any]:
    haystack = " ".join([title, source_category, " ".join(source_path or [])]).lower()
    # Exact source mapping first, but only if it is not too generic for keyword override.
    for raw, cid in SOURCE_CATEGORY_MAP.items():
        if raw.lower() in haystack:
            mapped = cid
            break
    else:
        mapped = None

    scores = Counter()
    for cid, words in CATEGORY_KEYWORDS.items():
        for word in words:
            if word.lower() in haystack:
                scores[cid] += 1
    if scores:
        cid, score = scores.most_common(1)[0]
        return {"id": cid, "label": CANONICAL[cid], "confidence": min(0.98, 0.68 + score * 0.1), "rule": f"keyword:{cid}"}
    if mapped:
        return {"id": mapped, "label": CANONICAL[mapped], "confidence": 0.74, "rule": f"source_category:{source_category}"}
    return {"id": "other", "label": CANONICAL["other"], "confidence": 0.35, "rule": "fallback"}


def make_deal(source_id: str, *, external_id: str, title: Any, source_category_label: str = "", source_category_path: Optional[List[str]] = None, image_url: Any = "", url: Any = "", price_label: str = "특가", deal_price: Any = None, original_price: Any = None, discount_rate: Any = None, starts_at: Optional[str] = None, ends_at: Optional[str] = None, badges: Optional[List[str]] = None, status: str = "판매중", raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    source = SOURCES[source_id]
    title = clean_text(title)
    source_category_label = clean_text(source_category_label) or source["deal_type"]
    source_category_path = [clean_text(x) for x in (source_category_path or [source_category_label]) if clean_text(x)]
    category = categorize(title, source_category_label, source_category_path)
    dprice = price_to_int(deal_price)
    oprice = price_to_int(original_price)
    if discount_rate in (None, "") and dprice and oprice and oprice > dprice:
        discount_rate = round((1 - dprice / oprice) * 100)
    try:
        discount_rate = int(float(discount_rate)) if discount_rate not in (None, "") else None
    except Exception:
        discount_rate = None
    badges = [b for b in (badges or []) if b]
    return {
        "id": f"{source_id}:{external_id}",
        "source_id": source_id,
        "source_name": source["name"],
        "deal_type": source["deal_type"],
        "accent": source["accent"],
        "source_home_url": source["human_url"],
        "source_category": {"label": source_category_label, "path": source_category_path},
        "canonical_category": category,
        "title": title,
        "image_url": ensure_http(image_url),
        "url": ensure_http(url),
        "price_label": price_label,
        "deal_price": dprice,
        "original_price": oprice,
        "discount_rate": discount_rate,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "badges": badges[:4],
        "status": status,
        "checked_at": NOW.isoformat(),
        "raw_excerpt": raw or {},
    }


def fetch_json(url: str, *, method: str = "GET", headers: Optional[Dict[str, str]] = None, params: Optional[Dict[str, Any]] = None, json_body: Optional[Dict[str, Any]] = None, timeout: int = 20) -> Dict[str, Any]:
    h = dict(BASE_HEADERS)
    h.update(headers or {})
    if method == "POST":
        r = requests.post(url, headers=h, params=params, json=json_body, timeout=timeout)
    else:
        r = requests.get(url, headers=h, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def collect_kakao(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    endpoint = "https://shopping-channel-api.kakao.com/shopping/f-s/home/tab/talk-deal/products"
    headers = {"Origin": "https://store.kakao.com", "Referer": "https://store.kakao.com/home/best", "Accept": "application/json, text/plain, */*"}
    page_size = min(limit or 100, 100)
    products: List[Dict[str, Any]] = []
    seen_ids = set()
    page = 0
    while True:
        j = fetch_json(endpoint, headers=headers, params={"page": page, "size": page_size})
        data = j.get("data", {})
        rows = data.get("products", [])
        for p in rows:
            pid = p.get("productId")
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            products.append(p)
            if limit and len(products) >= limit:
                break
        if (limit and len(products) >= limit) or data.get("last") or not rows:
            break
        page += 1

    deals = []
    for p in products:
        badges = ["톡딜가"]
        if p.get("freeDelivery"):
            badges.append("무료배송")
        if p.get("groupDiscountUserCount"):
            badges.append(f"참여 {p.get('groupDiscountUserCount'):,}명")
        if p.get("new"):
            badges.append("신규")
        deals.append(make_deal(
            "kakao",
            external_id=str(p.get("productId")),
            title=p.get("productName"),
            source_category_label=p.get("categoryName") or "톡딜",
            source_category_path=["톡딜", p.get("categoryName") or ""],
            image_url=p.get("imageUrl") or p.get("productImage"),
            url="https://store.kakao.com" + (p.get("linkPath") or ""),
            price_label="톡딜가",
            deal_price=p.get("groupDiscountedPrice") or p.get("discountedPrice"),
            original_price=p.get("originalPrice") or p.get("discountedPrice"),
            discount_rate=p.get("groupDiscountRate") or p.get("discountRate"),
            starts_at=iso_from_ms(p.get("groupDiscountStartAt")),
            ends_at=iso_from_ms(p.get("groupDiscountEndAt")),
            badges=badges,
            status="품절" if p.get("displayedSaleStatus") == "SOLD_OUT" else "판매중",
            raw={"groupDiscountRemainSeconds": p.get("groupDiscountRemainSeconds"), "storeName": p.get("storeName")},
        ))
    return deals


def collect_11st(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    j = fetch_json(
        "https://apis.11st.co.kr/pui/v2/page",
        headers={"Origin": "https://www.11st.co.kr", "Referer": "https://www.11st.co.kr/", "Accept": "application/json"},
        params={"pageId": "PCHOMEHOME"},
    )
    products: List[Dict[str, Any]] = []
    for carrier in j.get("data", []):
        for block in carrier.get("blockList", []):
            if block.get("type") == "PC_Product_Deal_Time":
                products.extend(block.get("list", []))
    deals = []
    for p in (products[:limit] if limit else products):
        benefit = []
        for b in (p.get("benefit") or {}).get("web", []):
            if b.get("text1"):
                benefit.append(b["text1"])
        if p.get("remainText"):
            benefit.append(f"잔여 {p.get('remainText')}")
        deals.append(make_deal(
            "11st",
            external_id=str(p.get("prdNo")),
            title=p.get("title1") or (p.get("logData", {}).get("dataBody", {}) or {}).get("product_name"),
            source_category_label="타임딜",
            source_category_path=["홈", "타임딜"],
            image_url=p.get("imageUrl1") or p.get("originalPrdImgUrl"),
            url=p.get("linkUrl1"),
            price_label="타임딜가",
            deal_price=p.get("finalDscPrice"),
            original_price=p.get("sellPrice"),
            discount_rate=None,
            starts_at=iso_from_yyyymmddhhmmss(p.get("displayBeginDate")),
            ends_at=iso_from_yyyymmddhhmmss(p.get("displayEndDate")),
            badges=["타임딜가"] + benefit[:2],
            status="판매중",
            raw={"selQty": p.get("selQty"), "limitQty": p.get("limitQty")},
        ))
    return deals


def collect_coupang(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    body = {
        "page": 0,
        "storeId": 73179,
        "vendorId": "A00165603",
        "brandId": 0,
        "filter": "{}",
        "customAdditionalParams": "{}",
        "enableAdultItemDisplay": True,
        "nextPageKey": 0,
        "selectedVendorItemIds": [],
        "cartSelectedVendorItemIds": [],
    }
    j = fetch_json(
        "https://shop.coupang.com/api/v1/listing",
        method="POST",
        headers={"Referer": SOURCES["coupang"]["human_url"], "Content-Type": "application/json", "Accept": "application/json, text/plain, */*"},
        json_body=body,
    )
    products = j.get("data", {}).get("products", [])
    if limit:
        products = products[:limit]
    deals = []
    for p in products:
        title_area = p.get("imageAndTitleArea") or {}
        price_area = p.get("priceArea") or {}
        badges = ["타임딜"]
        if p.get("rocketArea", {}).get("show") or p.get("rocketMerchant"):
            badges.append("로켓")
        if p.get("cashBackArea", {}).get("cashRewardText"):
            badges.append("적립")
        if p.get("btcInfo", {}).get("deliveryFee") == 0:
            badges.append("무료배송")
        deals.append(make_deal(
            "coupang",
            external_id=str(p.get("vendorItemId") or p.get("productId")),
            title=title_area.get("title") or title_area.get("groupTitle"),
            source_category_label="타임딜",
            source_category_path=["쿠팡", "타임딜"],
            image_url=title_area.get("completeHttpUrl") or title_area.get("defaultUrl"),
            url="https://www.coupang.com" + (p.get("link") or ""),
            price_label="타임딜가",
            deal_price=price_area.get("price") or price_area.get("salesPrice"),
            original_price=price_area.get("originalPrice") or price_area.get("basePrice"),
            discount_rate=price_area.get("discountRate"),
            starts_at=p.get("salesStartDate"),
            ends_at=price_area.get("instantDiscountExpiryDateUTCV2"),
            badges=badges,
            status="품절" if p.get("soldoutArea", {}).get("soldout") else "판매중",
            raw={"rating": (p.get("reviewArea") or {}).get("ratingAverage"), "reviews": (p.get("reviewArea") or {}).get("ratingCount")},
        ))
    return deals


def collect_naver(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    # A currently visible Naver promotion page found during reconnaissance.
    url = "https://shopping.naver.com/festa/onsale/living/6a0d47f983b92b14479746b4?tr=prosol"
    r = requests.get(url, headers=BASE_HEADERS, timeout=25)
    r.raise_for_status()
    text = r.text
    matches = re.findall(
        r'\\"productId\\":\\"(\d+)\\".*?\\"name\\":\\"(.*?)\\".*?\\"imageUrl\\":\\"(.*?)\\".*?\\"landingUrl\\":\\"(.*?)\\".*?\\"isSoldOut\\":(true|false).*?\\"salePrice\\":(\d+).*?\\"discountedPrice\\":(\d+).*?\\"discountedRatio\\":(\d+)',
        text,
    )
    seen = set()
    deals = []
    for pid, name, image, landing, sold_out, sale_price, discounted_price, ratio in matches:
        if pid in seen:
            continue
        seen.add(pid)
        deals.append(make_deal(
            "naver",
            external_id=pid,
            title=fix_mojibake(name),
            source_category_label="생필품 특가",
            source_category_path=["프로모션", "넾다세일", "생필품 특가"],
            image_url=image.replace("\\/", "/"),
            url=landing.replace("\\/", "/"),
            price_label="프로모션가",
            deal_price=discounted_price,
            original_price=sale_price,
            discount_rate=ratio,
            starts_at=None,
            ends_at=None,
            badges=["프로모션", "N+ 스토어"],
            status="품절" if sold_out == "true" else "판매중",
        ))
        if limit and len(deals) >= limit:
            break
    return deals


def collect_ssg(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    url = SOURCES["ssg"]["human_url"]
    r = requests.get(url, headers=BASE_HEADERS, timeout=25)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    if not m:
        return []
    data = json.loads(unescape(m.group(1)))
    items: List[Dict[str, Any]] = []

    def walk(x: Any) -> None:
        if limit and len(items) >= limit:
            return
        if isinstance(x, dict):
            if x.get("itemId") and (x.get("itemName") or x.get("itemNm")):
                items.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(data)
    deals = []
    seen = set()
    for p in items:
        item_id = str(p.get("itemId"))
        if item_id in seen:
            continue
        seen.add(item_id)
        if limit and len(deals) >= limit:
            break
        price_info = p.get("priceInfo") or {}
        deals.append(make_deal(
            "ssg",
            external_id=str(p.get("itemId")),
            title=p.get("itemName") or p.get("itemNm"),
            source_category_label=p.get("festaName") or "쓱특가",
            source_category_path=["SSG", p.get("festaName") or "쓱특가"],
            image_url=p.get("itemImgUrl") or (p.get("reactingDetail", {}).get("mkt_info", {}) or {}).get("item_img_url"),
            url=p.get("itemUrl") or p.get("itemDetailLink"),
            price_label="쓱특가",
            deal_price=p.get("finalPrice") or price_info.get("primaryPrice"),
            original_price=p.get("strikeOutPrice") or price_info.get("strikeOutPrice"),
            discount_rate=p.get("discountRate"),
            badges=[p.get("festaName") or "쓱특가", p.get("brandName") or p.get("brandNm") or ""],
            status="품절" if p.get("soldOutMessage") else "판매중",
        ))
    return deals


def collect_lotteon(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    headers = {"Origin": "https://www.lotteon.com", "Referer": SOURCES["lotteon"]["human_url"], "Accept": "application/json"}
    shop = fetch_json(
        "https://pbf.lotteon.com/display/v2/dpShop/seltMainShop",
        headers=headers,
        params={"dshopNo": "60938", "mdiaCd": "PC"},
    )
    async_urls = []
    for mod in (shop.get("data") or {}).get("dpShopMdulList", []):
        if mod.get("asyncUrl"):
            async_urls.append(mod["asyncUrl"])
    if not async_urls:
        return []
    items: List[Dict[str, Any]] = []

    for async_url in async_urls:
        j = fetch_json(async_url, headers=headers)

        def walk(x: Any) -> None:
            if limit and len(items) >= limit:
                return
            if isinstance(x, dict):
                if x.get("spdNo") and x.get("spdNm"):
                    items.append(x)
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)

        walk(j)
        if limit and len(items) >= limit:
            break

    deals = []
    seen = set()
    for p in items:
        spd_no = p.get("spdNo")
        if spd_no in seen:
            continue
        seen.add(spd_no)
        if limit and len(deals) >= limit:
            break
        img = p.get("repImgPathNm") or p.get("imgPathNm") or ""
        if img and img.startswith("/"):
            img = "https://contents.lotteon.com" + img
        deals.append(make_deal(
            "lotteon",
            external_id=str(spd_no),
            title=p.get("spdNm"),
            source_category_label="쇼핑특가",
            source_category_path=["롯데ON", "쇼핑특가"],
            image_url=p.get("imgFullUrl") or p.get("wdthImgUrl") or img,
            url=f"https://www.lotteon.com/p/product/{spd_no}" if spd_no else SOURCES["lotteon"]["human_url"],
            price_label="쇼핑특가",
            deal_price=p.get("finalDscPrc") or p.get("slPrc"),
            original_price=p.get("slPrc"),
            discount_rate=p.get("dscRt") or p.get("onerDcRt"),
            starts_at=iso_from_yyyymmddhhmmss(p.get("slStrtDttm")),
            ends_at=iso_from_yyyymmddhhmmss(p.get("timerEndDttm") or p.get("slEndDttm")),
            badges=["쇼핑특가"],
            status="품절" if p.get("slStatCd") == "SOUT" else "판매중",
        ))
    return deals


COLLECTORS = {
    "kakao": collect_kakao,
    "11st": collect_11st,
    "coupang": collect_coupang,
    "naver": collect_naver,
    "ssg": collect_ssg,
    "lotteon": collect_lotteon,
}


def source_summary(deals: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_source = defaultdict(list)
    for d in deals:
        by_source[d["source_id"]].append(d)
    summary = {}
    for sid, source in SOURCES.items():
        rows = by_source.get(sid, [])
        cats = Counter(d["canonical_category"]["label"] for d in rows)
        discounts = [d["discount_rate"] for d in rows if isinstance(d.get("discount_rate"), int)]
        summary[sid] = {
            "source_id": sid,
            "name": source["name"],
            "deal_type": source["deal_type"],
            "accent": source["accent"],
            "count": len(rows),
            "top_categories": cats.most_common(3),
            "avg_discount": round(sum(discounts) / len(discounts)) if discounts else None,
            "last_checked_at": max((d["checked_at"] for d in rows), default=None),
        }
    return summary


def main() -> None:
    all_deals: List[Dict[str, Any]] = []
    manifest: Dict[str, Any] = {}
    for sid, collector in COLLECTORS.items():
        try:
            rows = collector()
            all_deals.extend(rows)
            manifest[sid] = {"status": "ok", "count": len(rows), "error": None}
        except Exception as exc:
            manifest[sid] = {"status": "error", "count": 0, "error": repr(exc)}
    payload = {
        "generated_at": NOW.isoformat(),
        "contract": "browser loads this generated JSON; scheduled runner refreshes it hourly and preserves the previous successful file on unsafe output",
        "canonical_categories": CANONICAL,
        "sources": SOURCES,
        "manifest": manifest,
        "source_summary": source_summary(all_deals),
        "deals": all_deals,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    PUBLIC_OUT.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} and {PUBLIC_OUT} with {len(all_deals)} deals")
    for sid, m in manifest.items():
        print(f"{sid}: {m['status']} count={m['count']} error={m['error'] or ''}")


if __name__ == "__main__":
    main()
