import json
import os
import urllib.parse
from functools import partial
from typing import Any
from typing import Optional

import requests
from fastapi import Depends
from fastapi import Header
from fastapi import HTTPException
from iterative_telemetry import IterativeTelemetryLogger
from pydantic import BaseModel
from pydantic import parse_obj_as
from starlette.responses import JSONResponse
from typing_extensions import Annotated

import evidently
from evidently.ui.config import Configuration
from evidently.ui.config import get_configuration
from evidently.utils import NumpyEncoder

SECRET = os.environ.get("EVIDENTLY_SECRET", None)

_event_logger = None


def event_logger(
    config: Configuration = Depends(get_configuration),
):
    global _event_logger
    if _event_logger is None:
        _event_logger = IterativeTelemetryLogger(
            config.telemetry.tool_name,
            evidently.__version__,
            url=config.telemetry.url,
            token=config.telemetry.token,
            enabled=config.telemetry.enabled,
        )
    yield partial(_event_logger.send_event, config.telemetry.service_name)


def set_secret(secret: Optional[str]):
    global SECRET
    SECRET = secret


async def authenticated(evidently_secret: Annotated[Optional[str], Header()] = None):
    if SECRET is not None and evidently_secret != SECRET:
        raise HTTPException(403, "Not allowed")


class RemoteClientBase:
    def __init__(self, base_url: str, secret: str = None):
        self.base_url = base_url
        self.secret = secret

    def _request(
        self,
        path: str,
        method: str,
        query_params: Optional[dict] = None,
        body: Optional[dict] = None,
        response_model=None,
    ):
        # todo: better encoding
        headers = {"evidently-secret": self.secret}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"

            data = json.dumps(body, allow_nan=True, cls=NumpyEncoder).encode("utf8")

        response = requests.request(
            method, urllib.parse.urljoin(self.base_url, path), params=query_params, data=data, headers=headers
        )
        response.raise_for_status()
        if response_model is not None:
            return parse_obj_as(response_model, response.json())
        return response


class NumpyJsonResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(
            content, ensure_ascii=False, allow_nan=True, indent=None, separators=(",", ":"), cls=NumpyEncoder
        ).encode("utf-8")


_skip_jsonable_encoder_cache = {}


def skip_jsonable_encoder(f):
    """Decorator to change route's return model so that it does not call `jsonable_encoder` on response content
    It is needed for routes that can return invalid json produced with NumpyEncoder
    Should be used with response_class=NumpyJsonResponse"""
    return_model = f.__annotations__["return"]
    if not isinstance(return_model, type) or not issubclass(return_model, BaseModel):
        raise ValueError("Can skip jsonable encoder only for BaseModel return model")
    # we generete new type derived from original type with `json_encoders` field in Config class
    # this encoder is called on 2nd iteration of jsonable_encoder called from fastapi.routing.serialize_response
    # 1st one creates dict from BaseModel with `.dict` and passes model's `json_encoders` to subsequent `jsonable_encoder` calls
    # On 2nd call it gets a dict from model and short-circuites with our custom encoder for dict and returns immediately
    if return_model not in _skip_jsonable_encoder_cache:
        new_return_model = type(
            return_model.__name__,
            (return_model,),
            {"Config": type("Config", tuple(), {"json_encoders": {dict: lambda x: x}})},
        )
        _skip_jsonable_encoder_cache[return_model] = new_return_model
    f.__annotations__["return"] = _skip_jsonable_encoder_cache[return_model]
    return f
