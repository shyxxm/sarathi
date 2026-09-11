from pathlib import Path
from urllib.parse import parse_qs

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["rupees"] = lambda paise: f"₹{paise / 100:,.2f}"


async def form_values(request: Request):
    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Use a text form")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16_384:
            raise HTTPException(413, "Message is too long")
    try:
        values = parse_qs(body.decode("utf-8"), max_num_fields=10)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "Invalid form") from None
    return {key: entries[-1] for key, entries in values.items()}


def render(request, page, partial, values, *, headers=None):
    template = partial if request.headers.get("HX-Request") == "true" else page
    return templates.TemplateResponse(request=request, name=template, context=values, headers=headers)
