from pathlib import Path
import subprocess


def test_docker_entrypoint_shell_contract() -> None:
    root = Path(__file__).resolve().parent.parent
    subprocess.run(
        [
            "sh",
            str(root / "tests" / "test_docker_entrypoint.sh"),
            str(root / "bot" / "docker-entrypoint.sh"),
        ],
        check=True,
    )
