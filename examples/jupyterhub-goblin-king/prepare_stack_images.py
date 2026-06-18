"""Build and load fresh local images for the editable JupyterHub stack."""

from __future__ import annotations

import argparse
import subprocess

from local_image_loader import load_images_for_current_context


def main() -> None:
    args = _parse_args()
    images = _images(args.tag)
    build_args = ["--no-cache"] if args.no_cache else []
    for image, context in _contexts(images).items():
        subprocess.run(["docker", "build", *build_args, "-t", image, context], check=True)
    load_images_for_current_context(list(_contexts(images)), args.kind_cluster)
    print(f"Prepared local JupyterHub stack images with tag {args.tag}")


def _images(tag: str) -> dict[str, str]:
    return {
        "app": f"goblin-king:{tag}",
        "admin": f"goblin-king-admin-ui:{tag}",
        "notebook_runner": f"goblin-king-notebook-python-function:{tag}",
        "notebook_service_runner": f"goblin-king-notebook-asgi-service:{tag}",
        "long_hello": f"goblin-king-example-long-hello:{tag}",
    }


def _contexts(images: dict[str, str]) -> dict[str, str]:
    return {
        images["app"]: ".",
        images["admin"]: "admin-ui",
        images["notebook_runner"]: "workers/notebook.python-function",
        images["notebook_service_runner"]: "workers/notebook.asgi-service",
        images["long_hello"]: "workers/example.long-hello",
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--kind-cluster", default="kind")
    parser.add_argument("--no-cache", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    main()
