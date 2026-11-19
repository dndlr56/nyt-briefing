"""Edge TTS / Google Cloud TTS(무료 티어) / Gemini TTS로 단일 앵커 MP3 오디오를 생성합니다."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import io
import os
from pathlib import Path
import re
import subprocess
import tempfile
import wave

from google import genai
import requests

from script_writer import RoundupAudioScript, get_script_segments


CLOUD_TTS_ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"
CLOUD_TTS_MAX_UTF8_BYTES = 4200  # Google Cloud TTS 5,000바이트 제한에 여유 버퍼 확보

DEFAULT_TTS_PROVIDER = "edge_tts"
DEFAULT_EDGE_TTS_VOICE = "ko-KR-HyunsuMultilingualNeural"
DEFAULT_CLOUD_TTS_VOICE = "ko-KR-Chirp3-HD-Kore"
DEFAULT_GEMINI_TTS_MODEL = "gemini-3.8-flash-tts"
DEFAULT_GEMINI_TTS_VOICE = "Kore"
DEFAULT_ANCHOR_STYLE = (
    "calm, warm, articulate Korean radio news anchor, steady relaxed pace"
)
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


def _normalize_voice_for_provider(voice: str | None, provider: str) -> str:
    """사용자가 드롭다운에서 다른 프로바이더용 보이스를 골라도 안전하게 보정합니다."""
    if provider == "edge_tts":
        raw = (voice or os.getenv("EDGE_TTS_VOICE") or DEFAULT_EDGE_TTS_VOICE).strip()
        if raw.endswith("Neural"):
            return raw
        return DEFAULT_EDGE_TTS_VOICE

    if provider == "google_cloud_tts":
        raw = (voice or os.getenv("GOOGLE_CLOUD_TTS_VOICE") or DEFAULT_CLOUD_TTS_VOICE).strip()
        if raw.startswith("ko-KR-Chirp3-HD-") or raw.startswith("ko-KR-Neural2-") or raw.startswith("ko-KR-Wavenet-"):
            return raw
        if raw.endswith("Neural"):
            return DEFAULT_CLOUD_TTS_VOICE
        # Gemini 보이스 이름(Kore, Charon, Aoede 등)이 들어온 경우 Chirp3-HD로 자동 매핑
        return f"ko-KR-Chirp3-HD-{raw}"

    raw = (voice or os.getenv("GEMINI_TTS_VOICE") or DEFAULT_GEMINI_TTS_VOICE).strip()
    if raw.startswith("ko-KR-Chirp3-HD-"):
        return raw.removeprefix("ko-KR-Chirp3-HD-")
    if raw.startswith("ko-KR-"):
        return DEFAULT_GEMINI_TTS_VOICE
    return raw


def _synthesize_edge_tts_segment(
    text: str,
    voice_name: str,
    rate: str = "+0%",
    max_retries: int = 4,
) -> bytes:
    """Microsoft Edge TTS로 단일 세그먼트를 MP3 바이트로 합성합니다 (일시적 오류 자동 재시도 포함)."""
    import time
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
        # 재시도 시 혹시 모를 특수문자/기호 이슈를 피하기 위해 기본 문장부호만 남긴 텍스트로 정제
        candidate_text = cleaned
        if attempt >= 3:
            candidate_text = re.sub(r"[^\w\s.,?!%·가-힣a-zA-Z0-9-]", " ", cleaned)
            candidate_text = re.sub(r"\s+", " ", candidate_text).strip()

        try:
            data = asyncio.run(_run(candidate_text))
            if data:
                return data
            last_exc = RuntimeError(
                f"Edge TTS 응답이 비어 있습니다 (voice={voice_name})."
            )
        except Exception as exc:
            last_exc = exc

        if attempt < max_retries:
            wait_sec = 1.5 * (2 ** (attempt - 1))
            print(
                f"      [WARN] Edge TTS attempt {attempt}/{max_retries} failed "
                f"({type(last_exc).__name__}: {last_exc}). Retrying in {wait_sec:.1f}s..."
            )
            time.sleep(wait_sec)

    raise RuntimeError(
        f"Edge TTS 합성 실패 ({max_retries}회 재시도 후 중단, voice={voice_name}): {last_exc}"
    )


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


def _synthesize_gemini_tts_part(
    client: genai.Client,
    text: str,
    model: str,
    voice: str,
    style: str,
) -> bytes:
    """선택 옵션: Gemini TTS로 단일 파트를 WAV 음성 합성합니다."""
    interaction = client.interactions.create(
        model=model,
        input=[
            {
                "type": "text",
                "text": text.strip(),
                "annotations": [
                    {
                        "type": "speech_metadata",
                        "style": style,
                    }
                ],
            }
        ],
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
        return raw_data
    if isinstance(raw_data, str):
        return base64.b64decode(raw_data)
    raise TypeError(f"예상하지 못한 오디오 데이터 타입입니다: {type(raw_data)}")


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
                "128k",
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
                    "128k",
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

    if provider == "edge_tts":
        import time

        selected_model = "microsoft-edge-tts"
        input_suffix = ".mp3"
        for idx, (label, text) in enumerate(segments, start=1):
            print(f"      [Edge TTS] {idx}/{len(segments)} {label} ({len(text)} chars)...")
            mp3_bytes = _synthesize_edge_tts_segment(
                text=text,
                voice_name=selected_voice,
            )
            audio_parts.append(mp3_bytes)
            if idx < len(segments):
                time.sleep(0.5)
    elif provider == "google_cloud_tts":
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
    else:
        selected_model = tts_model or os.getenv("GEMINI_TTS_MODEL", DEFAULT_GEMINI_TTS_MODEL)
        selected_style = style or os.getenv("GEMINI_TTS_STYLE", DEFAULT_ANCHOR_STYLE)
        client = genai.Client(http_options={"api_version": "v1beta"})

        # Gemini TTS는 호출 횟수를 줄이기 위해 2개 파트로 균형 분할
        midpoint = max(1, len(segments) // 2)
        grouped_parts = [
            "\n\n".join(text for _, text in segments[:midpoint]),
            "\n\n".join(text for _, text in segments[midpoint:]),
        ]
        grouped_parts = [p for p in grouped_parts if p.strip()]

        for idx, part_text in enumerate(grouped_parts, start=1):
            print(f"      [Gemini TTS] Part {idx}/{len(grouped_parts)} ({len(part_text)} chars)...")
            wav_bytes = _synthesize_gemini_tts_part(
                client=client,
                text=part_text,
                model=selected_model,
                voice=selected_voice,
                style=selected_style,
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
