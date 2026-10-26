"""Deploy the public demo to a Hugging Face Space (Docker SDK).

    uvx --from huggingface_hub hf auth login            # once, with a Write token
    uv run --with huggingface_hub python deploy/huggingface/deploy.py <user>/<space-name>

Uploads the committed tree (git archive HEAD, so local .env files and data never leave the machine)
with this folder's Dockerfile and README at the Space root, sets the secrets, and Hugging Face
builds the image. Prompts for the demo's LLM API key unless --keep-secrets is given.
"""

import argparse
import getpass
import secrets
import subprocess
import tarfile
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
INCLUDE = ["backend", "web", "eval/corpus", ".dockerignore"]


def stage(dest: Path) -> None:
    archive = dest / "src.tar"
    subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", "-o", str(archive), "HEAD", *INCLUDE], check=True)
    with tarfile.open(archive) as tar:
        tar.extractall(dest, filter="data")
    archive.unlink()
    for tests in dest.glob("backend/tests"):
        subprocess.run(["rm", "-rf", str(tests)], check=True)
    (dest / "Dockerfile").write_text((HERE.parent / "Dockerfile.demo").read_text())
    (dest / "README.md").write_text((HERE / "README.md").read_text())


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("space", help="e.g. yourname/rag-knowledge-assistant")
    p.add_argument("--keep-secrets", action="store_true", help="don't prompt for or reset secrets")
    args = p.parse_args()

    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", *INCLUDE], capture_output=True, text=True).stdout
    if dirty.strip():
        print("Note: uncommitted changes are NOT deployed (only HEAD is uploaded):\n" + dirty)

    api = HfApi()
    print(f"Signed in to Hugging Face as {api.whoami()['name']}")
    api.create_repo(args.space, repo_type="space", space_sdk="docker", exist_ok=True)

    if not args.keep_secrets:
        key = getpass.getpass("LLM API key for the demo (OpenRouter key with a daily limit; input hidden): ").strip()
        if not key:
            raise SystemExit("No key given; nothing deployed.")
        api.add_space_secret(args.space, "OPENAI_API_KEY", key)
        api.add_space_secret(args.space, "JWT_SECRET", secrets.token_urlsafe(48))
        print("Secrets set: OPENAI_API_KEY, JWT_SECRET")

    with tempfile.TemporaryDirectory() as tmp:
        stage(Path(tmp))
        api.upload_folder(
            repo_id=args.space,
            repo_type="space",
            folder_path=tmp,
            commit_message=f"Deploy {subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()}",
            delete_patterns=["*"],  # the Space mirrors the repo exactly; files removed upstream go too
        )
    owner, name = args.space.split("/")
    print(f"Uploaded. Hugging Face is building the image (about 10 minutes):\n  https://huggingface.co/spaces/{args.space}")
    print(f"  App URL once running: https://{owner.lower()}-{name.lower().replace('_', '-')}.hf.space")


if __name__ == "__main__":
    main()
