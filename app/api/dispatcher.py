from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app.api.service import MessageUnavailable
from app.api.views import context
from app.api.web import form_values, render

router = APIRouter()


def board(request, *, minute=None, error=None, notice=None):
    values = context(request.app.state.shift, minute=minute)
    values.update(error=error, notice=notice)
    return render(request, "dispatcher.html", "partials/board.html", values)


@router.get("/dispatcher")
@router.get("/dispatcher/board")
def dispatcher(request: Request, minute: int | None = Query(None, ge=0, le=600)):
    return board(request, minute=minute)


@router.post("/dispatcher/clock")
async def advance(request: Request):
    form = await form_values(request)
    if form.get("minutes") not in {"1", "15"}:
        raise HTTPException(422, "Advance by 1 or 15 minutes")
    request.app.state.shift.advance(int(form["minutes"]))
    return board(request)


@router.post("/dispatcher/approvals/{draft_id}")
async def review(request: Request, draft_id: str):
    form = await form_values(request)
    try:
        request.app.state.shift.review(draft_id, form.get("action"))
    except KeyError:
        raise HTTPException(404, "Draft not found") from None
    except ValueError as exc:
        return board(request, error=str(exc))
    return board(request, notice="Review saved. Customer messages are not sent by this demo.")


@router.post("/dispatcher/exceptions/{exception_id}/assess")
async def assess(request: Request, exception_id: str):
    try:
        await run_in_threadpool(request.app.state.shift.assess_exception, exception_id)
    except KeyError:
        raise HTTPException(404, "Exception not found") from None
    except MessageUnavailable as exc:
        return board(request, error=str(exc))
    return board(request, notice="Reply checked. The driver can now read the reply and its sources.")
