"""권위 있는 1차 소스(OpenAlex, Europe PMC, Semantic Scholar, CORE 백업, arXiv, RSS) 수집 및 전문 추출."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import io
import os
from pathlib import Path
import re
import time
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from pypdf import PdfReader
import requests

from deep_dive.models import FetchedDocument, SourceCandidate
from deep_dive.themes import (
    ArxivQuerySpec,
    EuropePmcQuerySpec,
    OpenAlexQuerySpec,
    PmcQuerySpec,
    RssFeedSpec,
    SemanticScholarQuerySpec,
    ThemeSlot,
)


USER_AGENT = (
    "Mozilla/5.0 (compatible; DeepDiveKnowledgeBriefing/1.0; "
    "+https://github.com/dndlr56/nyt-briefing)"
)
HTTP_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/xml, application/rss+xml, text/html, application/pdf;q=0.9, */*;q=0.8",
}

OPENALEX_API_URL = "https://api.openalex.org/works"
EUROPE_PMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EUROPE_PMC_FULLTEXT_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmc_id}/fullTextXML"
SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
CORE_SEARCH_WORKS_URL = "https://api.core.ac.uk/v3/search/works"
ARXIV_API_URL = "https://export.arxiv.org/api/query"
NCBI_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

MIN_FULLTEXT_CHARS = 2400  # 유료 페이월/짧은 초록 스텁 배제 기준
MAX_LLM_DOCUMENT_CHARS = 75_000  # 단일 문서 LLM 입력 상한 (~20K~25K 토큰)
MAX_PDF_BYTES = 25 * 1024 * 1024  # 25 MB 안전 상한

_WS_RE = re.compile(r"\s+")
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_PAYWALL_MARKERS = (
    "subscribe to continue reading",
    "buy this article",
    "rent or buy this article",
    "already a subscriber? sign in",
    "get full access to this article",
    "purchase instant access",
)


def _clean_text(text: str) -> str:
    return _WS_RE.sub(" ", text or "").strip()


def _strip_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    return _clean_text(soup.get_text(" "))


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _normalize_doi(raw_doi: str) -> str:
    cleaned = (raw_doi or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
    return cleaned


def _reconstruct_openalex_abstract(inverted_index: dict | None) -> str:
    """OpenAlex abstract_inverted_index를 읽기 가능한 원문 초록 문자열로 복원합니다."""
    if not isinstance(inverted_index, dict) or not inverted_index:
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        if isinstance(positions, list):
            for pos in positions:
                if isinstance(pos, int):
                    positioned.append((pos, str(word)))
    positioned.sort(key=lambda pair: pair[0])
    return _clean_text(" ".join(w for _, w in positioned))


def _format_openalex_percentile(pct_obj: dict | None) -> str:
    if not isinstance(pct_obj, dict):
        return ""
    if pct_obj.get("is_in_top_1_percent"):
        return "Top 1%"
    if pct_obj.get("is_in_top_10_percent"):
        return "Top 10%"
    val = pct_obj.get("value")
    if isinstance(val, (int, float)) and 0 < val <= 1:
        top_pct = max(1, round((1.0 - float(val)) * 100))
        if top_pct <= 25:
            return f"Top {top_pct}%"
    return ""


def _parse_published_datetime(raw: str) -> datetime | None:
    """ISO 8601 또는 RFC 2822 날짜 문자열을 UTC aware datetime으로 파싱합니다."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(text)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    match = _ISO_DATE_RE.search(text)
    if match:
        y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            return datetime(y, m, d, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _filter_by_lookback(
    candidates: list[SourceCandidate],
    lookback_days: int,
    now_utc: datetime | None = None,
) -> list[SourceCandidate]:
    """지정된 lookback_days 이내 문헌만 남깁니다 (날짜 미상 항목은 보존, 0건이면 완화 폴백)."""
    if lookback_days <= 0 or not candidates:
        return candidates
    ref = now_utc or datetime.now(timezone.utc)
    cutoff = ref - timedelta(days=lookback_days)

    recent: list[SourceCandidate] = []
    for cand in candidates:
        pub_dt = _parse_published_datetime(cand.published)
        if pub_dt is None or pub_dt >= cutoff:
            recent.append(cand)
    if not recent:
        relaxed_cutoff = ref - timedelta(days=lookback_days * 2)
        recent = [
            c
            for c in candidates
            if (_parse_published_datetime(c.published) or ref) >= relaxed_cutoff
        ]
    return recent or candidates[:3]


def fetch_openalex_candidates(
    spec: OpenAlexQuerySpec,
    theme_id: str,
    now_utc: datetime | None = None,
) -> list[SourceCandidate]:
    """OpenAlex API에서 철회 논문을 제외하고 FWCI·분야별 상위 백분위 OA 문헌을 수집합니다."""
    ref = now_utc or datetime.now(timezone.utc)
    from_date = (ref - timedelta(days=365 * max(1, spec.lookback_years))).strftime("%Y-%m-%d")

    def _query(use_top10: bool) -> list[dict]:
        filters = [
            "is_oa:true",
            "is_retracted:false",
            "is_paratext:false",
            "language:en",
            f"from_publication_date:{from_date}",
        ]
        if spec.min_citations > 0:
            filters.append(f"cited_by_count:>{spec.min_citations}")
        if spec.work_type:
            filters.append(f"type:{spec.work_type}")
        if use_top10:
            filters.append("citation_normalized_percentile.is_in_top_10_percent:true")

        params = {
            "search": spec.search,
            "filter": ",".join(filters),
            "sort": "cited_by_count:desc",
            "per-page": spec.max_results,
        }
        resp = requests.get(
            OPENALEX_API_URL,
            params=params,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=25,
        )
        resp.raise_for_status()
        return resp.json().get("results") or []

    results = _query(use_top10=spec.prefer_top_10_percent)
    if spec.prefer_top_10_percent and len(results) < 3:
        time.sleep(0.25)
        results = _query(use_top10=False)

    candidates: list[SourceCandidate] = []
    for work in results:
        if not isinstance(work, dict) or work.get("is_retracted"):
            continue
        title = _clean_text(work.get("display_name") or work.get("title") or "")
        if not title:
            continue

        best_oa = work.get("best_oa_location") or {}
        primary = work.get("primary_location") or {}
        pdf_url = (best_oa.get("pdf_url") or primary.get("pdf_url") or "").strip()
        landing_url = (
            best_oa.get("landing_page_url")
            or primary.get("landing_page_url")
            or work.get("doi")
            or work.get("id")
            or ""
        ).strip()
        if not landing_url:
            continue

        source_obj = primary.get("source") or best_oa.get("source") or {}
        journal = _clean_text(source_obj.get("display_name") or spec.name)
        pub_date = _clean_text(work.get("publication_date") or str(work.get("publication_year") or ""))
        cited = int(work.get("cited_by_count") or 0)
        raw_fwci = work.get("fwci")
        fwci_val = round(float(raw_fwci), 2) if isinstance(raw_fwci, (int, float)) else None
        pct_label = _format_openalex_percentile(work.get("citation_normalized_percentile"))
        doi = _normalize_doi(str(work.get("doi") or ""))

        authorships = work.get("authorships") or []
        authors = ", ".join(
            _clean_text((a.get("author") or {}).get("display_name", ""))
            for a in authorships[:5]
            if isinstance(a, dict) and (a.get("author") or {}).get("display_name")
        )
        abstract = _reconstruct_openalex_abstract(work.get("abstract_inverted_index"))
        if not abstract:
            abstract = f"[OpenAlex Top-Impact OA] Journal: {journal}. Cited by {cited} works."

        pmc_id = ""
        ids_obj = work.get("ids") or {}
        raw_pmcid = str(ids_obj.get("pmcid") or "")
        if "PMC" in raw_pmcid.upper():
            pmc_match = re.search(r"PMC\d+", raw_pmcid, re.IGNORECASE)
            if pmc_match:
                pmc_id = pmc_match.group(0).upper()

        candidates.append(
            SourceCandidate(
                title=title,
                url=landing_url,
                source_name=f"{journal} (OpenAlex OA)",
                theme_id=theme_id,
                published=pub_date[:10],
                authors=authors,
                abstract=abstract[:1500],
                pdf_url=pdf_url,
                pmc_id=pmc_id,
                source_kind="review" if spec.work_type == "review" else "paper",
                sub_topic_hint=spec.sub_topic_hint,
                authority_tier=1,
                doi=doi,
                cited_by_count=cited,
                fwci=fwci_val,
                top_percentile_label=pct_label,
            )
        )
    # FWCI와 피인용 수를 종합해 정렬
    candidates.sort(key=lambda c: ((c.fwci or 0.0), c.cited_by_count), reverse=True)
    return candidates


def fetch_europe_pmc_candidates(
    spec: EuropePmcQuerySpec,
    theme_id: str,
    now_utc: datetime | None = None,
) -> list[SourceCandidate]:
    """Europe PMC REST API에서 OPEN_ACCESS:y + 피인용 순(CITED desc) 고인용 의학·뇌과학 리뷰를 수집합니다."""
    ref = now_utc or datetime.now(timezone.utc)
    from_date = (ref - timedelta(days=365 * max(1, spec.lookback_years))).strftime("%Y-%m-%d")
    to_date = ref.strftime("%Y-%m-%d")

    full_query = f"({spec.query}) AND FIRST_PDATE:[{from_date} TO {to_date}]"
    params = {
        "query": full_query,
        "resultType": "core",
        "sort": "CITED desc",
        "pageSize": spec.max_results,
        "format": "json",
    }
    resp = requests.get(
        EUROPE_PMC_SEARCH_URL,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=25,
    )
    resp.raise_for_status()
    items = resp.json().get("resultList", {}).get("result", []) or []

    candidates: list[SourceCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title") or "")
        if not title:
            continue

        pmc_id = str(item.get("pmcid") or "").strip().upper()
        doi = _normalize_doi(str(item.get("doi") or ""))
        journal = _clean_text(
            (item.get("journalInfo") or {}).get("journal", {}).get("title")
            or item.get("journalTitle")
            or spec.name
        )
        pub_date = _clean_text(item.get("firstPublicationDate") or str(item.get("pubYear") or ""))
        authors = _clean_text(item.get("authorString") or "")[:200]
        abstract = _strip_html(str(item.get("abstractText") or ""))
        cited = int(item.get("citedByCount") or 0)

        pdf_url = ""
        landing_url = f"https://europepmc.org/article/PMC/{pmc_id}" if pmc_id else ""
        url_list = (item.get("fullTextUrlList") or {}).get("fullTextUrl") or []
        for entry in url_list:
            if not isinstance(entry, dict):
                continue
            u = str(entry.get("url") or "").strip()
            style = str(entry.get("documentStyle") or "").lower()
            avail = str(entry.get("availabilityCode") or "").upper()
            if style == "pdf" and avail in ("OA", "F") and u:
                pdf_url = u
            elif not landing_url and u:
                landing_url = u
        if not landing_url and doi:
            landing_url = f"https://doi.org/{doi}"
        if not landing_url:
            continue

        candidates.append(
            SourceCandidate(
                title=title,
                url=landing_url,
                source_name=f"{journal} (Europe PMC OA)",
                theme_id=theme_id,
                published=pub_date[:10],
                authors=authors,
                abstract=(abstract or f"[Europe PMC OA] Journal: {journal}, Cited: {cited}")[:1500],
                pdf_url=pdf_url,
                pmc_id=pmc_id,
                source_kind="review",
                sub_topic_hint=spec.sub_topic_hint,
                authority_tier=1,
                doi=doi,
                cited_by_count=cited,
            )
        )
    return candidates


def fetch_semantic_scholar_candidates(
    spec: SemanticScholarQuerySpec,
    theme_id: str,
    now_utc: datetime | None = None,
) -> list[SourceCandidate]:
    """Semantic Scholar Academic Graph API에서 실질적 핵심 인용(influentialCitationCount) 기반 OA 논문을 수집합니다."""
    ref = now_utc or datetime.now(timezone.utc)
    start_year = ref.year - max(1, spec.lookback_years)
    year_range = f"{start_year}-{ref.year}"

    params: dict[str, object] = {
        "query": spec.query,
        "year": year_range,
        "minCitationCount": spec.min_citations,
        "openAccessPdf": "",
        "limit": max(spec.max_results * 2, 16),
        "fields": (
            "title,url,year,publicationDate,venue,authors,abstract,"
            "citationCount,influentialCitationCount,openAccessPdf,externalIds,tldr"
        ),
    }
    if spec.fields_of_study:
        params["fieldsOfStudy"] = ",".join(spec.fields_of_study)

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    api_key = (os.getenv("SEMANTIC_SCHOLAR_API_KEY") or "").strip()
    if api_key:
        headers["x-api-key"] = api_key

    resp = requests.get(
        SEMANTIC_SCHOLAR_SEARCH_URL,
        params=params,
        headers=headers,
        timeout=25,
    )
    resp.raise_for_status()
    rows = resp.json().get("data") or []

    candidates: list[SourceCandidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _clean_text(row.get("title") or "")
        if not title:
            continue

        oa_pdf = row.get("openAccessPdf") or {}
        pdf_url = str(oa_pdf.get("url") or "").strip()
        ext_ids = row.get("externalIds") or {}
        doi = _normalize_doi(str(ext_ids.get("DOI") or ""))
        arxiv_id = str(ext_ids.get("ArXiv") or "").strip()
        raw_pmc = str(ext_ids.get("PubMedCentral") or "").strip()
        pmc_id = f"PMC{raw_pmc}" if raw_pmc and not raw_pmc.upper().startswith("PMC") else raw_pmc.upper()

        if not pdf_url and arxiv_id:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        landing_url = str(row.get("url") or "").strip()
        if arxiv_id and not landing_url:
            landing_url = f"https://arxiv.org/abs/{arxiv_id}"
        if not landing_url and doi:
            landing_url = f"https://doi.org/{doi}"
        if not landing_url:
            continue

        venue = _clean_text(row.get("venue") or spec.name)
        pub_date = _clean_text(row.get("publicationDate") or str(row.get("year") or ""))
        authors = ", ".join(
            _clean_text(a.get("name") or "")
            for a in (row.get("authors") or [])[:5]
            if isinstance(a, dict) and a.get("name")
        )
        tldr_text = _clean_text((row.get("tldr") or {}).get("text") or "")
        abstract = _clean_text(row.get("abstract") or "")
        combined_abs = f"[TL;DR] {tldr_text} {abstract}".strip() if tldr_text else abstract
        cited = int(row.get("citationCount") or 0)
        influential = int(row.get("influentialCitationCount") or 0)

        candidates.append(
            SourceCandidate(
                title=title,
                url=landing_url,
                source_name=f"{venue} (Semantic Scholar OA)",
                theme_id=theme_id,
                published=pub_date[:10],
                authors=authors,
                abstract=combined_abs[:1500],
                pdf_url=pdf_url,
                pmc_id=pmc_id,
                source_kind="paper",
                sub_topic_hint=spec.sub_topic_hint,
                authority_tier=1,
                doi=doi,
                cited_by_count=cited,
                influential_citations=influential,
            )
        )

    candidates.sort(key=lambda c: (c.influential_citations, c.cited_by_count), reverse=True)
    return candidates[: spec.max_results]


def fetch_arxiv_candidates(spec: ArxivQuerySpec, theme_id: str) -> list[SourceCandidate]:
    """arXiv Atom API에서 최근 lookback_days 이내 권위 있는 논문 후보를 수집합니다."""
    params = {
        "search_query": spec.query,
        "start": 0,
        "max_results": max(spec.max_results * 2, 20),
        "sortBy": spec.sort_by,
        "sortOrder": "descending",
    }
    resp = requests.get(ARXIV_API_URL, params=params, headers=HTTP_HEADERS, timeout=25)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    candidates: list[SourceCandidate] = []

    for entry in root.findall("atom:entry", ns):
        title = _clean_text(entry.findtext("atom:title", "", ns))
        entry_id = (entry.findtext("atom:id", "", ns) or "").strip()
        summary = _clean_text(entry.findtext("atom:summary", "", ns))
        published = (entry.findtext("atom:published", "", ns) or "").strip()
        authors = ", ".join(
            _clean_text(a.findtext("atom:name", "", ns))
            for a in entry.findall("atom:author", ns)[:5]
        )

        pdf_url = ""
        for link in entry.findall("atom:link", ns):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = link.attrib.get("href", "").strip()
                break
        if not pdf_url and "/abs/" in entry_id:
            pdf_url = entry_id.replace("/abs/", "/pdf/") + ".pdf"

        if title and entry_id:
            candidates.append(
                SourceCandidate(
                    title=title,
                    url=entry_id,
                    source_name=spec.name,
                    theme_id=theme_id,
                    published=published[:10],
                    authors=authors,
                    abstract=summary[:1500],
                    pdf_url=pdf_url,
                    source_kind="paper",
                    sub_topic_hint=spec.sub_topic_hint,
                    authority_tier=1,
                )
            )
    return _filter_by_lookback(candidates, spec.lookback_days)[: spec.max_results]


def fetch_pmc_candidates(spec: PmcQuerySpec, theme_id: str) -> list[SourceCandidate]:
    """PubMed Central(PMC) Open Access 논문 후보를 최근 lookback_days 기준으로 수집합니다."""
    search_params = {
        "db": "pmc",
        "term": spec.query,
        "retmax": spec.max_results,
        "sort": spec.sort_by,
        "datetype": "pdat",
        "reldate": spec.lookback_days,
        "retmode": "json",
    }
    search_resp = requests.get(
        NCBI_ESEARCH_URL, params=search_params, headers=HTTP_HEADERS, timeout=25
    )
    search_resp.raise_for_status()
    id_list = search_resp.json().get("esearchresult", {}).get("idlist", [])
    if not id_list and spec.lookback_days < 1825:
        search_params["reldate"] = 1825
        time.sleep(0.4)
        retry_resp = requests.get(
            NCBI_ESEARCH_URL, params=search_params, headers=HTTP_HEADERS, timeout=25
        )
        retry_resp.raise_for_status()
        id_list = retry_resp.json().get("esearchresult", {}).get("idlist", [])
    if not id_list:
        return []

    time.sleep(0.4)
    summary_params = {"db": "pmc", "id": ",".join(id_list), "retmode": "json"}
    sum_resp = requests.get(
        NCBI_ESUMMARY_URL, params=summary_params, headers=HTTP_HEADERS, timeout=25
    )
    sum_resp.raise_for_status()
    result_map = sum_resp.json().get("result", {})

    candidates: list[SourceCandidate] = []
    for pmcid_num in id_list:
        item = result_map.get(str(pmcid_num))
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title", ""))
        if not title:
            continue
        pmc_code = f"PMC{pmcid_num}" if not str(pmcid_num).startswith("PMC") else str(pmcid_num)
        journal = _clean_text(item.get("fulljournalname") or item.get("source") or spec.name)
        pub_date = _clean_text(item.get("pubdate", ""))
        authors_raw = item.get("authors") or []
        authors = ", ".join(
            _clean_text(a.get("name", ""))
            for a in authors_raw[:5]
            if isinstance(a, dict) and a.get("name")
        )
        article_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmc_code}/"
        pdf_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmc_code}/pdf/"

        candidates.append(
            SourceCandidate(
                title=title,
                url=article_url,
                source_name=f"{journal} (PMC OA)",
                theme_id=theme_id,
                published=pub_date,
                authors=authors,
                abstract=f"[PMC Open Access Full Text] Journal: {journal}. Published: {pub_date}.",
                pdf_url=pdf_url,
                pmc_id=pmc_code,
                source_kind="review",
                sub_topic_hint=spec.sub_topic_hint,
                authority_tier=1,
            )
        )
    return candidates


def fetch_rss_candidates(spec: RssFeedSpec, theme_id: str) -> list[SourceCandidate]:
    """표준 RSS 2.0 / Atom / RDF 피드에서 lookback_days 이내 문헌 후보를 수집합니다."""
    resp = requests.get(spec.url, headers=HTTP_HEADERS, timeout=25)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    candidates: list[SourceCandidate] = []

    items: list[ET.Element] = []
    for elem in root.iter():
        tag = _local_tag(elem.tag)
        if tag in ("item", "entry"):
            items.append(elem)

    for item in items[: max(spec.max_items * 2, 16)]:
        title = ""
        link = ""
        published = ""
        summary = ""
        authors_list: list[str] = []
        pdf_url = ""

        for child in item:
            tag = _local_tag(child.tag)
            if tag == "title" and not title:
                title = _clean_text(child.text or "")
            elif tag == "link":
                href = (child.attrib.get("href") or child.text or "").strip()
                link_type = (child.attrib.get("type") or "").lower()
                if link_type == "application/pdf" or href.lower().endswith(".pdf"):
                    pdf_url = href
                elif not link and href:
                    link = href
            elif tag in ("pubDate", "published", "updated", "date") and not published:
                published = _clean_text(child.text or "")
            elif tag in ("description", "summary", "encoded") and not summary:
                summary = _strip_html(child.text or "")
            elif tag in ("creator", "author"):
                name = _clean_text(child.text or "")
                if not name:
                    for sub in child:
                        if _local_tag(sub.tag) == "name" and sub.text:
                            name = _clean_text(sub.text)
                            break
                if name:
                    authors_list.append(name)
            elif tag == "enclosure":
                enc_url = (child.attrib.get("url") or "").strip()
                enc_type = (child.attrib.get("type") or "").lower()
                if "pdf" in enc_type or enc_url.lower().endswith(".pdf"):
                    pdf_url = enc_url

        if title and link:
            candidates.append(
                SourceCandidate(
                    title=title,
                    url=link,
                    source_name=spec.name,
                    theme_id=theme_id,
                    published=published[:35],
                    authors=", ".join(authors_list[:4]),
                    abstract=summary[:1500],
                    pdf_url=pdf_url,
                    source_kind=spec.source_kind,
                    sub_topic_hint=spec.sub_topic_hint,
                    authority_tier=spec.authority_tier,
                )
            )
    return _filter_by_lookback(candidates, spec.lookback_days)[: spec.max_items]


def collect_candidates_for_theme(theme: ThemeSlot) -> list[SourceCandidate]:
    """주어진 테마 슬롯의 모든 소스(OpenAlex, Europe PMC, Semantic Scholar, PMC, arXiv, RSS)에서 후보를 병합 수집합니다."""
    all_candidates: list[SourceCandidate] = []
    seen_keys: set[str] = set()

    def _extend(new_items: list[SourceCandidate]) -> None:
        for item in new_items:
            norm_url = item.url.strip().rstrip("/")
            norm_doi = item.doi.lower().strip()
            norm_title = re.sub(r"\W+", "", item.title.lower())[:60]
            keys = [k for k in (norm_url, f"doi:{norm_doi}" if norm_doi else "", f"t:{norm_title}") if k]
            if any(k in seen_keys for k in keys):
                continue
            for k in keys:
                seen_keys.add(k)
            all_candidates.append(item)

    for oq in theme.openalex_queries:
        try:
            items = fetch_openalex_candidates(oq, theme.theme_id)
            print(f"   • [OpenAlex FWCI/OA] {oq.name}: {len(items)}건 수집")
            _extend(items)
            time.sleep(0.3)
        except Exception as exc:
            print(f"   • [WARN] OpenAlex 수집 실패 ({oq.name}): {exc}")

    for eq in theme.europe_pmc_queries:
        try:
            items = fetch_europe_pmc_candidates(eq, theme.theme_id)
            print(f"   • [Europe PMC OA] {eq.name}: {len(items)}건 수집")
            _extend(items)
            time.sleep(0.3)
        except Exception as exc:
            print(f"   • [WARN] Europe PMC 수집 실패 ({eq.name}): {exc}")

    for sq in theme.semantic_scholar_queries:
        try:
            items = fetch_semantic_scholar_candidates(sq, theme.theme_id)
            print(f"   • [Semantic Scholar] {sq.name}: {len(items)}건 수집")
            _extend(items)
            time.sleep(1.1)  # S2 1 RPS rate limit 준수
        except Exception as exc:
            print(f"   • [WARN] Semantic Scholar 수집 실패 ({sq.name}): {exc}")

    for pq in theme.pmc_queries:
        try:
            items = fetch_pmc_candidates(pq, theme.theme_id)
            print(f"   • [NCBI PMC OA] {pq.name}: {len(items)}건 수집")
            _extend(items)
            time.sleep(0.5)
        except Exception as exc:
            print(f"   • [WARN] PMC OA 수집 실패 ({pq.name}): {exc}")

    for aq in theme.arxiv_queries:
        try:
            items = fetch_arxiv_candidates(aq, theme.theme_id)
            print(f"   • [arXiv] {aq.name}: {len(items)}건 수집")
            _extend(items)
            time.sleep(0.5)
        except Exception as exc:
            print(f"   • [WARN] arXiv 수집 실패 ({aq.name}): {exc}")

    for rf in theme.rss_feeds:
        try:
            items = fetch_rss_candidates(rf, theme.theme_id)
            print(f"   • [RSS] {rf.name}: {len(items)}건 수집")
            _extend(items)
        except Exception as exc:
            print(f"   • [WARN] RSS 수집 실패 ({rf.name}): {exc}")

    return all_candidates


def extract_text_from_pdf_bytes(pdf_bytes: bytes, max_pages: int = 45) -> str:
    """PDF 바이트에서 본문 텍스트를 추출합니다."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages_text: list[str] = []
    for page in reader.pages[:max_pages]:
        txt = page.extract_text() or ""
        if txt.strip():
            pages_text.append(txt.strip())
    return "\n\n".join(pages_text).strip()


def _download_pdf(url: str, timeout: int = 45) -> bytes | None:
    resp = requests.get(url, headers=HTTP_HEADERS, timeout=timeout, stream=True)
    resp.raise_for_status()
    content_type = (resp.headers.get("Content-Type") or "").lower()

    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=65536):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_PDF_BYTES:
            return None
        chunks.append(chunk)

    raw = b"".join(chunks)
    if raw.startswith(b"%PDF") or "application/pdf" in content_type:
        return raw
    return None


def _parse_jats_fulltext_xml(xml_bytes: bytes) -> str:
    """PMC / Europe PMC JATS XML에서 초록과 본문 섹션 전체를 추출합니다."""
    root = ET.fromstring(xml_bytes)
    sections: list[str] = []

    for abstract in root.findall(".//abstract"):
        txt = _clean_text(" ".join(abstract.itertext()))
        if txt:
            sections.append(f"[Abstract]\n{txt}")

    body = root.find(".//body")
    if body is not None:
        for sec in body.findall(".//sec"):
            sec_title = _clean_text(sec.findtext("title", ""))
            paras = [_clean_text(" ".join(p.itertext())) for p in sec.findall("p")]
            paras = [p for p in paras if p]
            if paras:
                header = f"## {sec_title}\n" if sec_title else ""
                sections.append(header + "\n".join(paras))
        if not sections:
            raw_body = _clean_text(" ".join(body.itertext()))
            if raw_body:
                sections.append(raw_body)

    return "\n\n".join(sections).strip()


def _fetch_pmc_fulltext_xml(pmc_id: str) -> str:
    """Europe PMC fullTextXML 우선 시도 후 실패 시 NCBI PMC EFetch XML로 전문을 추출합니다."""
    normalized = pmc_id.upper().strip()
    if not normalized.startswith("PMC"):
        normalized = f"PMC{normalized}"

    try:
        epmc_resp = requests.get(
            EUROPE_PMC_FULLTEXT_URL.format(pmc_id=normalized),
            headers=HTTP_HEADERS,
            timeout=30,
        )
        if epmc_resp.ok and b"<body" in epmc_resp.content:
            parsed = _parse_jats_fulltext_xml(epmc_resp.content)
            if len(parsed) >= MIN_FULLTEXT_CHARS:
                return parsed
    except Exception:
        pass

    numeric_id = normalized.replace("PMC", "")
    resp = requests.get(
        NCBI_EFETCH_URL,
        params={"db": "pmc", "id": numeric_id, "retmode": "xml"},
        headers=HTTP_HEADERS,
        timeout=35,
    )
    resp.raise_for_status()
    return _parse_jats_fulltext_xml(resp.content)


def _fetch_core_fulltext_fallback(candidate: SourceCandidate) -> tuple[str, bytes | None]:
    """CORE API v3(무료 CORE_API_KEY 설정 시)로 대학 기관 리포지토리의 Green OA 전문/PDF를 회수합니다."""
    api_key = (os.getenv("CORE_API_KEY") or "").strip()
    if not api_key:
        return "", None

    query = f'doi:"{candidate.doi}"' if candidate.doi else f'title:"{candidate.title[:120]}"'
    resp = requests.get(
        CORE_SEARCH_WORKS_URL,
        params={"q": query, "limit": 3},
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        timeout=25,
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []

    for work in results:
        if not isinstance(work, dict):
            continue
        full_text = _clean_text(str(work.get("fullText") or ""))
        dl_url = str(work.get("downloadUrl") or "").strip()
        pdf_bytes: bytes | None = None
        if dl_url:
            try:
                pdf_bytes = _download_pdf(dl_url, timeout=35)
                if pdf_bytes and len(full_text) < MIN_FULLTEXT_CHARS:
                    full_text = extract_text_from_pdf_bytes(pdf_bytes)
            except Exception:
                pass
        if len(full_text) >= MIN_FULLTEXT_CHARS:
            return full_text, pdf_bytes

    return "", None


def _extract_html_article_and_pdf_link(url: str) -> tuple[str, str]:
    """웹페이지에서 본문 텍스트와 (존재 시) PDF 다운로드 링크를 추출합니다."""
    resp = requests.get(url, headers=HTTP_HEADERS, timeout=30)
    resp.raise_for_status()
    content_type = (resp.headers.get("Content-Type") or "").lower()
    if "application/pdf" in content_type or resp.content.startswith(b"%PDF"):
        return "", url

    soup = BeautifulSoup(resp.text, "html.parser")

    discovered_pdf = ""
    meta_pdf = soup.find("meta", attrs={"name": "citation_pdf_url"})
    if meta_pdf and meta_pdf.get("content"):
        discovered_pdf = urljoin(url, str(meta_pdf["content"]).strip())
    if not discovered_pdf:
        for a in soup.find_all("a", href=True):
            href = str(a["href"]).strip()
            if href.lower().endswith(".pdf") or "/pdf/" in href.lower():
                discovered_pdf = urljoin(url, href)
                break

    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg"]):
        tag.decompose()

    container = (
        soup.find("article")
        or soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.body
        or soup
    )

    blocks: list[str] = []
    for elem in container.find_all(["h1", "h2", "h3", "p", "blockquote", "li"]):
        txt = _clean_text(elem.get_text(" "))
        if len(txt) < 25 and elem.name not in ("h1", "h2", "h3"):
            continue
        if elem.name in ("h1", "h2", "h3"):
            blocks.append(f"\n## {txt}")
        else:
            blocks.append(txt)

    text = "\n\n".join(blocks).strip()
    return text, discovered_pdf


def _looks_paywalled(text: str) -> bool:
    lowered = text.lower()
    if len(text) < MIN_FULLTEXT_CHARS:
        return True
    hit_count = sum(1 for marker in _PAYWALL_MARKERS if marker in lowered)
    return hit_count >= 1 and len(text) < 4500


def _safe_pdf_filename(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9가-힣_-]+", "_", title).strip("_")[:60]
    return f"{slug or 'original_source'}.pdf"


def fetch_full_document(
    candidate: SourceCandidate,
    min_chars: int = MIN_FULLTEXT_CHARS,
    max_llm_chars: int = MAX_LLM_DOCUMENT_CHARS,
) -> FetchedDocument | None:
    """
    원칙적 전문(Full-text) 확보:
    1) Europe PMC / NCBI PMC Open Access XML 우선
    2) 직접 PDF URL 다운로드 & 텍스트 추출 (원본 PDF 바이트 보존)
    3) HTML 본문 추출 (+ 페이지 내 PDF 링크 탐색)
    4) CORE API v3 Green OA 리포지토리 백업 회수 (CORE_API_KEY 설정 시)
    """
    pdf_bytes: bytes | None = None
    full_text = ""
    text_source = ""

    # 1) Europe PMC / NCBI PMC XML
    if candidate.pmc_id:
        try:
            xml_text = _fetch_pmc_fulltext_xml(candidate.pmc_id)
            if len(xml_text) >= min_chars:
                full_text = xml_text
                text_source = "pmc_xml"
        except Exception as exc:
            print(f"      [WARN] PMC XML 추출 실패 ({candidate.pmc_id}): {exc}")

    # 2) 직접 PDF URL
    if candidate.pdf_url:
        try:
            downloaded = _download_pdf(candidate.pdf_url)
            if downloaded:
                pdf_bytes = downloaded
                if not full_text:
                    pdf_text = extract_text_from_pdf_bytes(downloaded)
                    if len(pdf_text) >= min_chars:
                        full_text = pdf_text
                        text_source = "pdf"
        except Exception as exc:
            print(f"      [WARN] PDF 다운로드/파싱 실패 ({candidate.pdf_url}): {exc}")

    # 3) HTML 전문 추출
    if not full_text and candidate.url:
        try:
            html_text, discovered_pdf = _extract_html_article_and_pdf_link(candidate.url)
            if discovered_pdf and pdf_bytes is None:
                try:
                    downloaded = _download_pdf(discovered_pdf)
                    if downloaded:
                        pdf_bytes = downloaded
                        pdf_text = extract_text_from_pdf_bytes(downloaded)
                        if len(pdf_text) > len(html_text):
                            html_text = pdf_text
                            text_source = "pdf"
                except Exception:
                    pass

            if html_text and not _looks_paywalled(html_text) and len(html_text) >= min_chars:
                full_text = html_text
                text_source = text_source or "html"
        except Exception as exc:
            print(f"      [WARN] HTML 전문 추출 실패 ({candidate.url}): {exc}")

    # 4) CORE API v3 대학 리포지토리 전문/PDF 백업 회수
    if not full_text and os.getenv("CORE_API_KEY"):
        try:
            core_text, core_pdf = _fetch_core_fulltext_fallback(candidate)
            if len(core_text) >= min_chars:
                full_text = core_text
                text_source = "core_oa"
                if core_pdf and pdf_bytes is None:
                    pdf_bytes = core_pdf
        except Exception as exc:
            print(f"      [WARN] CORE API 백업 전문 추출 실패: {exc}")

    if not full_text or len(full_text) < min_chars:
        return None

    truncated = False
    if len(full_text) > max_llm_chars:
        head_len = int(max_llm_chars * 0.75)
        tail_len = max_llm_chars - head_len - 80
        full_text = (
            full_text[:head_len].rstrip()
            + "\n\n[... 중간 세부 표/부록 생략 — 원문 PDF 전체는 아카이브에 보관됨 ...]\n\n"
            + full_text[-tail_len:].lstrip()
        )
        truncated = True

    return FetchedDocument(
        candidate=candidate,
        full_text=full_text,
        text_source=text_source,
        pdf_bytes=pdf_bytes,
        pdf_filename=_safe_pdf_filename(candidate.title) if pdf_bytes else "",
        char_count=len(full_text),
        truncated_for_llm=truncated,
    )


def load_manual_source(
    source_url: str | None = None,
    pdf_path: Path | None = None,
    theme_id: str = "weekend_special",
) -> FetchedDocument:
    """사용자가 직접 지정한 URL 또는 로컬 PDF 파일을 FetchedDocument로 로드합니다."""
    if pdf_path is not None:
        if not pdf_path.exists():
            raise FileNotFoundError(f"지정한 PDF 파일을 찾을 수 없습니다: {pdf_path}")
        pdf_bytes = pdf_path.read_bytes()
        text = extract_text_from_pdf_bytes(pdf_bytes)
        if not text:
            raise ValueError(f"PDF에서 텍스트를 추출하지 못했습니다: {pdf_path}")
        cand = SourceCandidate(
            title=pdf_path.stem.replace("_", " ").strip(),
            url=source_url or f"file://{pdf_path.name}",
            source_name="Manual PDF Upload",
            theme_id=theme_id,
            source_kind="paper",
        )
        truncated = len(text) > MAX_LLM_DOCUMENT_CHARS
        if truncated:
            head_len = int(MAX_LLM_DOCUMENT_CHARS * 0.75)
            tail_len = MAX_LLM_DOCUMENT_CHARS - head_len - 80
            text = text[:head_len] + "\n\n[... 중간 생략 ...]\n\n" + text[-tail_len:]
        return FetchedDocument(
            candidate=cand,
            full_text=text,
            text_source="manual_pdf",
            pdf_bytes=pdf_bytes,
            pdf_filename=pdf_path.name,
            truncated_for_llm=truncated,
        )

    if source_url:
        host = urlparse(source_url).netloc or "Manual URL"
        is_pdf = source_url.lower().endswith(".pdf") or "/pdf/" in source_url.lower()
        cand = SourceCandidate(
            title=source_url.rsplit("/", 1)[-1] or host,
            url=source_url,
            source_name=host,
            theme_id=theme_id,
            pdf_url=source_url if is_pdf else "",
            source_kind="paper" if is_pdf else "article",
        )
        doc = fetch_full_document(cand, min_chars=800)
        if doc is None:
            raise RuntimeError(f"지정한 URL에서 전문 텍스트를 추출하지 못했습니다: {source_url}")
        return doc

    raise ValueError("source_url 또는 pdf_path 중 하나가 필요합니다.")
