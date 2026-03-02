import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SecretsArtifactHygieneTests(unittest.TestCase):
    def test_gitignore_blocks_env_and_local_db_artifacts(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn(".env", gitignore)
        self.assertIn(".env.*", gitignore)
        self.assertIn("data/*.db", gitignore)
        self.assertIn("data/*.sqlite*", gitignore)
        self.assertIn("!**/.env.example", gitignore)

    def test_dockerignore_blocks_env_and_local_db_artifacts(self):
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

        self.assertIn(".env", dockerignore)
        self.assertIn(".env.*", dockerignore)
        self.assertIn("data/*.db", dockerignore)
        self.assertIn("data/*.sqlite*", dockerignore)
        self.assertIn("!**/.env.example", dockerignore)

    def test_repository_env_template_exists(self):
        env_example = ROOT / ".env.example"
        self.assertTrue(env_example.exists())
        content = env_example.read_text(encoding="utf-8")
        self.assertIn("DATABASE_URL=", content)
        self.assertIn("DATABASE_USE_SQLITE=false", content)
        self.assertIn("JWT_SECRET_KEY=change_me", content)
        self.assertIn("BOT_TOKEN=change_me", content)


if __name__ == "__main__":
    unittest.main()
