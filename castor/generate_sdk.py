"""
castor/generate_sdk.py — OpenAPI Python client SDK generator (issue #264).

Fetches the OpenAPI schema from a running gateway (or loads from a local
cache), then generates a typed Python client module at the specified output
path.

CLI::

    castor generate-sdk --lang python --output sdk/

Or programmatically::

    from castor.generate_sdk import SDKGenerator

    gen = SDKGenerator(base_url="http://localhost:8000")
    code = gen.generate()
    gen.write(code, output_dir="./sdk")

The generated ``sdk/client.py`` contains one method per API endpoint:
  - Typed arguments matching the endpoint's path/query parameters.
  - Returns ``dict`` (parsed JSON response).
  - Uses ``httpx`` when available, falls back to ``urllib.request`` (stdlib).

Cache: ``castor/openapi_cache.json`` — written after a successful fetch so
subsequent offline runs can still generate the client.
"""

from __future__ import annotations

import json
import logging
import textwrap
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.SDK.Generator")

_CACHE_PATH = Path(__file__).parent / "openapi_cache.json"

# ---------------------------------------------------------------------------
# Optional httpx guard
# ---------------------------------------------------------------------------

try:
    import httpx as _httpx

    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

# ---------------------------------------------------------------------------
# HTTP fetch helpers
# ---------------------------------------------------------------------------


def _fetch_openapi_schema(base_url: str, timeout: float = 10.0) -> dict:
    """Fetch ``/openapi.json`` from *base_url*.

    Uses httpx when available, otherwise urllib.request.

    Args:
        base_url: Gateway base URL (e.g. ``"http://localhost:8000"``).
        timeout:  Request timeout in seconds.

    Returns:
        Parsed OpenAPI schema dict.

    Raises:
        RuntimeError: When the schema cannot be fetched.
    """
    url = f"{base_url.rstrip('/')}/openapi.json"

    if HAS_HTTPX:
        try:
            resp = _httpx.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            raise RuntimeError(f"httpx fetch failed: {exc}") from exc

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        raise RuntimeError(f"urllib fetch failed: {exc}") from exc


def _load_cached_schema(cache_path: Path) -> Optional[dict]:
    """Load schema from the local cache file, or return None.

    Args:
        cache_path: Path to the JSON cache file.

    Returns:
        Parsed schema dict, or ``None`` if the file does not exist or is invalid.
    """
    if not cache_path.exists():
        return None
    try:
        with open(cache_path) as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning("Failed to load cached schema: %s", exc)
        return None


def _save_cached_schema(schema: dict, cache_path: Path) -> None:
    """Write *schema* to *cache_path*.

    Args:
        schema:     OpenAPI schema dict.
        cache_path: Destination path.
    """
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as fh:
            json.dump(schema, fh, indent=2)
    except Exception as exc:
        logger.warning("Failed to cache schema: %s", exc)


# ---------------------------------------------------------------------------
# Code generation helpers
# ---------------------------------------------------------------------------


def _method_name(method: str, path: str) -> str:
    """Derive a Python method name from an HTTP method + path.

    Args:
        method: HTTP verb (``"get"``, ``"post"``, etc.).
        path:   URL path (e.g. ``"/api/health"``).

    Returns:
        Snake-case method name (e.g. ``"get_api_health"``).
    """
    parts = [method.lower()]
    for segment in path.strip("/").split("/"):
        # Strip path parameters like {id}
        segment = segment.lstrip("{").rstrip("}")
        parts.append(segment.replace("-", "_"))
    name = "_".join(p for p in parts if p)
    # Sanitise: replace non-alnum chars with _
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name)


def _python_type(schema_type: str) -> str:
    """Map an OpenAPI type string to a Python type annotation.

    Args:
        schema_type: OpenAPI type string (``"string"``, ``"integer"``, etc.).

    Returns:
        Python type annotation string.
    """
    mapping = {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "array": "list",
        "object": "dict",
    }
    return mapping.get(schema_type, "Any")


def _generate_method(method: str, path: str, operation: dict) -> str:
    """Generate a Python method string for one API endpoint.

    Args:
        method:    HTTP verb (``"get"``, ``"post"``, etc.).
        path:      URL path template.
        operation: OpenAPI operation dict.

    Returns:
        Python method source code as a string.
    """
    name = _method_name(method, path)
    summary = operation.get("summary", "") or operation.get("operationId", name)
    params = operation.get("parameters", [])
    has_body = method.lower() in ("post", "put", "patch")

    # Build parameter list
    args = ["self"]
    path_params = []
    query_params = []

    for p in params:
        pname = p.get("name", "param").replace("-", "_")
        pschema = p.get("schema", {})
        ptype = _python_type(pschema.get("type", "string"))
        required = p.get("required", False)
        location = p.get("in", "query")

        if location == "path":
            args.append(f"{pname}: {ptype}")
            path_params.append(pname)
        elif location == "query":
            default = "None" if not required else ""
            if default:
                args.append(f"{pname}: Optional[{ptype}] = None")
            else:
                args.append(f"{pname}: {ptype}")
            query_params.append(pname)

    if has_body:
        args.append("body: Optional[dict] = None")

    args_str = ", ".join(args)

    # Build URL construction
    url_template = path
    for pp in path_params:
        url_template = url_template.replace("{" + pp + "}", f"{{{pp}}}")

    lines = [
        f"    def {name}({args_str}) -> dict:",
        f'        """{summary}."""',
    ]

    if path_params:
        lines.append(f'        url = f"{{self._base_url}}{url_template}"')
    else:
        lines.append(f'        url = f"{{self._base_url}}{path}"')

    if query_params:
        lines.append("        params = {k: v for k, v in {")
        for qp in query_params:
            lines.append(f'            "{qp}": {qp},')
        lines.append("        }.items() if v is not None}")
        if has_body:
            lines.append(
                f'        return self._request("{method.upper()}", url, params=params, json=body)'
            )
        else:
            lines.append(f'        return self._request("{method.upper()}", url, params=params)')
    elif has_body:
        lines.append(f'        return self._request("{method.upper()}", url, json=body)')
    else:
        lines.append(f'        return self._request("{method.upper()}", url)')

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SDK Generator
# ---------------------------------------------------------------------------


class SDKGenerator:
    """Generates a typed Python client from an OpenAPI schema.

    Args:
        base_url:   Gateway base URL.
        cache_path: Local JSON cache path.
        timeout:    HTTP request timeout.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        cache_path: Optional[Path] = None,
        timeout: float = 10.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._cache_path = cache_path or _CACHE_PATH
        self._timeout = timeout

    def fetch_schema(self) -> dict:
        """Fetch OpenAPI schema from gateway or local cache.

        Returns:
            Parsed OpenAPI schema dict.

        Raises:
            RuntimeError: When neither the live endpoint nor the cache is available.
        """
        try:
            schema = _fetch_openapi_schema(self._base_url, timeout=self._timeout)
            _save_cached_schema(schema, self._cache_path)
            logger.info("OpenAPI schema fetched from %s", self._base_url)
            return schema
        except Exception as exc:
            logger.warning("Live schema fetch failed: %s — trying cache", exc)

        cached = _load_cached_schema(self._cache_path)
        if cached:
            logger.info("Using cached OpenAPI schema from %s", self._cache_path)
            return cached

        raise RuntimeError(
            f"Cannot fetch OpenAPI schema from {self._base_url} and no cache found at {self._cache_path}"
        )

    def generate(self, schema: Optional[dict] = None) -> str:
        """Generate Python client source code from *schema*.

        Args:
            schema: OpenAPI schema dict.  When ``None``, fetches via :meth:`fetch_schema`.

        Returns:
            Python source code string for ``client.py``.
        """
        if schema is None:
            schema = self.fetch_schema()

        title = schema.get("info", {}).get("title", "OpenCastor")
        version = schema.get("info", {}).get("version", "unknown")
        paths: dict[str, Any] = schema.get("paths", {})

        header = textwrap.dedent(
            f'''\
            """
            Auto-generated OpenCastor API client.
            Title:   {title}
            Version: {version}
            Base URL: {self._base_url}

            DO NOT EDIT — regenerate with: castor generate-sdk
            """
            from __future__ import annotations

            import json
            import urllib.request
            from typing import Any, Optional

            try:
                import httpx as _httpx
                _HAS_HTTPX = True
            except ImportError:
                _HAS_HTTPX = False


            class CastorClient:
                """Typed Python client for the OpenCastor API.

                Args:
                    base_url: Gateway base URL (e.g. ``"http://localhost:8000"``).
                    token:    Bearer token for authentication.
                    timeout:  Request timeout in seconds.
                """

                def __init__(
                    self,
                    base_url: str = "{self._base_url}",
                    token: Optional[str] = None,
                    timeout: float = 30.0,
                ):
                    self._base_url = base_url.rstrip("/")
                    self._token = token
                    self._timeout = timeout

                def _request(
                    self,
                    method: str,
                    url: str,
                    params: Optional[dict] = None,
                    json: Optional[dict] = None,
                ) -> dict:
                    """Execute an HTTP request and return the parsed JSON response."""
                    if params:
                        from urllib.parse import urlencode
                        url = f"{{url}}?{{urlencode({{k: v for k, v in params.items() if v is not None}})}}".format(url=url)

                    if _HAS_HTTPX:
                        headers = {{}}
                        if self._token:
                            headers["Authorization"] = f"Bearer {{self._token}}"
                        resp = _httpx.request(
                            method, url, json=json, headers=headers, timeout=self._timeout
                        )
                        try:
                            return resp.json()
                        except Exception:
                            return {{"status_code": resp.status_code, "text": resp.text}}

                    body = None
                    if json is not None:
                        import json as _json
                        body = _json.dumps(json).encode()
                    req = urllib.request.Request(url, data=body, method=method)
                    req.add_header("Content-Type", "application/json")
                    if self._token:
                        req.add_header("Authorization", f"Bearer {{self._token}}")
                    with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                        return _json.loads(resp.read())

            '''
        )

        methods = []
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for verb in ("get", "post", "put", "patch", "delete"):
                operation = path_item.get(verb)
                if operation is None:
                    continue
                try:
                    method_code = _generate_method(verb, path, operation)
                    methods.append(method_code)
                except Exception as exc:
                    logger.warning("Skipping %s %s: %s", verb.upper(), path, exc)

        # Indent methods inside the class body
        method_block = "\n\n".join(methods) if methods else "    pass"
        return header + method_block + "\n"

    def write(self, code: str, output_dir: str = "./sdk") -> Path:
        """Write generated *code* to ``{output_dir}/client.py``.

        Args:
            code:       Generated Python source code.
            output_dir: Output directory.

        Returns:
            Path to the written file.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        client_path = out / "client.py"
        client_path.write_text(code, encoding="utf-8")
        logger.info("SDK written to %s", client_path)
        return client_path


# ---------------------------------------------------------------------------
# CLI entry point (called from castor/cli.py)
# ---------------------------------------------------------------------------


def cmd_generate_sdk(args) -> None:
    """CLI handler for ``castor generate-sdk``.

    Args:
        args: Parsed argparse namespace with ``lang``, ``output``, ``gateway`` attrs.
    """
    lang = getattr(args, "lang", "python")
    output = getattr(args, "output", "sdk/")
    gateway = getattr(args, "gateway", "http://localhost:8000")

    if lang != "python":
        logger.error("Only 'python' language is supported currently (got %r)", lang)
        return

    gen = SDKGenerator(base_url=gateway)
    try:
        code = gen.generate()
        out_path = gen.write(code, output_dir=output)
        print(f"SDK generated: {out_path}")
    except RuntimeError as exc:
        logger.error("SDK generation failed: %s", exc)
        raise SystemExit(1) from exc
