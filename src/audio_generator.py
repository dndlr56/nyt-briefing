"""Gemini 3.8 Flash TTS로 방송 대본을 음성 합성하고 MP3로 변환합니다."""

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

from script_writer import AudioBriefingScript


DEFAULT_TTS_MODEL = "gemini-3.8-flash-tts"
DEFAULT_TTS_VOICE = "Kore"


@dataclass
class GeneratedAudio:
    mp3_path: Path
    duration_seconds: int
    file_size_bytes: int
    voice: str
    model: str


def _get_wav_duration_seconds(wav_bytes: bytes) -> int:
    """WAV 바이트에서 재생 시간(초)을 계산합니다."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate > 0:
                return max(1, round(frames / rate))
    except wave.Error:
        pass
    return 0


def _convert_wav_to_mp3(wav_bytes: bytes, output_mp3: Path) -> None:
    """ffmpeg를 사용해 WAV 바이트를 텔레그램 스트리밍용 MP3로 변환합니다."""
    output_mp3.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        tmp_wav.write(wav_bytes)
        tmp_wav_path = Path(tmp_wav.name)

    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(tmp_wav_path),
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
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg MP3 변환 실패 (exit {result.returncode}): {result.stderr.strip()}"
            )
    finally:
        tmp_wav_path.unlink(missing_ok=True)


def generate_audio_briefing(
    script: AudioBriefingScript,
    output_mp3: Path,
    voice: str | None = None,
    tts_model: str | None = None,
) -> GeneratedAudio:
    """AudioBriefingScript를 Gemini 3.8 TTS로 합성해 MP3 파일로 저장합니다."""
    selected_model = tts_model or os.getenv("GEMINI_TTS_MODEL", DEFAULT_TTS_MODEL)
    selected_voice = voice or os.getenv("GEMINI_TTS_VOICE", DEFAULT_TTS_VOICE)

    client = genai.Client(http_options={"api_version": "v1beta"})

    tts_inputs = [
        {
            "type": "text",
            "text": seg.text.strip(),
            "annotations": [
                {
                    "type": "speech_metadata",
                    "style": seg.style.strip(),
                }
            ],
        }
        for seg in script.segments
        if seg.text.strip()
    ]

    if not tts_inputs:
        raise ValueError("음성으로 합성할 대본 세그먼트가 비어 있습니다.")

    interaction = client.interactions.create(
        model=selected_model,
        input=tts_inputs,
        response_format={"type": "audio"},
        generation_config={
            "speech_config": [
                {"voice": selected_voice},
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
        wav_bytes = raw_data
    elif isinstance(raw_data, str):
        wav_bytes = base64.b64decode(raw_data)
    else:
        raise TypeError(f"예상하지 못한 오디오 데이터 타입입니다: {type(raw_data)}")

    duration_seconds = _get_wav_duration_seconds(wav_bytes)
    _convert_wav_to_mp3(wav_bytes, output_mp3)

    return GeneratedAudio(
        mp3_path=output_mp3,
        duration_seconds=duration_seconds,
        file_size_bytes=output_mp3.stat().st_size,
        voice=selected_voice,
        model=selected_model,
    )
