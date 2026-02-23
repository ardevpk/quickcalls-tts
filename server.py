import io
import os
import time
import logging
from contextlib import asynccontextmanager
from typing import List, Optional

import torch
import torchaudio
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from huggingface_hub import hf_hub_download

# Disable Triton compilation for stability
os.environ["NO_TORCH_COMPILE"] = "1"

from generator import load_csm_1b, Generator, Segment

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("csm-server")

# ---------------------------------------------------------------------------
# Speaker prompts – downloaded once at startup
# ---------------------------------------------------------------------------

SPEAKER_PROMPTS: dict = {}


def _load_speaker_prompts(sample_rate: int) -> dict:
    """Download and cache the default CSM speaker prompts from HuggingFace."""
    prompt_a_path = hf_hub_download(repo_id="sesame/csm-1b", filename="prompts/conversational_a.wav")
    prompt_b_path = hf_hub_download(repo_id="sesame/csm-1b", filename="prompts/conversational_b.wav")

    def _load(path: str) -> torch.Tensor:
        audio, sr = torchaudio.load(path)
        audio = audio.squeeze(0)
        audio = torchaudio.functional.resample(audio, orig_freq=sr, new_freq=sample_rate)
        return audio

    return {
        "conversational_a": {
            "speaker": 0,
            "text": (
                "like revising for an exam I'd have to try and like keep up the momentum because I'd "
                "start really early I'd be like okay I'm gonna start revising now and then like "
                "you're revising for ages and then I just like start losing steam I didn't do that "
                "for the exam we had recently to be fair that was a more of a last minute scenario "
                "but like yeah I'm trying to like yeah I noticed this yesterday that like Mondays I "
                "sort of start the day with this not like a panic but like a"
            ),
            "audio": _load(prompt_a_path),
        },
        "conversational_b": {
            "speaker": 1,
            "text": (
                "like a super Mario level. Like it's very like high detail. And like, once you get "
                "into the park, it just like, everything looks like a computer game and they have all "
                "these, like, you know, if, if there's like a, you know, like in a Mario game, they "
                "will have like a question block. And if you like, you know, punch it, a coin will "
                "come out. So like everyone, when they come into the park, they get like this little "
                "bracelet and then you can go punching question blocks around."
            ),
            "audio": _load(prompt_b_path),
        },
    }


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

generator: Optional[Generator] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global generator, SPEAKER_PROMPTS

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(f"Loading CSM 1B on {device} …")
    t0 = time.time()
    generator = load_csm_1b(device)
    logger.info(f"CSM loaded in {time.time() - t0:.1f}s  (sample_rate={generator.sample_rate})")

    SPEAKER_PROMPTS = _load_speaker_prompts(generator.sample_rate)
    logger.info(f"Loaded {len(SPEAKER_PROMPTS)} speaker prompts")

    yield

    generator = None
    logger.info("CSM server shut down")


app = FastAPI(title="CSM TTS Server", version="1.0.0", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="Text to synthesize")
    speaker: int = Field(default=0, ge=0, le=1, description="Speaker ID (0 or 1)")
    max_audio_length_ms: float = Field(default=10_000, ge=1000, le=90_000, description="Max audio length in ms")
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    topk: int = Field(default=50, ge=1, le=1000)


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    device: str
    sample_rate: int
    gpu_available: bool


class VoiceInfo(BaseModel):
    id: str
    speaker: int
    description: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/tts")
async def tts(req: TTSRequest):
    if generator is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    prompt_key = "conversational_a" if req.speaker == 0 else "conversational_b"
    prompt = SPEAKER_PROMPTS[prompt_key]
    context = [Segment(speaker=prompt["speaker"], text=prompt["text"], audio=prompt["audio"])]

    try:
        t0 = time.time()
        audio = generator.generate(
            text=req.text,
            speaker=req.speaker,
            context=context,
            max_audio_length_ms=req.max_audio_length_ms,
            temperature=req.temperature,
            topk=req.topk,
        )
        elapsed = time.time() - t0
        logger.info(f"Generated {audio.shape[-1] / generator.sample_rate:.2f}s audio in {elapsed:.2f}s")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Generation failed")
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")

    buf = io.BytesIO()
    torchaudio.save(buf, audio.unsqueeze(0).cpu(), generator.sample_rate, format="wav")
    buf.seek(0)

    return Response(content=buf.read(), media_type="audio/wav", headers={"X-Audio-Duration": f"{audio.shape[-1] / generator.sample_rate:.3f}"})


@app.get("/health", response_model=HealthResponse)
async def health():
    gpu = torch.cuda.is_available()
    loaded = generator is not None
    return HealthResponse(
        status="ok" if loaded else "loading",
        model_loaded=loaded,
        device=str(generator.device) if loaded else ("cuda" if gpu else "cpu"),
        sample_rate=generator.sample_rate if loaded else 24000,
        gpu_available=gpu,
    )


@app.get("/voices", response_model=List[VoiceInfo])
async def voices():
    return [
        VoiceInfo(id="conversational_a", speaker=0, description="Conversational voice A (female)"),
        VoiceInfo(id="conversational_b", speaker=1, description="Conversational voice B (male)"),
    ]
