"""국토교통부 아파트 실거래가(매매/전세) -> data/<구코드>.json + data/index.json
GitHub Actions에서 매일 자동 실행됩니다. 로컬 실행: export DATA_GO_KR_KEY="인증키(Decoding)" 후 python fetch_apt_data.py
"""
import os, re, json, time, datetime, urllib.parse, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from collections import defaultdict

KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()
if not KEY:
    raise SystemExit("DATA_GO_KR_KEY 값이 비어 있어요. GitHub Secrets 이름과 값을 확인하세요.")
BASE = "https://apis.data.go.kr/1613000/"
API = {
    "sale": "RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
    "rent": "RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
}
GU = {  # 서울 25개 구 (시군구 코드)
    "11110": "종로구", "11140": "중구", "11170": "용산구", "11200": "성동구", "11215": "광진구",
    "11230": "동대문구", "11260": "중랑구", "11290": "성북구", "11305": "강북구", "11320": "도봉구",
    "11350": "노원구", "11380": "은평구", "11410": "서대문구", "11440": "마포구", "11470": "양천구",
    "11500": "강서구", "11530": "구로구", "11545": "금천구", "11560": "영등포구", "11590": "동작구",
    "11620": "관악구", "11650": "서초구", "11680": "강남구", "11710": "송파구", "11740": "강동구",
}
START_YEAR = 2020   # 시작 연도. 나중에 줄이면(예: 2016) 빠진 달만 추가로 받아요
MIN_TRADES = 12     # 이 건수보다 적게 거래된 (단지, 면적, 유형)은 제외
HH_LIST = "AptListService3/getSigunguAptList3"        # 공동주택 단지 목록 (단지코드)
HH_INFO = "AptBasisInfoServiceV4/getAphusBassInfoV4"  # 공동주택 기본정보 (세대수)
HH = {"ok": True, "any": False}
CACHE = "rowcache"


class Quota(Exception):
    pass


def bucket(area):  # 전용면적을 정수(㎡)로 묶음: 84.88 -> "84", 59.97 -> "59"
    a = float(area or 0)
    return str(int(a)) if a >= 20 else None


def parse(kind, r):  # 필요한 항목만 뽑아 작게 저장
    b = bucket(r.get("excluUseAr"))
    if not b or r.get("cdealDay"):  # 면적 구간 밖이거나 해제(취소)된 거래 제외
        return None
    if kind == "sale":
        price = r.get("dealAmount", "")
    elif r.get("monthlyRent", "0") in ("0", ""):  # 전세만
        price = r.get("deposit", "")
    else:
        return None
    try:
        return [r["aptNm"], r.get("umdNm", ""), r.get("buildYear", ""), b, int(price.replace(",", ""))]
    except (KeyError, ValueError):
        return None


def fetch(kind, gu, ym):
    path = f"{CACHE}/{kind}_{gu}_{ym}.json"
    t = datetime.date.today()
    recent = (t.year * 12 + t.month) - (int(ym[:4]) * 12 + int(ym[4:])) <= 2  # 최근 3개월은 늦은 신고가 있어 매번 새로 받음
    if os.path.exists(path) and not recent:
        cached = json.load(open(path, encoding="utf-8"))
        if isinstance(cached, dict) and cached.get("v") == 2:  # 예전 형식(59/84만 저장)은 다시 받음
            return cached["rows"]
    rows, page = [], 1
    while True:
        q = urllib.parse.urlencode({"serviceKey": KEY, "LAWD_CD": gu, "DEAL_YMD": ym,
                                    "pageNo": page, "numOfRows": 1000})
        req = urllib.request.Request(f"{BASE}{API[kind]}?{q}", headers={"User-Agent": "Mozilla/5.0"})
        for attempt in range(4):
            try:
                root = ET.fromstring(urllib.request.urlopen(req, timeout=30).read())
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    raise Quota()
                raise SystemExit(f"[{kind} {gu} {ym}] HTTP {e.code} {e.reason}: {e.read()[:300]!r}")
            except (urllib.error.URLError, TimeoutError, ET.ParseError):
                if attempt == 3:
                    raise
                time.sleep(3)
                continue
            err = (root.findtext(".//errMsg") or "") + (root.findtext(".//returnAuthMsg") or "")
            if "PER_SECOND" in err and attempt < 3:
                time.sleep(3)
                continue
            if "EXCEEDS" in err:
                raise Quota()
            if err:
                raise SystemExit(f"[{kind} {gu} {ym}] API 오류: {err}")
            break
        for it in root.findall(".//item"):
            row = parse(kind, {c.tag: (c.text or "").strip() for c in it})
            if row:
                rows.append(row)
        if page * 1000 >= int(root.findtext(".//totalCount") or 0):
            break
        page += 1
    os.makedirs(CACHE, exist_ok=True)
    json.dump({"v": 2, "rows": rows}, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    time.sleep(0.15)
    return rows


def kapt_call(op, **p):
    q = urllib.parse.urlencode({"serviceKey": KEY, **p})
    req = urllib.request.Request(f"{BASE}{op}?{q}", headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(4):
        try:
            root = ET.fromstring(urllib.request.urlopen(req, timeout=30).read())
        except (urllib.error.URLError, TimeoutError, ET.ParseError):
            if attempt == 3:
                raise
            time.sleep(3)
            continue
        err = (root.findtext(".//errMsg") or "") + (root.findtext(".//returnAuthMsg") or "")
        if "PER_SECOND" in err and attempt < 3:
            time.sleep(3)
            continue
        if "EXCEEDS" in err:
            raise Quota()
        if err:
            raise RuntimeError(err)
        return root


def households(gu):  # K-apt 단지별 세대수. 구마다 한 번만 받아 저장해 둠
    path = f"{CACHE}/hh_{gu}.json"
    if os.path.exists(path):
        cached = json.load(open(path, encoding="utf-8"))
        if isinstance(cached, dict) and cached.get("v") == 2:  # 예전 형식(구간별 세대수 없음)은 다시 받음
            return cached["rows"]
    items, page = [], 1
    while True:
        root = kapt_call(HH_LIST, sigunguCode=gu, pageNo=page, numOfRows=1000)
        items += [{c.tag: (c.text or "").strip() for c in it} for it in root.findall(".//item")]
        if page * 1000 >= int(root.findtext(".//totalCount") or 0):
            break
        page += 1
    def num(info, *tags):  # 항목 이름이 조금 달라도 읽도록 여러 이름을 시도
        for t in tags:
            v = info.findtext(f".//{t}")
            if v and v.strip():
                return int(float(v))
        return 0
    out = []
    for it in items:
        info = kapt_call(HH_INFO, kaptCode=it.get("kaptCode", ""))
        out.append([it.get("kaptName", ""), it.get("as3", ""), num(info, "kaptdaCnt"),
                    [num(info, "kaptMparea_60", "kaptMparea60"),      # 60㎡ 이하 세대수
                     num(info, "kaptMparea_85", "kaptMparea85"),      # 60~85㎡ 세대수
                     num(info, "kaptMparea_135", "kaptMparea135"),    # 85~135㎡ 세대수
                     num(info, "kaptdaSize_136", "kaptMparea_136", "kaptMparea136")]])  # 135㎡ 초과
        time.sleep(0.1)
    os.makedirs(CACHE, exist_ok=True)
    json.dump({"v": 2, "rows": out}, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    return out


def norm(s):
    return re.sub(r"\(.*?\)|[\s·\-_.,]|아파트", "", s)


def match(cs, hh):  # 실거래 단지명과 K-apt 단지명을 맞춰 세대수(총 세대수, 면적 구간별 세대수)를 채움
    by_dn = {(norm(n), d): (h, tuple(hb)) for n, d, h, hb in hh}
    by_n = defaultdict(set)
    for n, d, h, hb in hh:
        by_n[norm(n)].add((h, tuple(hb)))
    for c in cs:
        nm, dg = norm(c["_nm"]), c["_dg"]
        v = by_dn.get((nm, dg))
        if v is None and len(by_n.get(nm, ())) == 1:
            v = next(iter(by_n[nm]))
        if v is None:  # 이름이 서로 포함되는 경우 (같은 동 또는 동 정보 없음), 후보가 하나일 때만
            cand = {vv for (n, d), vv in by_dn.items()
                    if min(len(nm), len(n)) >= 3 and (nm in n or n in nm) and (d == dg or not d)}
            v = next(iter(cand)) if len(cand) == 1 else None
        c["h"] = v[0] if v else 0
        if v and any(v[1]):
            c["hb"] = list(v[1])


def build(gname, rows_by_idx, M):
    acc = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))  # (단지,동) -> (유형,면적) -> 월인덱스 -> [가격]
    year = {}
    for idx, kind, row in rows_by_idx:
        name, dong, by, b, price = row
        acc[(name, dong)][(kind, b)][idx].append(price / 10000)
        year.setdefault((name, dong), int(by) if by.isdigit() else 0)
    names = defaultdict(int)
    for name, dong in acc:
        names[name] += 1
    out = []
    for (name, dong), series in acc.items():
        series = {kb: d for kb, d in series.items() if sum(len(v) for v in d.values()) >= MIN_TRADES}
        if not series:
            continue
        c = {"n": name if names[name] == 1 else f"{name}({dong})", "y": year[(name, dong)],
             "h": 0, "_nm": name, "_dg": dong,
             "sale": {}, "rent": {}, "cnt": {"sale": {}, "rent": {}}}
        for (kind, b), d in series.items():
            c[kind][b] = [round(sum(d[i]) / len(d[i]), 2) if i in d else None for i in range(M)]
            c["cnt"][kind][b] = [len(d[i]) if i in d else 0 for i in range(M)]
        out.append(c)
    return out


def main():
    now = datetime.date.today()
    months = [(y, m) for y in range(START_YEAR, now.year + 1) for m in range(1, 13) if (y, m) <= (now.year, now.month)]
    M = len(months)
    os.makedirs("data", exist_ok=True)
    result = {}
    try:
        for gu, gname in GU.items():
            rows = []
            for idx, (y, m) in enumerate(months):
                for kind in API:
                    rows += [(idx, kind, r) for r in fetch(kind, gu, f"{y}{m:02d}")]
            result[gu] = build(gname, rows, M)
            if HH["ok"]:
                try:
                    match(result[gu], households(gu))
                    HH["any"] = True
                except Exception as e:  # 세대수를 못 가져와도 나머지 데이터는 정상 저장
                    HH["ok"] = False
                    print("세대수 정보를 가져오지 못했어요:", repr(e)[:200], flush=True)
            print("완료:", gname, len(result[gu]), "개 단지", flush=True)
    except Quota:
        print("일일 호출 한도에 도달했어요. 받은 데이터는 저장해 뒀고, 다음 실행 때 이어서 받아요.")
        return
    for gu, cs in result.items():
        cs = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cs]
        json.dump({"gu": GU[gu], "complexes": cs}, open(f"data/{gu}.json", "w", encoding="utf-8"),
                  ensure_ascii=False, separators=(",", ":"))
    json.dump({"updated": now.strftime("%Y.%m"), "start": f"{START_YEAR}-01", "households": HH["ok"] and HH["any"],
               "gus": [{"code": g, "name": n} for g, n in GU.items()]},
              open("data/index.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("data/ 저장 완료")


if __name__ == "__main__":
    main()
