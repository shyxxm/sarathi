from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app.api.service import MessageUnavailable
from app.api.views import context
from app.api.web import form_values, render
from app.voice.stt import MAX_AUDIO_BYTES, MEDIA_TYPES, STTUnavailable

router = APIRouter()


@router.post("/driver/voice-messages/{message_id}")
async def voice_message(message_id: UUID, request: Request):
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type not in MEDIA_TYPES:
        return JSONResponse({"error": "That audio format is not supported. Please type your message."}, status_code=415)
    audio = bytearray()
    async for chunk in request.stream():
        audio.extend(chunk)
        if len(audio) > MAX_AUDIO_BYTES:
            return JSONResponse({"error": "The recording is too large. Please record a shorter note or type."}, status_code=413)
    if not audio:
        return JSONResponse({"error": "The recording was empty. Nothing was submitted. Please type or record again."}, status_code=422)
    service = request.app.state.shift
    try:
        await run_in_threadpool(service.submit_voice, bytes(audio), media_type, str(message_id),
                                request.app.state.speech_input)
    except STTUnavailable as error:
        return JSONResponse({"error": str(error)}, status_code=503)
    except MessageUnavailable as error:
        return JSONResponse({"error": str(error)}, status_code=409)
    return {"reply_id": f"message-{message_id}"}


@router.get("/driver/replies/{reply_id}/audio")
def reply_audio(reply_id: str, request: Request):
    service = request.app.state.shift
    with service.lock:
        exchanges = [*service.recorded_exchanges(service.state), *service.exchanges]
        exchange = next((item for item in reversed(exchanges) if item.id == reply_id), None)
        parent = service.reply_trace_parents.get(reply_id)
    if exchange is None:
        raise HTTPException(404, "Reply not found")
    # Separate request, after the text is on screen. No shift lock during I/O.
    rendered = request.app.state.reply_audio.render(reply_id, exchange.reply, parent=parent)
    if rendered.audio is None:
        return Response(status_code=204)
    return Response(rendered.audio, media_type="audio/wav")


@router.get("/driver")
@router.get("/driver/updates")
def driver(request: Request):
    return render(request, "driver.html", "partials/driver_updates.html",
                  context(request.app.state.shift))


@router.post("/driver/messages")
async def message(request: Request):
    form = await form_values(request)
    text = form.get("text", "").strip()
    message_id = form.get("message_id", "")
    error = None
    try:
        UUID(message_id)
    except ValueError:
        error = "Please reload the page before sending your message."
    if not text or len(text) > 2000:
        error = "Write a message between 1 and 2,000 characters."
    if not error:
        try:
            await run_in_threadpool(request.app.state.shift.submit, text, message_id)
        except MessageUnavailable as exc:
            error = str(exc)
    values = context(request.app.state.shift)
    values.update(error=error, typed_text=text if error else "")
    if error and message_id:
        values["message_id"] = message_id
    return render(request, "driver.html", "partials/message_result.html", values,
                  headers={"HX-Trigger": "shiftUpdated"} if not error else None)
