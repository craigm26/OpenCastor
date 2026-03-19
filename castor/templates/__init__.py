"""Template loader for OpenCastor systemd service files and config templates."""

import importlib.resources as _res
from pathlib import Path


def load_template(name: str) -> str:
    """Load a template file from castor/templates/.

    Args:
        name: Template filename relative to castor/templates/
              (e.g. 'systemd/opencastor-attestation.service.tmpl')

    Returns:
        Template content as a string.

    Raises:
        FileNotFoundError: if template does not exist.
    """
    # Try importlib.resources first (works when installed via pip)
    try:
        parts = name.replace("\\", "/").split("/")
        subpkg = "castor.templates." + ".".join(parts[:-1]) if len(parts) > 1 else "castor.templates"
        filename = parts[-1]
        ref = _res.files(subpkg).joinpath(filename)
        return ref.read_text(encoding="utf-8")
    except Exception:
        pass
    # Fallback: relative to this file
    here = Path(__file__).parent
    path = here / Path(name)
    if path.exists():
        return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Template not found: {name}")
