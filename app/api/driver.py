from uuid import UUID

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from app.api.service import MessageUnavailable
from app.api.views import context
from app.api.web import form_values, render

router = APIRouter()


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
