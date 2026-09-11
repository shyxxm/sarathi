from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.api.service import MessageUnavailable
from app.api.views import context
from app.api.web import form_values, render

router = APIRouter()


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
