from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "goblins"

SAMPLES = {
    "hello-dotnet": ["Dockerfile", "README.md", "HelloDotnet.csproj", "Program.cs"],
    "hello-go": ["Dockerfile", "README.md", "main.go"],
    "hello-java": ["Dockerfile", "README.md", "HelloGoblin.java"],
    "hello-node": ["Dockerfile", "README.md", "worker.mjs"],
    "hello-php": ["Dockerfile", "README.md", "worker.php"],
    "hello-python": ["Dockerfile", "README.md", "worker.py"],
    "hello-ruby": ["Dockerfile", "README.md", "worker.rb"],
    "hello-rust": ["Dockerfile", "README.md", "Cargo.toml", "src/main.rs"],
    "hello-shell": ["Dockerfile", "README.md", "worker.sh"],
}


def test_cross_language_samples_have_contract_files() -> None:
    for sample, expected_files in SAMPLES.items():
        sample_dir = EXAMPLES / sample
        assert sample_dir.is_dir(), f"{sample} directory is missing"
        for relative_file in expected_files:
            assert (sample_dir / relative_file).is_file(), f"{sample}/{relative_file} is missing"


def test_cross_language_samples_use_container_contract() -> None:
    required_terms = {
        "GOBLIN_INPUT_PATH",
        "GOBLIN_CONTEXT_PATH",
        "GOBLIN_RESULT_PATH",
        "Hello World",
        "status",
        "success",
    }
    for sample in SAMPLES:
        source_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (EXAMPLES / sample).rglob("*")
            if path.is_file() and path.name != "README.md"
        )
        for term in required_terms:
            assert term in source_text, f"{sample} does not reference {term}"
        assert "goblin_king" not in source_text, f"{sample} imports Goblin King internals"


def test_cross_language_readmes_include_build_command() -> None:
    for sample in SAMPLES:
        readme = (EXAMPLES / sample / "README.md").read_text(encoding="utf-8")
        assert "docker build" in readme
        assert "goblin-example-" in readme
