"""국토교통부 아파트 실거래가(매매/전월세) -> apt-data.json
사용법:
  1) 공공데이터포털에서 아래 두 API를 활용신청하고 인증키(Decoding)를 받습니다.
     - 국토교통부_아파트 매매 실거래가 상세 자료
     - 국토교통부_아파트 전월세 실거래가 자료
  2) export DATA_GO_KR_KEY="발급받은키"   (Windows: set DATA_GO_KR_KEY=...)
  3) python fetch_apt_data.py
  4) 만들어진 apt-data.json을 웹 페이지의 '실거래가 데이터' 칸에서 선택합니다.
"""
import os, json, time, datetime, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

KEY = os.environ["DATA_GO_KR_KEY"]
BASE = "http://apis.data.go.kr/1613000/"
API = {
    "sale": "RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
    "rent": "RTMSDataSvcAptRent/getRTMSDataSvcAptRent",  # 포털 상세 페이지에서 주소 확인
}
GU = {"11140": "중구", "11200": "성동구"}  # 시군구 코드 5자리: 원하는 구를 추가
START_YEAR = 2016       # 시작 연도 (넓힐수록 호출이 많아져요. 결과는 cache/ 에 저장돼 이어서 실행 가능)
MIN_TRADES = 5          # 이 건수보다 적은 단지는 제외


def fetch(kind, gu, ym):
    path = f"cache/{kind}_{gu}_{ym}.json"
    t = datetime.date.today()
    recent = (t.year * 12 + t.month) - (int(ym[:4]) * 12 + int(ym[4:])) <= 2  # 최근 3개월은 늦게 신고된 거래가 있어 매번 새로 받음
    if os.path.exists(path) and not recent:
        return json.load(open(path, encoding="utf-8"))
    rows, page = [], 1
    while True:
        q = urllib.parse.urlencode({"serviceKey": KEY, "LAWD_CD": gu, "DEAL_YMD": ym,
                                    "pageNo": page, "numOfRows": 1000})
        root = ET.fromstring(urllib.request.urlopen(f"{BASE}{API[kind]}?{q}", timeout=30).read())
        err = root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg")
        if err:
            raise RuntimeError(f"API 오류: {err}")
        for it in root.findall(".//item"):
            rows.append({c.tag: (c.text or "").strip() for c in it})
        if page * 1000 >= int(root.findtext(".//totalCount") or 0):
            break
        page += 1
    os.makedirs("cache", exist_ok=True)
    json.dump(rows, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    time.sleep(0.2)
    return rows


def bucket(area):
    a = float(area or 0)
    return "59" if 55 <= a < 63 else "84" if 80 <= a < 88 else None


def won(s):  # "12,500" (만원) -> 억
    return int(s.replace(",", "")) / 10000


def main():
    now = datetime.date.today()
    agg = defaultdict(list)   # (구, 단지, 유형, 면적, 연-월) -> [가격(억)]
    built = {}
    for gu, gname in GU.items():
        for y in range(START_YEAR, now.year + 1):
            for m in range(1, 13):
                if (y, m) > (now.year, now.month):
                    break
                for kind in API:
                    for r in fetch(kind, gu, f"{y}{m:02d}"):
                        b = bucket(r.get("excluUseAr"))
                        if not b:
                            continue
                        if kind == "sale":
                            price = won(r["dealAmount"])
                        elif r.get("monthlyRent", "0") in ("0", ""):  # 전세만
                            price = won(r["deposit"])
                        else:
                            continue
                        name = r["aptNm"]
                        built.setdefault((gname, name), r.get("buildYear"))
                        agg[(gname, name, kind, b, f"{y}-{m:02d}")].append(price)
        print("완료:", gname)

    cx = {}
    for (gname, name, kind, b, ym), v in agg.items():
        c = cx.setdefault((gname, name), {"n": name, "gu": gname,
                          "y": int(built[(gname, name)] or 0) or None,
                          "sale": {}, "rent": {}, "_n": 0})
        c[kind].setdefault(b, {})[ym] = round(sum(v) / len(v), 2)
        c.setdefault("cnt", {"sale": {}, "rent": {}})[kind].setdefault(b, {})[ym] = len(v)  # 거래량
        c["_n"] += len(v)
    out = [{k: v for k, v in c.items() if k != "_n"} for c in cx.values() if c["_n"] >= MIN_TRADES]
    json.dump({"updated": now.strftime("%Y.%m"), "complexes": out},
              open("apt-data.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(f"apt-data.json 저장: 단지 {len(out)}개")


if __name__ == "__main__":
    main()
