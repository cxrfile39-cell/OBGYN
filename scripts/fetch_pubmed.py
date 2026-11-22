#!/usr/bin/env python3
"""
每週從 PubMed 抓取婦產科各次專科最新文獻（Journal article / Review / Guideline），
輸出到 docs/data.json。只用 Python 標準函式庫，不需安裝套件。

環境變數（可選）：
  NCBI_API_KEY  NCBI API key（有的話速率上限較高）
  NCBI_EMAIL    聯絡 email（NCBI 建議提供）
  DAYS          抓取最近幾天新收錄的文獻，預設 7
  RETMAX        每個次專科最多幾篇，預設 60
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
API_KEY = os.environ.get("NCBI_API_KEY", "").strip()
EMAIL = os.environ.get("NCBI_EMAIL", "").strip()
DAYS = int(os.environ.get("DAYS", "7"))
RETMAX = int(os.environ.get("RETMAX", "60"))
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
CORE_JOURNALS = {s.lower() for s in [
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
]}

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


def search(cat):
    term = cat["q"] + TYPE_FILTER
    raw = http("esearch.fcgi", {
        "db": "pubmed", "term": term, "retmax": RETMAX, "sort": "pub_date",
        "datetype": "edat", "reldate": DAYS, "retmode": "json",
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
    if len(abstract) > 2500:
        abstract = abstract[:2500].rsplit(" ", 1)[0] + "…"

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
        "abstract": abstract,
        "cats": [],
    }


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


def main():
    print(f"抓取最近 {DAYS} 天的文獻，每類最多 {RETMAX} 篇")
    cat_ids, counts, failed = {}, {}, []
    for cat in CATEGORIES:
        try:
            ids, total = search(cat)
            cat_ids[cat["id"]] = ids
            counts[cat["id"]] = total
            print(f"- {cat['en']}: {len(ids)}（共 {total}）")
        except Exception as e:
            failed.append(cat["id"])
            print(f"- {cat['en']}: 搜尋失敗 {e}", file=sys.stderr)

    all_ids = {i for ids in cat_ids.values() for i in ids}
    if not all_ids:
        print("沒有取得任何文獻（可能是網路或 NCBI 暫時故障），保留舊資料不覆寫。", file=sys.stderr)
        return 0

    details = fetch_details(all_ids)
    for cid, ids in cat_ids.items():
        for pmid in ids:
            if pmid in details:
                details[pmid]["cats"].append(cid)

    articles = sorted(details.values(), key=lambda a: (a["sort"], a["pmid"]), reverse=True)
    now_tpe = datetime.now(TPE)
    week = (now_tpe.date() - timedelta(days=now_tpe.weekday())).isoformat()  # 該週的週一（台北時間）
    data = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week": week,
        "range_start": (now_tpe.date() - timedelta(days=DAYS)).isoformat(),
        "range_end": now_tpe.date().isoformat(),
        "window_days": DAYS,
        "categories": [{k: c[k] for k in ("id", "en", "zh")} | {"total": counts.get(c["id"], 0)}
                       for c in CATEGORIES],
        "failed": failed,
        "articles": articles,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"完成：{len(articles)} 篇 → {OUT.relative_to(ROOT)}")
    save_archive(data)
    return 0


def save_archive(data):
    """每週存一份 docs/archive/<週一日期>.json，並更新 docs/archive/index.json。
    同一週內重複執行（例如修改網頁後自動重跑）會覆寫同一週的檔案。"""
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    (ARCHIVE / f"{data['week']}.json").write_text(payload, encoding="utf-8")

    files = sorted((f for f in ARCHIVE.glob("????-??-??.json")), reverse=True)
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
