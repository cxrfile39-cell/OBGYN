#!/usr/bin/env python3
"""
每週從 PubMed 抓取婦產科各次專科最新文獻（Journal article / Review / Guideline），
輸出到 docs/data.json。只用 Python 標準函式庫，不需安裝套件。

環境變數（可選）：
  NCBI_API_KEY  NCBI API key（有的話速率上限較高）
  NCBI_EMAIL    聯絡 email（NCBI 建議提供）
  RETMAX        每個次專科每週最多幾篇，預設 150
  BACKFILL_WEEKS  每次執行時，檢查並補建最近幾週的存檔，預設 4

每一週的資料範圍固定為「上週一 ～ 上週日」（台北時間，依 PubMed 收錄日期）。
不論在一週中的哪一天、執行幾次，同一週的結果都相同，各週之間不會重疊。
  OPENALEX_API_KEY  OpenAlex API key（免費；用來取得期刊 2 年平均被引次數，作為 IF 近似值）
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data.json"
ARCHIVE = ROOT / "docs" / "archive"
KEEP_WEEKS = int(os.environ.get("KEEP_WEEKS", "104"))   # 保留最近幾週（預設 2 年）
TPE = timezone(timedelta(hours=8))

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
OPENALEX = "https://api.openalex.org/sources"
OPENALEX_KEY = os.environ.get("OPENALEX_API_KEY", "").strip()
JOURNAL_CACHE = ROOT / "docs" / "journals.json"          # 期刊指標快取（OpenAlex 失敗時沿用）
JIF_OVERRIDE = ROOT / "scripts" / "jif_override.json"   # 手動填入的官方 JCR JIF（優先使用）
ZH_FILE = ROOT / "docs" / "zh.json"                     # Claude 撰寫的一句中文重點（指引、統合分析）{pmid: 文字}
ZH_QUEUE = ROOT / "data" / "zh_queue.json"              # 待撰寫清單（給每週 Claude 排程任務使用）
ZH_QUEUE_MAX = 800
API_KEY = os.environ.get("NCBI_API_KEY", "").strip()
EMAIL = os.environ.get("NCBI_EMAIL", "").strip()
DAYS = 7
RETMAX = int(os.environ.get("RETMAX", "150"))
BACKFILL_WEEKS = int(os.environ.get("BACKFILL_WEEKS", "4"))
SCHEME = 2   # 存檔格式版本：2 = 固定「週一～週日」範圍
RANGES = [("30d", "30 天", 30), ("1y", "1 年", 365), ("2y", "2 年", 730)]
RANGE_DIR = ROOT / "docs" / "range"
RANGE_N = int(os.environ.get("RANGE_N", "40"))   # 長區間：每個「次專科 × 文章類型」區塊最多取幾篇（最新）

# 四類文章的 PubMed 語法（彼此互斥），與網頁上的分類規則一致
_GL = ('(guideline[pt] OR practice guideline[pt] OR consensus development conference[pt]'
       ' OR guideline*[ti] OR consensus[ti] OR "committee opinion"[ti] OR "practice bulletin"[ti]'
       ' OR "position statement"[ti] OR "position paper"[ti] OR green-top[ti])')
_MA = ('(meta-analysis[pt] OR systematic review[pt] OR meta-analys*[ti] OR metaanalys*[ti]'
       ' OR "systematic review"[ti])')
_RV = '(review[pt] OR scoping review[pt])'
TYPE_CLAUSES = {
    "original": f" NOT {_GL} NOT {_MA} NOT {_RV}",
    "review": f" AND {_RV} NOT {_GL} NOT {_MA}",
    "meta": f" AND {_MA} NOT {_GL}",
    "guideline": f" AND {_GL}",
}
SLEEP = 0.12 if API_KEY else 0.4  # NCBI 速率限制：無 key 3 次/秒，有 key 10 次/秒

# 共用篩選：英文、文章類型、排除評論/社論/勘誤/個案報告
TYPE_FILTER = (
    ' AND english[la]'
    ' AND (journal article[pt] OR review[pt] OR systematic review[pt] OR meta-analysis[pt]'
    ' OR guideline[pt] OR practice guideline[pt] OR randomized controlled trial[pt])'
    ' AND (hasabstract OR guideline[pt] OR practice guideline[pt])'
    ' NOT (comment[pt] OR editorial[pt] OR letter[pt] OR erratum[pt] OR news[pt]'
    ' OR case reports[pt] OR retracted publication[pt] OR preprint[pt])'
)

OBGYN_JOURNALS = (
    '"Obstet Gynecol"[ta] OR "Am J Obstet Gynecol"[ta] OR "BJOG"[ta] OR "Int J Gynaecol Obstet"[ta]'
    ' OR "Acta Obstet Gynecol Scand"[ta] OR "Eur J Obstet Gynecol Reprod Biol"[ta]'
    ' OR "J Obstet Gynaecol Can"[ta] OR "Arch Gynecol Obstet"[ta] OR "Taiwan J Obstet Gynecol"[ta]'
    ' OR "J Obstet Gynaecol Res"[ta] OR "Aust N Z J Obstet Gynaecol"[ta] OR "Obstet Gynecol Surv"[ta]'
)

CATEGORIES = [
    {"id": "general", "en": "General Obstetrics and Gynecology", "zh": "一般婦產科",
     "q": f"({OBGYN_JOURNALS})"},
    {"id": "mfm", "en": "Maternal-Fetal Medicine", "zh": "母胎醫學",
     "q": '("Pregnancy Complications"[MeSH] OR "Fetal Diseases"[MeSH] OR "Prenatal Diagnosis"[MeSH]'
          ' OR "Am J Obstet Gynecol MFM"[ta] OR "Ultrasound Obstet Gynecol"[ta] OR "Fetal Diagn Ther"[ta]'
          ' OR preeclampsia[tiab] OR pre-eclampsia[tiab] OR "fetal growth restriction"[tiab]'
          ' OR "preterm birth"[tiab] OR "fetal medicine"[tiab] OR "maternal-fetal"[tiab])'},
    {"id": "rei", "en": "Reproductive Endocrinology and Infertility", "zh": "生殖內分泌與不孕症",
     "q": '("Infertility"[MeSH] OR "Reproductive Techniques, Assisted"[MeSH] OR "Polycystic Ovary Syndrome"[MeSH]'
          ' OR "Hum Reprod"[ta] OR "Fertil Steril"[ta] OR "Reprod Biomed Online"[ta] OR "Hum Reprod Update"[ta]'
          ' OR infertil*[tiab] OR "in vitro fertilization"[tiab] OR IVF[tiab] OR "embryo transfer"[tiab]'
          ' OR "ovarian stimulation"[tiab] OR "polycystic ovary"[tiab] OR "ovarian reserve"[tiab])'},
    {"id": "gynonc", "en": "Gynecologic Oncology", "zh": "婦科腫瘤",
     "q": '("Genital Neoplasms, Female"[MeSH] OR "Gynecol Oncol"[ta] OR "Int J Gynecol Cancer"[ta]'
          ' OR "J Gynecol Oncol"[ta] OR "ovarian cancer"[tiab] OR "endometrial cancer"[tiab]'
          ' OR "cervical cancer"[tiab] OR "vulvar cancer"[tiab] OR "uterine sarcoma"[tiab]'
          ' OR "gestational trophoblastic"[tiab] OR "ovarian carcinoma"[tiab])'},
    {"id": "urogyn", "en": "Urogynecology and Pelvic Reconstructive Surgery", "zh": "泌尿婦科與骨盆重建",
     "q": '("Pelvic Organ Prolapse"[MeSH] OR "Urinary Incontinence"[MeSH] OR "Urinary Bladder, Overactive"[MeSH]'
          ' OR "Int Urogynecol J"[ta] OR "Urogynecology (Phila)"[ta] OR "Neurourol Urodyn"[ta]'
          ' OR "pelvic organ prolapse"[tiab] OR "urinary incontinence"[tiab] OR "overactive bladder"[tiab]'
          ' OR urogynecolog*[tiab] OR "pelvic floor muscle training"[tiab])'},
    {"id": "migs", "en": "Minimally Invasive Gynecologic Surgery", "zh": "微創婦科手術",
     "q": '("J Minim Invasive Gynecol"[ta] OR ((laparoscop*[tiab] OR robotic*[tiab] OR "minimally invasive"[tiab]'
          ' OR vNOTES[tiab] OR "natural orifice"[tiab]) AND (hysterectomy[tiab] OR myomectomy[tiab]'
          ' OR gynecolog*[tiab] OR gynaecolog*[tiab] OR endometriosis[tiab] OR adnex*[tiab] OR "ovarian cyst"[tiab])))'},
    {"id": "endoscopy", "en": "Gynecologic Endoscopy", "zh": "婦科內視鏡",
     "q": '("Hysteroscopy"[MeSH] OR "Colposcopy"[MeSH] OR hysteroscop*[tiab] OR colposcop*[tiab]'
          ' OR "Facts Views Vis Obgyn"[ta] OR "Gynecol Minim Invasive Ther"[ta]'
          ' OR ("Laparoscopy"[MeSH] AND ("Genital Diseases, Female"[MeSH] OR gynecolog*[tiab])))'},
    {"id": "fp", "en": "Family Planning", "zh": "家庭計畫（避孕與人工流產）",
     "q": '("Contraception"[MeSH] OR "Contraceptive Agents"[MeSH] OR "Abortion, Induced"[MeSH]'
          ' OR "Family Planning Services"[MeSH] OR "Contraception"[ta] OR contracepti*[tiab]'
          ' OR "intrauterine device"[tiab] OR "medication abortion"[tiab] OR "induced abortion"[tiab]'
          ' OR "family planning"[tiab])'},
    {"id": "menopause", "en": "Menopause Medicine", "zh": "更年期醫學",
     "q": '("Menopause"[MeSH] OR "Postmenopause"[MeSH] OR "Hormone Replacement Therapy"[MeSH]'
          ' OR "Menopause"[ta] OR "Climacteric"[ta] OR "Maturitas"[ta] OR menopaus*[tiab]'
          ' OR perimenopaus*[tiab] OR "vasomotor symptoms"[tiab] OR "genitourinary syndrome"[tiab]'
          ' OR "hot flashes"[tiab] OR "menopausal hormone therapy"[tiab])'},
    {"id": "pag", "en": "Pediatric and Adolescent Gynecology", "zh": "兒童及青少年婦科",
     "q": '("J Pediatr Adolesc Gynecol"[ta] OR "pediatric and adolescent gynecology"[tiab]'
          ' OR (("Adolescent"[MeSH] OR "Child"[MeSH] OR adolescen*[ti] OR pediatric[ti] OR paediatric[ti]'
          ' OR girls[ti]) AND ("Genital Diseases, Female"[MeSH] OR "Menstruation Disturbances"[MeSH]'
          ' OR "Puberty, Precocious"[MeSH] OR menarche[tiab] OR dysmenorrh*[tiab] OR "heavy menstrual"[tiab]'
          ' OR "ovarian torsion"[tiab] OR "Mullerian anomal*"[tiab] OR "müllerian anomal*"[tiab])))'},
    {"id": "obmed", "en": "Obstetric Medicine", "zh": "產科內科學",
     "q": '("Obstet Med"[ta] OR "Diabetes, Gestational"[MeSH] OR "Pregnancy in Diabetics"[MeSH]'
          ' OR "Pregnancy Complications, Cardiovascular"[MeSH] OR "Pregnancy Complications, Hematologic"[MeSH]'
          ' OR "Pregnancy Complications, Infectious"[MeSH] OR "gestational diabetes"[tiab]'
          ' OR ((pregnan*[ti] OR peripartum[ti] OR postpartum[ti]) AND (lupus[tiab] OR thyroid*[tiab]'
          ' OR epilep*[tiab] OR "kidney disease"[tiab] OR cardiomyopathy[tiab] OR "heart disease"[tiab]'
          ' OR thrombo*[tiab] OR asthma[tiab] OR "inflammatory bowel"[tiab] OR "chronic hypertension"[tiab])))'},
    {"id": "fpmrs", "en": "Female Pelvic Medicine and Reconstructive Surgery", "zh": "女性骨盆醫學與重建手術",
     "q": '("Pelvic Floor Disorders"[MeSH] OR "Fecal Incontinence"[MeSH] OR "Vesicovaginal Fistula"[MeSH]'
          ' OR "Suburethral Slings"[MeSH] OR sacrocolpopexy[tiab] OR "midurethral sling"[tiab]'
          ' OR "mid-urethral sling"[tiab] OR "transvaginal mesh"[tiab] OR "obstetric anal sphincter"[tiab]'
          ' OR OASIS[tiab] OR "pelvic floor dysfunction"[tiab] OR colpocleisis[tiab]'
          ' OR "female pelvic medicine"[tiab])'},
    {"id": "genetics", "en": "Reproductive Genetics", "zh": "生殖遺傳學",
     "q": '("Preimplantation Diagnosis"[MeSH] OR "Noninvasive Prenatal Testing"[MeSH]'
          ' OR ("Genetic Testing"[MeSH] AND ("Pregnancy"[MeSH] OR "Infertility"[MeSH]))'
          ' OR "Prenat Diagn"[ta] OR "preimplantation genetic"[tiab] OR PGT-A[tiab] OR PGT-M[tiab]'
          ' OR "cell-free DNA"[tiab] OR NIPT[tiab] OR "carrier screening"[tiab]'
          ' OR (("exome sequencing"[tiab] OR "chromosomal microarray"[tiab]) AND (prenatal[tiab] OR fetal[tiab])))'},
    {"id": "hro", "en": "High-Risk Obstetrics", "zh": "高危險妊娠",
     "q": '("Pregnancy, High-Risk"[MeSH] OR "Placenta Accreta"[MeSH] OR "Postpartum Hemorrhage"[MeSH]'
          ' OR "Pregnancy, Multiple"[MeSH] OR "Obstetric Labor, Premature"[MeSH] OR "Stillbirth"[MeSH]'
          ' OR "high-risk pregnancy"[tiab] OR "placenta accreta"[tiab] OR "placenta previa"[tiab]'
          ' OR "postpartum hemorrhage"[tiab] OR "postpartum haemorrhage"[tiab] OR "twin pregnancy"[tiab]'
          ' OR "severe maternal morbidity"[tiab] OR "maternal mortality"[tiab] OR stillbirth[tiab])'},
]

# 重點期刊（MedlineTA 縮寫，小寫比對），網頁可勾選「只看重點期刊」

CORE_JOURNALS_DISPLAY = [
    "N Engl J Med", "Lancet", "JAMA", "BMJ", "Nat Med", "Ann Intern Med", "JAMA Intern Med",
    "Lancet Oncol", "J Clin Oncol", "JAMA Oncol", "Cochrane Database Syst Rev", "PLoS Med",
    "Obstet Gynecol", "Am J Obstet Gynecol", "BJOG", "Am J Obstet Gynecol MFM",
    "Ultrasound Obstet Gynecol", "Hum Reprod", "Hum Reprod Update", "Fertil Steril",
    "Reprod Biomed Online", "Gynecol Oncol", "Int J Gynecol Cancer", "J Gynecol Oncol",
    "Int Urogynecol J", "Urogynecology (Phila)", "J Minim Invasive Gynecol", "Contraception",
    "Menopause", "Climacteric", "Maturitas", "J Pediatr Adolesc Gynecol", "Obstet Med",
    "Prenat Diagn", "Genet Med", "Acta Obstet Gynecol Scand", "Int J Gynaecol Obstet",
    "Eur J Obstet Gynecol Reprod Biol", "J Obstet Gynaecol Can", "Taiwan J Obstet Gynecol",
    "Lancet Glob Health", "Lancet Diabetes Endocrinol", "Diabetes Care", "Hypertension",
    "J Clin Endocrinol Metab", "Eur Urol",
]
CORE_JOURNALS = {s.lower() for s in CORE_JOURNALS_DISPLAY}

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def http(endpoint, params, retries=4):
    params = dict(params)
    params["tool"] = "obgyn-weekly"
    if EMAIL:
        params["email"] = EMAIL
    if API_KEY:
        params["api_key"] = API_KEY
    data = urllib.parse.urlencode(params).encode()
    url = f"{EUTILS}/{endpoint}"
    last = None
    for attempt in range(retries):
        try:
            time.sleep(SLEEP)
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "obgyn-weekly/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            wait = 2 ** attempt * 2
            print(f"  ! {endpoint} 失敗（{e}），{wait}s 後重試", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"{endpoint} 多次失敗：{last}")


def search(cat, start, end, extra="", retmax=None):
    term = cat["q"] + TYPE_FILTER + extra
    raw = http("esearch.fcgi", {
        "db": "pubmed", "term": term, "retmax": RETMAX if retmax is None else retmax, "sort": "pub_date",
        "datetype": "edat", "mindate": start.strftime("%Y/%m/%d"), "maxdate": end.strftime("%Y/%m/%d"),
        "retmode": "json",
    })
    res = json.loads(raw)["esearchresult"]
    return res.get("idlist", []), int(res.get("count", 0))


def text(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_date(art, pmdata):
    """回傳 (顯示用日期字串, 排序用 YYYY-MM-DD)。"""
    def ymd(node):
        if node is None:
            return None
        y = text(node.find("Year"))
        if not y:
            md = text(node.find("MedlineDate"))
            return (md[:4], "01", "01") if md[:4].isdigit() else None
        m = text(node.find("Month")) or "1"
        m = str(MONTHS.get(m[:3].lower(), m)).zfill(2) if not m.isdigit() else m.zfill(2)
        d = (text(node.find("Day")) or "1").zfill(2)
        return (y, m, d)

    epub = ymd(art.find("ArticleDate"))
    ppub = ymd(art.find("Journal/JournalIssue/PubDate"))
    entrez = None
    if pmdata is not None:
        for d in pmdata.findall("History/PubMedPubDate"):
            if d.get("PubStatus") in ("pubmed", "entrez"):
                entrez = ymd(d)
                break
    shown = epub or ppub or entrez
    sort = entrez or epub or ppub
    fmt = lambda t: "-".join(t) if t else ""
    return fmt(shown), fmt(sort)


def classify(pubtypes, title):
    """四類：guideline（共識/指引）、meta（統合分析/系統性回顧）、review（綜論）、original（原著）。"""
    pts = {p.lower() for p in pubtypes}
    t = title.lower()
    if pts & {"guideline", "practice guideline", "consensus development conference",
              "consensus development conference, nih"} or any(
            k in t for k in ("guideline", "committee opinion", "practice bulletin", "consensus",
                             "position statement", "position paper", "green-top")):
        return "guideline"
    if pts & {"meta-analysis", "systematic review", "network meta-analysis"} or any(
            k in t for k in ("meta-analysis", "metaanalysis", "meta analysis", "systematic review")):
        return "meta"
    if pts & {"review", "scoping review", "narrative review"}:
        return "review"
    return "original"


def parse_article(pa):
    mc = pa.find("MedlineCitation")
    art = mc.find("Article")
    pmdata = pa.find("PubmedData")
    pmid = text(mc.find("PMID"))
    title = text(art.find("ArticleTitle")) or text(art.find("VernacularTitle"))

    parts = []
    for a in art.findall("Abstract/AbstractText"):
        label = a.get("Label")
        body = text(a)
        if body:
            parts.append(f"{label}: {body}" if label else body)
    abstract = "\n".join(parts)
    if len(abstract) > 2500:            # 太長時縮短前段，但保留最後一段（通常是結論）
        last = parts[-1] if len(parts) > 1 else ""
        head = "\n".join(parts[:-1]) if last else abstract
        room = max(400, 2500 - len(last))
        head = head[:room].rsplit(" ", 1)[0] + "…" if len(head) > room else head
        abstract = head + ("\n" + last if last else "")

    authors = []
    for au in art.findall("AuthorList/Author"):
        if au.find("CollectiveName") is not None:
            authors.append(text(au.find("CollectiveName")))
        else:
            ln, ini = text(au.find("LastName")), text(au.find("Initials"))
            if ln:
                authors.append(f"{ln} {ini}".strip())
    au_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")

    pubtypes = [text(p) for p in art.findall("PublicationTypeList/PublicationType")]
    issns = [text(i) for i in art.findall("Journal/ISSN") if text(i)]
    link = text(mc.find("MedlineJournalInfo/ISSNLinking"))
    if link and link not in issns:
        issns.append(link)
    ta = text(mc.find("MedlineJournalInfo/MedlineTA")) or text(art.find("Journal/ISOAbbreviation"))
    doi = ""
    for e in art.findall("ELocationID"):
        if e.get("EIdType") == "doi":
            doi = text(e)
    if not doi and pmdata is not None:
        for e in pmdata.findall("ArticleIdList/ArticleId"):
            if e.get("IdType") == "doi":
                doi = text(e)
    pmc = ""
    if pmdata is not None:
        for e in pmdata.findall("ArticleIdList/ArticleId"):
            if e.get("IdType") == "pmc":
                pmc = text(e)
    shown, sort = parse_date(art, pmdata)

    return {
        "pmid": pmid,
        "title": title,
        "journal": text(art.find("Journal/Title")),
        "ta": ta,
        "core": ta.lower() in CORE_JOURNALS,
        "date": shown,
        "sort": sort,
        "authors": au_str,
        "type": classify(pubtypes, title),
        "pubtypes": pubtypes,
        "doi": doi,
        "pmc": pmc,
        "issn": issns,
        "abstract": abstract,
        "cats": [],
    }


def get_json(url, params, retries=3):
    q = urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        try:
            time.sleep(0.2)
            req = urllib.request.Request(f"{url}?{q}", headers={"User-Agent": "obgyn-weekly/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
            last = e
            time.sleep(2 ** attempt * 2)
    raise RuntimeError(f"OpenAlex 失敗：{last}")


_QUERIED, _FRESH = set(), {}   # 本次執行已查過的 ISSN（多個區間共用，避免重複呼叫 OpenAlex）


def journal_metrics(articles):
    """以 ISSN 向 OpenAlex 查詢期刊 2 年平均被引次數（計算方式同 IF，但非 Clarivate 官方 JIF）。
    查不到或連線失敗時，沿用上次的快取。"""
    cache = {}
    if JOURNAL_CACHE.exists():
        try:
            cache = json.loads(JOURNAL_CACHE.read_text(encoding="utf-8")).get("by_issn", {})
        except Exception:
            cache = {}
    issns = sorted({i for a in articles for i in a.get("issn", [])} - _QUERIED)
    _QUERIED.update(issns)
    fresh, ok = _FRESH, 0
    for k in range(0, len(issns), 50):
        chunk = issns[k:k + 50]
        params = {"filter": "issn:" + "|".join(chunk), "per_page": 100,
                  "select": "display_name,issn_l,issn,summary_stats"}
        if OPENALEX_KEY:
            params["api_key"] = OPENALEX_KEY
        try:
            res = get_json(OPENALEX, params)
            ok += 1
        except Exception as e:
            print(f"  ! {e}", file=sys.stderr)
            continue
        for src in res.get("results", []):
            v = (src.get("summary_stats") or {}).get("2yr_mean_citedness")
            if v is None:
                continue
            rec = {"name": src.get("display_name", ""), "if": round(float(v), 1)}
            for i in (src.get("issn") or []) + [src.get("issn_l") or ""]:
                if i:
                    fresh[i] = rec
    merged = cache | fresh
    JOURNAL_CACHE.write_text(json.dumps({
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "OpenAlex summary_stats.2yr_mean_citedness",
        "by_issn": merged}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"期刊指標：OpenAlex 查詢 {ok} 批，取得 {len(fresh)} 個 ISSN（快取共 {len(merged)}）")

    overrides, jcr_year = {}, ""
    if JIF_OVERRIDE.exists():
        try:
            raw = json.loads(JIF_OVERRIDE.read_text(encoding="utf-8"))
            jcr_year = str(raw.get("_jcr_year", ""))
            overrides = {k.lower(): v for k, v in raw.items()
                         if not k.startswith("_") and isinstance(v, (int, float))}
        except Exception as e:
            print(f"  ! jif_override.json 格式錯誤：{e}", file=sys.stderr)

    for a in articles:
        jcr = overrides.get(a["ta"].lower())
        if jcr is not None:
            a["if"], a["if_src"] = round(float(jcr), 1), "jcr"
            continue
        for i in a.get("issn", []):
            if i in merged:
                a["if"], a["if_src"] = merged[i]["if"], "openalex"
                break
    return jcr_year


def fetch_details(pmids):
    out = {}
    ids = list(pmids)
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        raw = http("efetch.fcgi", {"db": "pubmed", "id": ",".join(chunk), "retmode": "xml"})
        root = ET.fromstring(raw)
        for pa in root.findall("PubmedArticle"):
            try:
                a = parse_article(pa)
                out[a["pmid"]] = a
            except Exception as e:  # 單篇解析失敗不影響整體
                print(f"  ! 解析失敗：{e}", file=sys.stderr)
    return out


def build_week(week_monday):
    """產生某一週的資料。week_monday = 該週的週一（存檔名稱），
    資料範圍 = 前一週的週一 ～ 週日（固定 7 天，各週不重疊）。"""
    start = week_monday - timedelta(days=7)
    end = week_monday - timedelta(days=1)
    print(f"\n== {week_monday}：收錄 {start} ～ {end}，每類最多 {RETMAX} 篇 ==")
    cat_ids, counts, failed = {}, {}, []
    for cat in CATEGORIES:
        try:
            ids, total = search(cat, start, end)
            cat_ids[cat["id"]] = ids
            counts[cat["id"]] = total
            print(f"- {cat['en']}: {len(ids)}（共 {total}）")
        except Exception as e:
            failed.append(cat["id"])
            print(f"- {cat['en']}: 搜尋失敗 {e}", file=sys.stderr)

    all_ids = {i for ids in cat_ids.values() for i in ids}
    if not all_ids:
        print("沒有取得任何文獻（可能是網路或 NCBI 暫時故障），此週不覆寫。", file=sys.stderr)
        return None

    details = fetch_details(all_ids)
    for cid, ids in cat_ids.items():
        for pmid in ids:
            if pmid in details:
                details[pmid]["cats"].append(cid)

    articles = sorted(details.values(), key=lambda a: (a["sort"], a["pmid"]), reverse=True)
    try:
        jcr_year = journal_metrics(articles)
    except Exception as e:  # IF 取得失敗不影響文獻更新
        jcr_year = ""
        print(f"  ! 期刊 IF 取得失敗：{e}", file=sys.stderr)
    return {
        "scheme": SCHEME,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week": week_monday.isoformat(),
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
        "window_days": DAYS,
        "categories": [{k: c[k] for k in ("id", "en", "zh", "q")}
                       | {"total": counts.get(c["id"], 0), "fetched": len(cat_ids.get(c["id"], []))}
                       for c in CATEGORIES],
        "method": {"type_filter": TYPE_FILTER.strip(), "retmax": RETMAX, "date_type": "edat",
                   "jcr_year": jcr_year,
                   "core_journals": sorted(CORE_JOURNALS_DISPLAY)},
        "failed": failed,
        "articles": articles,
    }


def build_range(key, label, days, end):
    """長區間（30 天／1 年／2 年）：每個「次專科 × 四類文章」分別查詢，
    取得真實總數與最新 RANGE_N 篇。為了讓檔案小，不含摘要（網頁點開時再向 PubMed 讀取）。"""
    start = end - timedelta(days=days - 1)
    print(f"\n== 區間 {label}：{start} ～ {end}，每區塊最多 {RANGE_N} 篇 ==")
    block_totals, found, failed = {}, {}, []
    for cat in CATEGORIES:
        block_totals[cat["id"]] = {}
        for tid, clause in TYPE_CLAUSES.items():
            try:
                ids, total = search(cat, start, end, clause, RANGE_N)
            except Exception as e:
                failed.append(f"{cat['id']}:{tid}")
                print(f"  ! {cat['id']}/{tid} 失敗 {e}", file=sys.stderr)
                continue
            block_totals[cat["id"]][tid] = total
            for pmid in ids:
                found.setdefault(pmid, {"cats": [], "type": tid})["cats"].append(cat["id"])
        t = block_totals[cat["id"]]
        print(f"- {cat['en']}: " + "、".join(f"{k} {v}" for k, v in t.items()))
    if not found:
        print("沒有取得任何文獻，此區間不覆寫。", file=sys.stderr)
        return None
    details = fetch_details(found.keys())
    articles = []
    for pmid, info in found.items():
        a = details.get(pmid)
        if not a:
            continue
        a["cats"], a["type"] = info["cats"], info["type"]
        a["abstract"] = ""          # 長區間不存摘要
        articles.append(a)
    articles.sort(key=lambda a: (a["sort"], a["pmid"]), reverse=True)
    try:
        jcr_year = journal_metrics(articles)
    except Exception as e:
        jcr_year = ""
        print(f"  ! 期刊 IF 取得失敗：{e}", file=sys.stderr)
    return {
        "scheme": SCHEME, "mode": "range", "range_key": key, "range_label": label,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "range_start": start.isoformat(), "range_end": end.isoformat(), "window_days": days,
        "per_block": RANGE_N, "block_totals": block_totals,
        "categories": [{k: c[k] for k in ("id", "en", "zh", "q")}
                       | {"total": sum(block_totals.get(c["id"], {}).values())} for c in CATEGORIES],
        "method": {"type_filter": TYPE_FILTER.strip(), "type_clauses": TYPE_CLAUSES, "jcr_year": jcr_year,
                   "core_journals": sorted(CORE_JOURNALS_DISPLAY)},
        "failed": failed,
        "articles": articles,
    }


def range_is_current(key, end):
    f = RANGE_DIR / f"{key}.json"
    if not f.exists():
        return False
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return False
    return d.get("range_end") == end.isoformat() and d.get("per_block") == RANGE_N and not d.get("failed")


def build_ranges(this_monday):
    end = this_monday - timedelta(days=1)       # 與每週資料對齊：截至上週日
    RANGE_DIR.mkdir(parents=True, exist_ok=True)
    for key, label, days in RANGES:
        if range_is_current(key, end):
            print(f"\n== 區間 {label}：本週已更新，略過 ==")
            continue
        try:
            data = build_range(key, label, days, end)
        except Exception as e:
            print(f"  ! 區間 {label} 建立失敗：{e}", file=sys.stderr)
            data = None
        if data:
            (RANGE_DIR / f"{key}.json").write_text(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            print(f"區間 {label}：{len(data['articles'])} 篇 → docs/range/{key}.json")


def needs_build(week_monday, is_current):
    """本週一律重建；過去的週次只有在缺檔、舊格式或上次有失敗時才補建。"""
    if is_current:
        return True
    f = ARCHIVE / f"{week_monday.isoformat()}.json"
    if not f.exists():
        return True
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return True
    return d.get("scheme") != SCHEME or bool(d.get("failed"))


def main():
    today = datetime.now(TPE).date()
    this_monday = today - timedelta(days=today.weekday())
    weeks = [this_monday - timedelta(weeks=k) for k in range(BACKFILL_WEEKS + 1)]
    latest = None
    for w in reversed(weeks):          # 由舊到新，最後處理本週
        if not needs_build(w, w == this_monday):
            print(f"\n== {w}：已有正確存檔，略過 ==")
            continue
        try:
            data = build_week(w)
        except Exception as e:      # 單一週失敗不影響其他週
            print(f"  ! {w} 建立失敗：{e}", file=sys.stderr)
            data = None
        if data is None:
            continue
        save_archive(data)
        if w == this_monday:
            latest = data
    if latest:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(latest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"\n完成：本週 {len(latest['articles'])} 篇 → {OUT.relative_to(ROOT)}")
    try:
        build_ranges(this_monday)
    except Exception as e:      # 長區間失敗不影響每週資料
        print(f"  ! 長區間更新失敗：{e}", file=sys.stderr)
    try:
        build_zh_queue(latest)
    except Exception as e:
        print(f"  ! 中文重點待辦清單建立失敗：{e}", file=sys.stderr)
    return 0


def zh_eligible(a):
    """需要中文重點的文章：共識／指引、統合分析／系統性回顧。"""
    return a.get("type") in ("guideline", "meta")


def key_text(abstract, limit=700):
    """從摘要取出最關鍵的段落：結論優先，再補結果，控制長度。"""
    if not abstract:
        return ""
    lines = abstract.split("\n")
    lab = lambda l: l.split(":", 1)[0].strip().upper()
    concl = [l for l in lines if lab(l) in ("CONCLUSION", "CONCLUSIONS", "INTERPRETATION", "CONCLUSIONS AND RELEVANCE",
             "MAIN CONCLUSIONS", "AUTHORS' CONCLUSIONS", "SUMMARY ANSWER", "IMPLICATIONS")]
    res = [l for l in lines if lab(l) in ("RESULTS", "RESULT", "FINDINGS", "MAIN RESULTS", "MAIN RESULTS AND THE ROLE OF CHANCE")]
    if concl:
        txt = "\n".join(concl)
        room = limit - len(txt)
        if res and room > 150:
            r = res[0] if len(res[0]) <= room else res[0][:room].rsplit(" ", 1)[0] + "…"
            txt = r + "\n" + txt
        return txt if len(txt) <= limit + 200 else txt[:limit + 200].rsplit(" ", 1)[0] + "…"
    tail = abstract[-limit:]
    if len(abstract) > limit:           # 從句子開頭起算
        i = tail.find(". ")
        tail = tail[i + 2:] if 0 <= i < 200 else tail
    return tail


def build_zh_queue(latest):
    """整理本週還沒有中文重點的文章（只看最新一週，不回補長區間）。"""
    done = {}
    if ZH_FILE.exists():
        try:
            done = json.loads(ZH_FILE.read_text(encoding="utf-8"))
        except Exception:
            done = {}
    if latest is None and OUT.exists():
        latest = json.loads(OUT.read_text(encoding="utf-8"))
    sources = [("week", (latest or {}).get("articles", []))]
    items, seen = [], set(done)
    for src, arts in sources:
        for a in arts:
            if a["pmid"] in seen or not zh_eligible(a):
                continue
            seen.add(a["pmid"])
            items.append({"pmid": a["pmid"], "src": src, "type": a.get("type", ""), "ta": a.get("ta", ""),
                          "title": a.get("title", ""), "abstract": a.get("abstract", "")})
            if len(items) >= ZH_QUEUE_MAX:
                break
    # 長區間的文章沒有存摘要，補抓（只抓佇列中的）
    need = [it["pmid"] for it in items if not it["abstract"]]
    if need:
        try:
            got = fetch_details(need)
            for it in items:
                if not it["abstract"] and it["pmid"] in got:
                    it["abstract"] = got[it["pmid"]].get("abstract", "")
        except Exception as e:
            print(f"  ! 補抓摘要失敗：{e}", file=sys.stderr)
    for it in items:
        it["key"] = key_text(it.pop("abstract"))
    ZH_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    ZH_QUEUE.write_text(json.dumps({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    "items": items}, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"中文重點待辦：{len(items)} 篇（已完成 {len(done)} 篇）→ data/zh_queue.json")


def save_archive(data):
    """每週存一份 docs/archive/<週一日期>.json，並更新 docs/archive/index.json。
    同一週內重複執行（例如修改網頁後自動重跑）會覆寫同一週的檔案。"""
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    (ARCHIVE / f"{data['week']}.json").write_text(payload, encoding="utf-8")

    files = sorted((f for f in ARCHIVE.glob("????-??-??.json")), reverse=True)
    for f in list(files):
        try:
            if json.loads(f.read_text(encoding="utf-8")).get("scheme") != SCHEME:
                f.unlink()          # 舊版「往前 7 天」格式，範圍會重疊，移除
                files.remove(f)
        except Exception:
            pass
    for old in files[KEEP_WEEKS:]:
        old.unlink()
    weeks = []
    for f in files[:KEEP_WEEKS]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        weeks.append({"week": f.stem, "file": f"archive/{f.name}", "count": len(d.get("articles", [])),
                      "range_start": d.get("range_start", ""), "range_end": d.get("range_end", ""),
                      "updated": d.get("updated", "")})
    (ARCHIVE / "index.json").write_text(
        json.dumps({"weeks": weeks}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"歷史週次：{len(weeks)} 週（{data['week']} 已存檔）")


if __name__ == "__main__":
    sys.exit(main())
