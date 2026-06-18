"""Build and load fresh local images for the editable JupyterHub stack."""

from __future__ import annotations

import argparse
import subprocess

from local_image_loader import load_images_for_current_context


def main() -> None:
    args = _parse_args()
    images = _images(args.tag)
    build_args = ["--no-cache"] if args.no_cache else []
    contexts = _contexts(images)
    for image, spec in contexts.items():
        command = ["docker", "build", *build_args, "-t", image]
        if spec.dockerfile:
            command.extend(["-f", spec.dockerfile])
        command.append(spec.context)
        subprocess.run(command, check=True)
    load_images_for_current_context(list(contexts), args.kind_cluster)
    print(f"Prepared local JupyterHub stack images with tag {args.tag}")


def _images(tag: str) -> dict[str, str]:
    return {
        "app": f"goblin-king:{tag}",
        "admin": f"goblin-king-admin-ui:{tag}",
        "repository_ui": f"goblin-king-repository-ui:{tag}",
        "notebook_runner": f"goblin-king-notebook-python-function:{tag}",
        "notebook_service_runner": f"goblin-king-notebook-asgi-service:{tag}",
        "long_hello": f"goblin-king-example-long-hello:{tag}",
    }


class ImageBuildSpec:
    def __init__(self, context: str, dockerfile: str | None = None) -> None:
        self.context = context
        self.dockerfile = dockerfile


def _contexts(images: dict[str, str]) -> dict[str, ImageBuildSpec]:
    return {
        images["app"]: ImageBuildSpec("."),
        images["admin"]: ImageBuildSpec("admin-ui"),
        images["repository_ui"]: ImageBuildSpec(".", "repository-ui/Dockerfile"),
        images["notebook_runner"]: ImageBuildSpec("workers/notebook.python-function"),
        images["notebook_service_runner"]: ImageBuildSpec("workers/notebook.asgi-service"),
        images["long_hello"]: ImageBuildSpec("workers/example.long-hello"),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--kind-cluster", default="kind")
    parser.add_argument("--no-cache", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    main()
