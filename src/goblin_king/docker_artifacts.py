"""Translate container artifact paths into validated local download locations."""

from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from goblin_king.contracts import GoblinResult


def normalize_docker_artifacts(
    result: GoblinResult, root: Path, container_root: str
) -> GoblinResult:
    """Retain only regular files beneath this job's artifact directory."""
    retained = []
    root = root.resolve(strict=True)
    for artifact in result.artifacts:
        parsed = urlsplit(artifact.uri)
        if parsed.scheme not in {"", "file"} or parsed.netloc not in {"", "localhost"}:
            raise ValueError(f"artifact {artifact.name!r} must use a local file path")
        if parsed.query or parsed.fragment:
            raise ValueError(f"artifact {artifact.name!r} has an invalid file path")
        raw = unquote(parsed.path) if parsed.scheme == "file" else artifact.uri
        path = PurePosixPath(raw)
        if "\\" in raw or ":" in raw or ".." in path.parts:
            raise ValueError(f"artifact {artifact.name!r} escapes its artifact directory")
        if path.is_absolute():
            try:
                path = path.relative_to(PurePosixPath(container_root))
            except ValueError as error:
                raise ValueError(
                    f"artifact {artifact.name!r} escapes its artifact directory"
                ) from error
        candidate = root
        for part in path.parts:
            candidate /= part
            if candidate.is_symlink():
                raise ValueError(f"artifact {artifact.name!r} may not use symbolic links")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError(f"artifact {artifact.name!r} is not a retained regular file")
        retained.append(artifact.model_copy(update={"uri": resolved.as_uri()}))
    return result.model_copy(update={"artifacts": retained})
