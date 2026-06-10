from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "goblins"

SAMPLES = {
    "wasi-rust-hello": ["Dockerfile", "README.md", "Cargo.toml", "run-wasi.sh", "src/main.rs"],
    "wasi-c-hello": ["Dockerfile", "README.md", "worker.c", "run-wasi.sh"],
}


def test_wasi_samples_are_container_wrapped() -> None:
    for sample, expected_files in SAMPLES.items():
        sample_dir = EXAMPLES / sample
        assert sample_dir.is_dir(), f"{sample} directory is missing"
        for relative_file in expected_files:
            assert (sample_dir / relative_file).is_file(), f"{sample}/{relative_file} is missing"
        dockerfile = (sample_dir / "Dockerfile").read_text(encoding="utf-8")
        assert "wasmtime" in dockerfile.lower()
        assert "worker.wasm" in dockerfile


def test_wasi_wrappers_pass_contract_environment() -> None:
    required_terms = {
        "GOBLIN_INPUT_PATH",
        "GOBLIN_CONTEXT_PATH",
        "GOBLIN_RESULT_PATH",
        "GOBLIN_ARTIFACT_ROOT",
        "worker.wasm",
        "--dir /goblin",
    }
    for sample in SAMPLES:
        wrapper = (EXAMPLES / sample / "run-wasi.sh").read_text(encoding="utf-8")
        for term in required_terms:
            assert term in wrapper, f"{sample} wrapper does not reference {term}"


def test_wasi_modules_write_goblin_result_envelope() -> None:
    required_terms = {"status", "success", "artifacts", "metrics", "handoff", "error"}
    for sample in SAMPLES:
        source_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (EXAMPLES / sample).rglob("*")
            if path.is_file() and path.name != "README.md"
        )
        for term in required_terms:
            assert term in source_text, f"{sample} does not reference {term}"
        assert "goblin_king" not in source_text, f"{sample} imports Goblin King internals"
