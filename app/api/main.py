from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import tracing
from app.api import dispatcher, driver
from app.api.service import ShiftService
from app.voice.tts import ReplyAudio
from app.voice.stt import SpeechInput


def create_app(service: ShiftService | None = None) -> FastAPI:
    tracing.start()
    application = FastAPI(title="Sarathi", docs_url=None, redoc_url=None)
    application.state.shift = service if service is not None else ShiftService()
    application.state.reply_audio = ReplyAudio()
    application.state.speech_input = SpeechInput()
    application.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    application.include_router(driver.router)
    application.include_router(dispatcher.router)

    @application.get("/", include_in_schema=False)
    def home():
        return RedirectResponse("/driver", status_code=307)

    @application.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    return application


app = create_app()
