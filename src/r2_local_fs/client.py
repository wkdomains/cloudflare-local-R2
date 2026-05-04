from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, quote
from urllib.request import Request, urlopen

from .errors import ApiError
from .models import R2Object


@dataclass
class LocalExplorerClient:
    endpoint: str = "http://localhost:8787"
    timeout: float = 30.0

    @property
    def api_base(self) -> str:
        return self.endpoint.rstrip("/") + "/cdn-cgi/explorer/api/"

    def list_buckets(self) -> list[str]:
        payload = self._json("GET", "r2/buckets")
        result = payload.get("result") or []
        if isinstance(result, dict):
            result = result.get("buckets") or []
        buckets = []
        for item in result:
            if isinstance(item, str):
                buckets.append(item)
            elif isinstance(item, dict):
                name = item.get("name") or item.get("bucket") or item.get("bucket_name")
                if name:
                    buckets.append(str(name))
        return buckets

    def list_objects(self, bucket: str, prefix: str = "") -> list[R2Object]:
        objects: list[R2Object] = []
        cursor: str | None = None

        while True:
            params: dict[str, str | int] = {"per_page": 1000}
            if prefix:
                params["prefix"] = prefix
            if cursor:
                params["cursor"] = cursor

            payload = self._json(
                "GET",
                f"r2/buckets/{quote(bucket, safe='')}/objects",
                params=params,
            )
            objects.extend(
                R2Object.from_api(item)
                for item in payload.get("result") or []
                if isinstance(item, dict)
            )

            info = payload.get("result_info") or {}
            if not isinstance(info, dict):
                break
            next_cursor = info.get("cursor") or info.get("next_cursor")
            truncated = str(info.get("is_truncated", "false")).lower() == "true"
            if not truncated or not next_cursor:
                break
            cursor = str(next_cursor)

        return objects

    def get_object(self, bucket: str, key: str) -> tuple[bytes, str | None]:
        response = self._request(
            "GET",
            f"r2/buckets/{quote(bucket, safe='')}/objects/{quote(key, safe='')}",
        )
        content_type = response.headers.get("content-type")
        return response.read(), content_type

    def put_object(
        self,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str = "application/octet-stream",
    ) -> R2Object:
        payload = self._json(
            "PUT",
            f"r2/buckets/{quote(bucket, safe='')}/objects/{quote(key, safe='')}",
            data=body,
            headers={"content-type": content_type},
        )
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            raise ApiError(f"unexpected PUT response for {key!r}: {payload!r}")
        return R2Object.from_api(
            {
                "key": result.get("key") or key,
                "size": result.get("size") or len(body),
                "etag": result.get("etag"),
                "last_modified": result.get("last_modified"),
            }
        )

    def delete_objects(self, bucket: str, keys: list[str]) -> None:
        if not keys:
            return
        self._json(
            "DELETE",
            f"r2/buckets/{quote(bucket, safe='')}/objects",
            data=json.dumps(keys).encode("utf-8"),
            headers={"content-type": "application/json"},
        )

    def _json(
        self,
        method: str,
        path: str,
        params: dict[str, str | int] | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = self._request(method, path, params=params, data=data, headers=headers)
        raw = response.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ApiError(f"Local Explorer returned invalid JSON for {path}") from exc

        if isinstance(payload, dict) and payload.get("success") is False:
            raise ApiError(f"Local Explorer request failed: {payload!r}")
        if not isinstance(payload, dict):
            raise ApiError(f"Local Explorer returned unexpected payload: {payload!r}")
        return payload

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str | int] | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ):
        url = urljoin(self.api_base, path)
        if params:
            url += "?" + urlencode(params)
        request = Request(url, data=data, method=method, headers=headers or {})
        try:
            return urlopen(request, timeout=self.timeout)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ApiError(f"{method} {url} failed with HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise ApiError(f"{method} {url} failed: {exc.reason}") from exc
