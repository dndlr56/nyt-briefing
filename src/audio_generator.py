"""Edge TTS / Google Cloud TTS(무료 티어) / Gemini TTS로 단일 앵커 MP3 오디오를 생성합니다."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import io
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import wave

from google import genai
import requests

from script_writer import RoundupAudioScript, get_script_segments
from usage_tracker import is_rate_limit_error, try_switch_to_paid_key


KST = timezone(timedelta(hours=9))
CLOUD_TTS_ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"
CLOUD_TTS_MAX_UTF8_BYTES = 4200  # Google Cloud TTS 5,000바이트 제한에 여유 버퍼 확보

DEFAULT_TTS_PROVIDER = "gemini_tts"
DEFAULT_EDGE_TTS_VOICE = "ko-KR-HyunsuMultilingualNeural"
DEFAULT_CLOUD_TTS_VOICE = "ko-KR-Chirp3-HD-Kore"
DEFAULT_GEMINI_TTS_MODEL = "gemini-3.8-flash-lite-tts"
DEFAULT_GEMINI_TTS_VOICE = "auto_rotate"
# 매일 다른 여성 목소리로 비교 테스트하기 위한 공식 Gemini TTS 여성 보이스 로테이션 (11/20=Kore 기준)
GEMINI_TTS_ROTATION_VOICES: tuple[str, ...] = (
    "Kore",          # Firm (단정·또렷)
    "Aoede",         # Breezy (산뜻·자연스러움)
    "Erinome",       # Clear (맑고 명료)
    "Despina",       # Smooth (부드럽고 매끄러움)
    "Sulafat",       # Warm (따뜻한 톤)
    "Vindemiatrix",  # Gentle (차분·온화)
    "Zephyr",        # Bright (밝고 경쾌)
    "Leda",          # Youthful (젊고 산뜻)
    "Callirrhoe",    # Easy-going (편안한 대화체)
    "Gacrux",        # Mature (차분·성숙)
)
VOICE_ROTATION_ANCHOR_DATE = date(2026, 11, 20)
# 공식 Gemini 3.8 TTS 가이드: 길고 복잡한 페르소나 지시문 대신 짧은 스타일(또는 공백)을 써야 음색 드리프트가 줄어듭니다.
DEFAULT_ANCHOR_STYLE = "calm, clear news narration"
GEMINI_TTS_MAX_PARTS = 7  # 무료 티어 일 10회(RPD) 한도 내 여유 확보 + 파트당 ~2분 이내로 음색 드리프트 최소화
GEMINI_TTS_MIN_INTERVAL_SECONDS = 22.0  # 무료 티어 분당 3회(RPM) 제한 준수 (요청 시작 간격 >= 22초)
SEGMENT_PAUSE_SECONDS = 0.6

_TAG_RE = re.compile(r"<[^>]+>")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+")


@dataclass
class GeneratedAudio:
    mp3_path: Path
    duration_seconds: int
    file_size_bytes: int
    voice: str
    model: str
    provider: str


def _get_wav_duration_seconds(wav_bytes: bytes) -> float:
    """WAV 바이트에서 재생 시간(초)을 계산합니다."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate > 0:
                return frames / rate
    except wave.Error:
        pass
    return 0.0


def _get_media_duration_seconds(media_path: Path) -> float:
    """ffprobe로 MP3 등 최종 오디오 파일의 정확한 재생 시간(초)을 조회합니다."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(media_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (OSError, ValueError):
        pass
    return 0.0


def _sanitize_plain_text(text: str) -> str:
    """TTS plain text에 불필요한 태그나 과도한 공백을 정리합니다."""
    cleaned = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def _split_text_by_utf8_bytes(
    text: str,
    max_bytes: int = CLOUD_TTS_MAX_UTF8_BYTES,
) -> list[str]:
    """UTF-8 바이트 길이가 max_bytes를 넘지 않도록 문장 단위로 안전하게 분할합니다."""
    cleaned = _sanitize_plain_text(text)
    if not cleaned:
        return []
    if len(cleaned.encode("utf-8")) <= max_bytes:
        return [cleaned]

    sentences = _SENTENCE_SPLIT_RE.split(cleaned)
    chunks: list[str] = []
    current = ""

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        candidate = f"{current} {sent}".strip() if current else sent
        if len(candidate.encode("utf-8")) <= max_bytes:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        # 단일 문장이 max_bytes를 넘는 극단적 경우 문자 단위로 절단
        if len(sent.encode("utf-8")) > max_bytes:
            buf = ""
            for ch in sent:
                if len((buf + ch).encode("utf-8")) > max_bytes:
                    if buf:
                        chunks.append(buf)
                    buf = ch
                else:
                    buf += ch
            current = buf
        else:
            current = sent

    if current:
        chunks.append(current)

    return chunks


def _pick_daily_gemini_voice(today: date | None = None) -> str:
    """날짜(KST)에 따라 매일 다른 Gemini TTS 여성 목소리를 순서대로 선택합니다."""
    current = today or datetime.now(KST).date()
    day_offset = (current - VOICE_ROTATION_ANCHOR_DATE).days
    return GEMINI_TTS_ROTATION_VOICES[day_offset % len(GEMINI_TTS_ROTATION_VOICES)]


def _normalize_voice_for_provider(voice: str | None, provider: str) -> str:
    """사용자가 드롭다운에서 다른 프로바이더용 보이스를 골라도 안전하게 보정합니다."""
    if provider == "edge_tts":
        raw = (voice or os.getenv("EDGE_TTS_VOICE") or DEFAULT_EDGE_TTS_VOICE).strip()
        if raw.endswith("Neural"):
            return raw
        return DEFAULT_EDGE_TTS_VOICE

    if provider == "google_cloud_tts":
        raw = (voice or os.getenv("GOOGLE_CLOUD_TTS_VOICE") or DEFAULT_CLOUD_TTS_VOICE).strip()
        if raw.lower() == "auto_rotate":
            return f"ko-KR-Chirp3-HD-{_pick_daily_gemini_voice()}"
        if raw.startswith("ko-KR-Chirp3-HD-") or raw.startswith("ko-KR-Neural2-") or raw.startswith("ko-KR-Wavenet-"):
            return raw
        if raw.endswith("Neural"):
            return DEFAULT_CLOUD_TTS_VOICE
        # Gemini 보이스 이름(Kore, Charon, Aoede 등)이 들어온 경우 Chirp3-HD로 자동 매핑
        return f"ko-KR-Chirp3-HD-{raw}"

    raw = (voice or os.getenv("GEMINI_TTS_VOICE") or DEFAULT_GEMINI_TTS_VOICE).strip()
    if raw.lower() == "auto_rotate":
        return _pick_daily_gemini_voice()
    if raw.startswith("ko-KR-Chirp3-HD-"):
        return raw.removeprefix("ko-KR-Chirp3-HD-")
    if raw.startswith("ko-KR-"):
        return _pick_daily_gemini_voice()
    return raw


EDGE_TTS_MAX_UTF8_BYTES = 900  # 약 280~300자 단위로 쪼개 Edge TTS 지연/타임아웃 방지
EDGE_TTS_INTER_CHUNK_DELAY = 1.0  # 동시 접속/연속 폭주로 인한 NoAudioReceived 방지


async def _synthesize_edge_tts_chunk_async(
    text: str,
    voice_name: str,
    rate: str = "+0%",
    max_retries: int = 6,
    timeout_sec: int = 60,
) -> bytes:
    """Microsoft Edge TTS로 짧은 청크(~280자)를 비동기 MP3 바이트로 합성합니다."""
    import edge_tts

    cleaned = _sanitize_plain_text(text)

    async def _run(target_text: str) -> bytes:
        communicate = edge_tts.Communicate(text=target_text, voice=voice_name, rate=rate)
        audio_buf = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                audio_buf.extend(chunk["data"])
        return bytes(audio_buf)

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        candidate_text = cleaned
        if attempt >= 3:
            candidate_text = re.sub(r"[^\w\s.,?!%·가-힣a-zA-Z0-9-]", " ", cleaned)
            candidate_text = re.sub(r"\s+", " ", candidate_text).strip()

        try:
            data = await asyncio.wait_for(_run(candidate_text), timeout=timeout_sec)
            if data:
                return data
            last_exc = RuntimeError(
                f"Edge TTS 응답이 비어 있습니다 (voice={voice_name})."
            )
        except Exception as exc:
            last_exc = exc

        if attempt < max_retries:
            wait_sec = 2.0 * attempt
            print(
                f"      [WARN] Edge TTS attempt {attempt}/{max_retries} failed "
                f"({type(last_exc).__name__}: {last_exc}). Retrying in {wait_sec:.1f}s..."
            )
            await asyncio.sleep(wait_sec)

    raise RuntimeError(
        f"Edge TTS 합성 실패 ({max_retries}회 재시도 후 중단, voice={voice_name}): {last_exc}"
    )


def _synthesize_edge_tts_all_segments(
    segments: list[tuple[str, str]],
    voice_name: str,
    rate: str = "+0%",
) -> list[bytes]:
    """모든 세그먼트를 ~280자 청크로 분할해 순차(1개씩, 1초 간격)로 안정적으로 합성합니다."""
    segment_chunks: list[list[str]] = []
    for _, text in segments:
        chunks = _split_text_by_utf8_bytes(text, max_bytes=EDGE_TTS_MAX_UTF8_BYTES)
        segment_chunks.append(chunks or [_sanitize_plain_text(text)])

    total_chunks = sum(len(c) for c in segment_chunks)
    print(
        f"      [Edge TTS] Synthesizing {len(segments)} segments "
        f"({total_chunks} chunks, sequential)..."
    )

    async def _run_all() -> list[list[bytes]]:
        results: list[list[bytes]] = []
        done_count = 0

        for s_idx, chunks in enumerate(segment_chunks):
            seg_bytes: list[bytes] = []
            label = segments[s_idx][0]
            for c_idx, chunk_text in enumerate(chunks):
                suffix = f" ({c_idx + 1}/{len(chunks)})" if len(chunks) > 1 else ""
                print(
                    f"      [Edge TTS] {s_idx + 1}/{len(segments)} {label}{suffix} "
                    f"({len(chunk_text)} chars)..."
                )
                mp3_bytes = await _synthesize_edge_tts_chunk_async(
                    text=chunk_text,
                    voice_name=voice_name,
                    rate=rate,
                )
                seg_bytes.append(mp3_bytes)
                done_count += 1
                if done_count < total_chunks:
                    await asyncio.sleep(EDGE_TTS_INTER_CHUNK_DELAY)
            results.append(seg_bytes)
        return results

    chunked_results = asyncio.run(_run_all())
    # 같은 세그먼트 내부 청크는 MP3 프레임을 바로 이어 붙이고, 세그먼트 간에만 0.6초 무음 삽입
    return [b"".join(chunks) for chunks in chunked_results]


CLOUD_TTS_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _get_cloud_tts_auth_headers(
    credentials_json: str | None = None,
) -> dict[str, str]:
    """Service Account JSON으로 Google Cloud TTS OAuth2 Bearer 토큰 헤더를 발급합니다."""
    import json
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import service_account

    raw = (
        credentials_json
        or os.getenv("GOOGLE_CLOUD_TTS_CREDENTIALS_JSON")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    )
    if not raw or not raw.strip():
        raise ValueError(
            "GOOGLE_CLOUD_TTS_CREDENTIALS_JSON 환경변수가 필요합니다. "
            "Google Cloud Console에서 서비스 계정 JSON 키를 발급해 GitHub Secrets에 등록해 주세요."
        )

    raw = raw.strip()
    if raw.startswith("{"):
        info = json.loads(raw)
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=CLOUD_TTS_SCOPES,
        )
        project_id = info.get("project_id")
    else:
        creds = service_account.Credentials.from_service_account_file(
            raw,
            scopes=CLOUD_TTS_SCOPES,
        )
        project_id = getattr(creds, "project_id", None)

    creds.refresh(GoogleAuthRequest())
    if not creds.token:
        raise RuntimeError("Google Cloud OAuth2 액세스 토큰 발급에 실패했습니다.")

    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    if project_id:
        headers["x-goog-user-project"] = str(project_id)
    return headers


def _synthesize_cloud_tts_chunk(
    text: str,
    voice_name: str,
    auth_headers: dict[str, str],
    timeout: int = 45,
) -> bytes:
    """Google Cloud Text-to-Speech REST API로 단일 청크를 LINEAR16 WAV로 합성합니다."""
    language_code = "-".join(voice_name.split("-")[:2]) if "-" in voice_name else "ko-KR"
    payload = {
        "input": {"text": text},
        "voice": {
            "languageCode": language_code,
            "name": voice_name,
        },
        "audioConfig": {
            "audioEncoding": "LINEAR16",
            "sampleRateHertz": 24000,
        },
    }

    resp = requests.post(
        CLOUD_TTS_ENDPOINT,
        headers=auth_headers,
        json=payload,
        timeout=timeout,
    )

    if not resp.ok:
        hint = ""
        if resp.status_code in (401, 403):
            hint = (
                " (Google Cloud Console에서 'Cloud Text-to-Speech API'가 "
                "사용 설정되어 있는지, 서비스 계정 JSON 키가 올바른지 확인해 주세요.)"
            )
        raise RuntimeError(
            f"Google Cloud TTS API 호출 실패 ({resp.status_code}): "
            f"{resp.text[:400]}{hint}"
        )

    audio_b64 = resp.json().get("audioContent")
    if not audio_b64:
        raise RuntimeError("Google Cloud TTS 응답에 audioContent가 비어 있습니다.")
    return base64.b64decode(audio_b64)


def _group_segments_balanced(
    segments: list[tuple[str, str]],
    max_parts: int = GEMINI_TTS_MAX_PARTS,
) -> list[str]:
    """세그먼트 순서를 유지하면서 최대 max_parts개의 균형 잡힌 파트(~2분 내외)로 묶습니다."""
    texts = [text.strip() for _, text in segments if text and text.strip()]
    if not texts:
        return []
    n = len(texts)
    k = min(n, max(1, max_parts))
    if k == n:
        return texts

    # 누적 길이로 DP: 연속 분할 시 가장 긴 파트의 글자 수를 최소화
    prefix = [0] * (n + 1)
    for i, t in enumerate(texts):
        prefix[i + 1] = prefix[i] + len(t)

    inf = 10**9
    dp = [[inf] * (n + 1) for _ in range(k + 1)]
    split = [[0] * (n + 1) for _ in range(k + 1)]
    dp[0][0] = 0

    for p in range(1, k + 1):
        for i in range(p, n - (k - p) + 1):
            for j in range(p - 1, i):
                cost = max(dp[p - 1][j], prefix[i] - prefix[j])
                if cost < dp[p][i]:
                    dp[p][i] = cost
                    split[p][i] = j

    bounds: list[tuple[int, int]] = []
    cur = n
    for p in range(k, 0, -1):
        prev = split[p][cur]
        bounds.append((prev, cur))
        cur = prev
    bounds.reverse()

    return ["\n\n".join(texts[s:e]) for s, e in bounds]


def _synthesize_gemini_tts_part(
    client: genai.Client,
    text: str,
    model: str,
    voice: str,
    style: str,
    max_retries: int = 4,
) -> tuple[bytes, genai.Client]:
    """Gemini TTS로 단일 파트를 WAV 음성 합성합니다 (무료 한도 소진 시 유료 키 자동 전환 포함)."""
    part_payload: dict[str, object] = {
        "type": "text",
        "text": text.strip(),
    }
    if style and style.strip():
        part_payload["annotations"] = [
            {
                "type": "speech_metadata",
                "style": style.strip(),
            }
        ]

    active_client = client
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            interaction = active_client.interactions.create(
                model=model,
                input=[part_payload],
                response_format={"type": "audio"},
                generation_config={
                    "speech_config": [
                        {"voice": voice},
                    ]
                },
                store=False,
            )

            output_audio = getattr(interaction, "output_audio", None)
            raw_data = getattr(output_audio, "data", None) if output_audio is not None else None
            if not raw_data:
                raise RuntimeError(
                    "Gemini TTS API 응답에서 오디오 데이터를 찾지 못했습니다."
                )

            if isinstance(raw_data, bytes):
                return raw_data, active_client
            if isinstance(raw_data, str):
                return base64.b64decode(raw_data), active_client
            raise TypeError(f"예상하지 못한 오디오 데이터 타입입니다: {type(raw_data)}")
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                if is_rate_limit_error(exc):
                    if attempt >= 2:
                        paid_key = try_switch_to_paid_key()
                        if paid_key:
                            active_client = genai.Client(
                                api_key=paid_key,
                                http_options={"api_version": "v1beta"},
                            )
                            continue
                    wait_sec = 22
                else:
                    wait_sec = 5 * attempt
                print(
                    f"      [WARN] Gemini TTS attempt {attempt}/{max_retries} failed "
                    f"({type(exc).__name__}: {exc}). Retrying in {wait_sec}s..."
                )
                time.sleep(wait_sec)

    raise RuntimeError(f"Gemini TTS 합성 {max_retries}회 실패: {last_exc}")


def _concat_audio_parts_to_mp3(
    audio_parts: list[bytes],
    output_mp3: Path,
    input_suffix: str = ".wav",
    pause_seconds: float = SEGMENT_PAUSE_SECONDS,
) -> None:
    """여러 오디오 파트(WAV/MP3)를 자연스러운 무음 간격으로 이어 붙여 단일 MP3로 인코딩합니다."""
    if not audio_parts:
        raise ValueError("인코딩할 오디오 파트가 없습니다.")

    output_mp3.parent.mkdir(parents=True, exist_ok=True)
    tmp_paths: list[Path] = []

    try:
        for audio_bytes in audio_parts:
            with tempfile.NamedTemporaryFile(suffix=input_suffix, delete=False) as tmp_file:
                tmp_file.write(audio_bytes)
                tmp_paths.append(Path(tmp_file.name))

        if len(tmp_paths) == 1:
            cmd = [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(tmp_paths[0]),
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "96k",
                "-ar",
                "24000",
                "-ac",
                "1",
                str(output_mp3),
            ]
        else:
            cmd = ["ffmpeg", "-y", "-loglevel", "error"]
            for path in tmp_paths:
                cmd.extend(["-i", str(path)])

            filter_segments: list[str] = []
            concat_inputs: list[str] = []
            for idx in range(len(tmp_paths)):
                norm_label = f"a{idx}"
                filter_segments.append(
                    f"[{idx}:a]aresample=24000,aformat=channel_layouts=mono[{norm_label}]"
                )
                concat_inputs.append(f"[{norm_label}]")
                if idx < len(tmp_paths) - 1:
                    silence_label = f"sil{idx}"
                    filter_segments.append(
                        f"anullsrc=r=24000:cl=mono:d={pause_seconds}[{silence_label}]"
                    )
                    concat_inputs.append(f"[{silence_label}]")

            total_streams = len(concat_inputs)
            filter_segments.append(
                f"{''.join(concat_inputs)}concat=n={total_streams}:v=0:a=1[out]"
            )
            filter_complex = ";".join(filter_segments)

            cmd.extend(
                [
                    "-filter_complex",
                    filter_complex,
                    "-map",
                    "[out]",
                    "-codec:a",
                    "libmp3lame",
                    "-b:a",
                    "96k",
                    "-ar",
                    "24000",
                    "-ac",
                    "1",
                    str(output_mp3),
                ]
            )

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg MP3 변환 실패 (exit {result.returncode}): {result.stderr.strip()}"
            )
    finally:
        for path in tmp_paths:
            path.unlink(missing_ok=True)


def generate_audio_briefing(
    script: RoundupAudioScript,
    output_mp3: Path,
    tts_provider: str | None = None,
    voice: str | None = None,
    tts_model: str | None = None,
    style: str | None = None,
) -> GeneratedAudio:
    """라운드업 대본을 단일 앵커 목소리로 합성해 하나의 MP3 파일로 결합합니다."""
    provider = (tts_provider or os.getenv("TTS_PROVIDER") or DEFAULT_TTS_PROVIDER).strip().lower()
    if provider not in ("edge_tts", "google_cloud_tts", "gemini_tts"):
        raise ValueError(f"지원하지 않는 TTS 프로바이더입니다: {provider}")

    segments = get_script_segments(script)
    if not segments:
        raise ValueError("음성으로 합성할 대본이 비어 있습니다.")

    selected_voice = _normalize_voice_for_provider(voice, provider)
    audio_parts: list[bytes] = []
    input_suffix = ".wav"
    total_duration = 0.0
    selected_model = ""

    if provider == "edge_tts":
        try:
            selected_model = "microsoft-edge-tts"
            input_suffix = ".mp3"
            audio_parts = _synthesize_edge_tts_all_segments(
                segments=segments,
                voice_name=selected_voice,
            )
        except Exception as edge_exc:
            if os.getenv("GOOGLE_CLOUD_TTS_CREDENTIALS_JSON") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
                fallback_voice = "ko-KR-Chirp3-HD-Charon" if "Hyunsu" in selected_voice or "InJoon" in selected_voice else DEFAULT_CLOUD_TTS_VOICE
                print(
                    f"      [WARN] Edge TTS failed ({edge_exc}). "
                    f"Falling back to Google Cloud TTS ({fallback_voice})..."
                )
                provider = "google_cloud_tts"
                selected_voice = fallback_voice
                audio_parts = []
                input_suffix = ".wav"
            else:
                raise

    if provider == "gemini_tts":
        try:
            selected_model = tts_model or os.getenv("GEMINI_TTS_MODEL", DEFAULT_GEMINI_TTS_MODEL)
            selected_style = style if style is not None else os.getenv("GEMINI_TTS_STYLE", DEFAULT_ANCHOR_STYLE)
            client = genai.Client(http_options={"api_version": "v1beta"})

            grouped_parts = _group_segments_balanced(segments, max_parts=GEMINI_TTS_MAX_PARTS)

            last_call_start = 0.0
            for idx, part_text in enumerate(grouped_parts, start=1):
                api_tier = (os.getenv("GEMINI_API_TIER") or "paid").strip().lower()
                min_interval = GEMINI_TTS_MIN_INTERVAL_SECONDS if api_tier == "free" else 0.0
                if idx > 1 and min_interval > 0:
                    elapsed = time.monotonic() - last_call_start
                    if elapsed < min_interval:
                        sleep_sec = min_interval - elapsed
                        print(
                            f"      [Gemini TTS] Free-tier RPM pacing: waiting {sleep_sec:.1f}s..."
                        )
                        time.sleep(sleep_sec)

                last_call_start = time.monotonic()
                print(
                    f"      [Gemini TTS] Part {idx}/{len(grouped_parts)} "
                    f"({len(part_text)} chars, model={selected_model}, voice={selected_voice})..."
                )
                wav_bytes, client = _synthesize_gemini_tts_part(
                    client=client,
                    text=part_text,
                    model=selected_model,
                    voice=selected_voice,
                    style=selected_style,
                )
                part_dur = _get_wav_duration_seconds(wav_bytes)
                audio_parts.append(wav_bytes)
                total_duration += part_dur
        except Exception as gemini_exc:
            if os.getenv("GOOGLE_CLOUD_TTS_CREDENTIALS_JSON") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
                fallback_voice = _normalize_voice_for_provider(selected_voice, "google_cloud_tts")
                print(
                    f"      [WARN] Gemini TTS failed ({gemini_exc}). "
                    f"Falling back to Google Cloud TTS ({fallback_voice})..."
                )
                provider = "google_cloud_tts"
                selected_voice = fallback_voice
                audio_parts = []
                total_duration = 0.0
                input_suffix = ".wav"
            else:
                raise

    if provider == "google_cloud_tts":
        auth_headers = _get_cloud_tts_auth_headers()
        selected_model = "google-cloud-tts"

        for idx, (label, text) in enumerate(segments, start=1):
            chunks = _split_text_by_utf8_bytes(text)
            for chunk_idx, chunk in enumerate(chunks, start=1):
                suffix = f" (part {chunk_idx}/{len(chunks)})" if len(chunks) > 1 else ""
                print(
                    f"      [Cloud TTS] {idx}/{len(segments)} {label}{suffix} "
                    f"({len(chunk)} chars, {len(chunk.encode('utf-8'))} bytes)..."
                )
                wav_bytes = _synthesize_cloud_tts_chunk(
                    text=chunk,
                    voice_name=selected_voice,
                    auth_headers=auth_headers,
                )
                part_dur = _get_wav_duration_seconds(wav_bytes)
                audio_parts.append(wav_bytes)
                total_duration += part_dur

    _concat_audio_parts_to_mp3(
        audio_parts=audio_parts,
        output_mp3=output_mp3,
        input_suffix=input_suffix,
    )

    probed_duration = _get_media_duration_seconds(output_mp3)
    if probed_duration > 0:
        final_duration = probed_duration
    else:
        if len(audio_parts) > 1:
            total_duration += SEGMENT_PAUSE_SECONDS * (len(audio_parts) - 1)
        final_duration = total_duration

    return GeneratedAudio(
        mp3_path=output_mp3,
        duration_seconds=max(1, round(final_duration)),
        file_size_bytes=output_mp3.stat().st_size,
        voice=selected_voice,
        model=selected_model,
        provider=provider,
    )
