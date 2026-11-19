"""Gemini 3.8 Flash TTS로 1인 앵커 한 목소리를 고정해 롱폼 MP3 오디오를 생성합니다."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import io
import os
from pathlib import Path
import subprocess
import tempfile
import wave

from google import genai

from script_writer import LongformAudioScript


DEFAULT_TTS_MODEL = "gemini-3.8-flash-tts"
DEFAULT_TTS_VOICE = "Kore"
# 전 구간에 동일한 짧은 스타일을 고정해 섹션 간 목소리 변동을 방지합니다.
DEFAULT_ANCHOR_STYLE = (
    "calm, warm, articulate Korean radio news anchor, steady relaxed pace"
)
PART_PAUSE_SECONDS = 0.7


@dataclass
class GeneratedAudio:
    mp3_path: Path
    duration_seconds: int
    file_size_bytes: int
    voice: str
    model: str


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


def _synthesize_single_part(
    client: genai.Client,
    text: str,
    model: str,
    voice: str,
    style: str,
) -> bytes:
    """단일 파트를 동일한 목소리와 고정 스타일로 WAV 음성 합성합니다."""
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


def _concat_wavs_to_mp3(
    wav_parts: list[bytes],
    output_mp3: Path,
    pause_seconds: float = PART_PAUSE_SECONDS,
) -> None:
    """여러 WAV 파트를 자연스러운 무음 간격으로 이어 붙여 단일 MP3로 인코딩합니다."""
    if not wav_parts:
        raise ValueError("인코딩할 WAV 오디오 파트가 없습니다.")

    output_mp3.parent.mkdir(parents=True, exist_ok=True)
    tmp_paths: list[Path] = []

    try:
        for wav_bytes in wav_parts:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                tmp_wav.write(wav_bytes)
                tmp_paths.append(Path(tmp_wav.name))

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

            # 각 파트 사이에 짧은 무음(pause_seconds) 삽입
            filter_segments: list[str] = []
            concat_inputs: list[str] = []
            for idx in range(len(tmp_paths)):
                concat_inputs.append(f"[{idx}:a]")
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
    script: LongformAudioScript,
    output_mp3: Path,
    voice: str | None = None,
    tts_model: str | None = None,
    style: str | None = None,
) -> GeneratedAudio:
    """Part 1과 Part 2를 동일한 목소리로 합성해 하나의 MP3 파일로 결합합니다."""
    selected_model = tts_model or os.getenv("GEMINI_TTS_MODEL", DEFAULT_TTS_MODEL)
    selected_voice = voice or os.getenv("GEMINI_TTS_VOICE", DEFAULT_TTS_VOICE)
    selected_style = style or os.getenv("GEMINI_TTS_STYLE", DEFAULT_ANCHOR_STYLE)

    parts_text = [
        text.strip()
        for text in (script.part1_text, script.part2_text)
        if text and text.strip()
    ]
    if not parts_text:
        raise ValueError("음성으로 합성할 대본이 비어 있습니다.")

    client = genai.Client(http_options={"api_version": "v1beta"})
    wav_parts: list[bytes] = []
    total_duration = 0.0

    for idx, text in enumerate(parts_text, start=1):
        print(f"      Synthesizing TTS part {idx}/{len(parts_text)} ({len(text)} chars)...")
        wav_bytes = _synthesize_single_part(
            client=client,
            text=text,
            model=selected_model,
            voice=selected_voice,
            style=selected_style,
        )
        part_dur = _get_wav_duration_seconds(wav_bytes)
        print(f"      Part {idx} ready ({part_dur:.1f}s).")
        wav_parts.append(wav_bytes)
        total_duration += part_dur

    if len(wav_parts) > 1:
        total_duration += PART_PAUSE_SECONDS * (len(wav_parts) - 1)

    _concat_wavs_to_mp3(wav_parts, output_mp3)

    return GeneratedAudio(
        mp3_path=output_mp3,
        duration_seconds=max(1, round(total_duration)),
        file_size_bytes=output_mp3.stat().st_size,
        voice=selected_voice,
        model=selected_model,
    )
