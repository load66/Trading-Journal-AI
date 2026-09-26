from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_render_blueprint_is_free_stateless_and_fail_closed():
    content = read("render.yaml")

    assert "type: web" in content
    assert "runtime: python" in content
    assert "plan: free" in content
    assert "rootDir: backend" in content
    assert "healthCheckPath: /" in content
    assert "uvicorn main:app --host 0.0.0.0 --port $PORT" in content

    for pair in (
        ("APP_ENV", "production"),
        ("AUTH_MODE", "supabase"),
        ("DATABASE_MODE", "turso"),
        ("STORAGE_MODE", "supabase"),
    ):
        key, value = pair
        assert f"key: {key}" in content
        assert f"value: {value}" in content

    for secret in (
        "AUTHORIZED_USER_ID",
        "SUPABASE_URL",
        "SUPABASE_SECRET_KEY",
        "TURSO_DATABASE_URL",
        "TURSO_AUTH_TOKEN",
    ):
        block = content.split(f"key: {secret}", 1)[1].split("- key:", 1)[0]
        assert "sync: false" in block


def test_pages_workflow_uses_project_path_and_only_public_browser_config():
    content = read(".github/workflows/deploy-pages.yml")

    assert "PUBLIC_URL: /Trading-Journal-AI" in content
    assert 'REACT_APP_AUTH_ENABLED: "true"' in content
    assert "vars.REACT_APP_API_URL" in content
    assert "vars.REACT_APP_SUPABASE_URL" in content
    assert "vars.REACT_APP_SUPABASE_PUBLISHABLE_KEY" in content

    assert "actions/configure-pages@v5" in content
    assert "actions/upload-pages-artifact@v4" in content
    assert "actions/deploy-pages@v4" in content
    assert "pages: write" in content
    assert "id-token: write" in content

    for forbidden in (
        "SUPABASE_SECRET_KEY",
        "TURSO_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "APCA_API_SECRET_KEY",
    ):
        assert forbidden not in content
